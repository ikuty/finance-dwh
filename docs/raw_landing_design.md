# raw / landing 層の設計（DuckDB、2026-09-11）

レイクのファイルツリーを DuckDB で読み、dbt の raw モデル・source として扱う。
2026-09-10 に PostgreSQL `file_fdw` で実装したが、実機検証で致命的に遅いことが判明し
（下記「移行前の実測」）、DuckDB + Parquet に全面移行した。

## 方式が2つある理由

DuckDB はファイルを直接読める（サーバなし）ので、単純にはレイクをそのまま SQL の
`FROM` に指定したい。実際そうしたのは **JSON（書類一覧）だけ**で、**CSV（明細）は
別処理**にした。

### `raw__edinet_document_index`（JSON）: DuckDB の直接読みで十分

```sql
select unnest(results, recursive := true)
from read_json_auto('/lake/edinet-dl/raw/response/*/*/*/document_list.json', filename := true)
```

JSON は構造が素直で、DuckDB のネイティブ JSON リーダーがそのまま `unnest()` できる。
書類一覧の全量（983ファイル）でも高速。中間ステップ不要、dbt の view モデル
（`models/raw/raw__edinet_document_index.sql`）1本で完結する。file_date はファイル名
ではなくパス階層（`response/{yyyy}/{mm}/{dd}/document_list.json`）にあるので、
`regexp_extract(filename, ...)` で組み立てる。全列 `varchar` にキャストし、raw 層の
「生データに忠実」を踏襲する（型付けは cleansed）。

### EDINET CSV 明細: DuckDB に CSV を読ませられない（実機で確認した不具合）

**DuckDB 自身の CSV パーサーは、EDINET のテキストブロック要素（引用符で囲われた
フィールドが数万文字に及ぶ）で解析エラーになる**。

```
Invalid Input Error: CSV Error on Line: 5464
Original Line: "2026-09-01","E30020","S100YZUT","jpcrp_cor:ParticularsOfNewShare...
```

再現条件は Python の `csv` モジュールでは正常にパースできる正当な CSV（1 フィールド
20,000〜30,000 文字、埋め込み改行なし）で、DuckDB 側の内部バッファ/行長制限に起因すると
みられる。EDINET の有価証券報告書は長文の定性記述（新株予約権の内容等）を1セルに
収めるため、この種の長大フィールドが日常的に発生する。

対策: **CSV を DuckDB に一切読ませない**。既にテスト済みの `edinet_csv_fdw.py`
（stdlib `csv` モジュール、EDINET CSV の BOM付きUTF-16LE・CRLF・タブ区切り・全列
クォートを正しく解釈する）でパースし、**行を列ごとのリストに集約 → pyarrow Table →
DuckDB `COPY ... TO ... (FORMAT PARQUET)`** で Parquet に変換する（下記「landing」）。

副産物として、この経路は `executemany`（1行ずつ SQL 実行、6,732行で4秒）より
**40〜50倍速い**（列指向集約 + pyarrow で実測 15〜20万行/秒）。

## landing（edinet_csv_facts）

```
edinet_csv_fdw.py（Python, stdlib csv） --date YYYY-MM-DD
  glob を raw/{yyyy}/{mm}/{dd}/ に限定（1日ぶんだけ、数秒）
  ↓ 列ごとに集約（_ColumnCollector）
  ↓ pyarrow.table(...)
  ↓ DuckDB: COPY (SELECT * FROM tbl) TO '...part.parquet.tmp' (FORMAT PARQUET)
  ↓ rename で part.parquet へアトミックに置換
{DATA_DIR}/landing/edinet_csv_facts/file_date=YYYY-MM-DD/part.parquet
```

`transform/flows/load_edinet.py`（Prefect task、dbt の外）が実行する。

- **取り込み対象日** = 「直近 `LANDING_LOOKBACK_DAYS`（既定7）日」∪「レイクに日付
  ディレクトリがあるが landing にまだ無い日」。前者は edinet-dl の `DAYS_WINDOW` に
  よる遡及取得、後者は過去バックフィルを拾う。判定はディレクトリの有無だけを見る
  （`disk_dates()` / `landing_logged_dates()`、ファイルの中身は見ない）。
- **冪等性**: 1 日ぶんは常に丸ごと書き直す（`part.parquet` の存在＝取り込み済みの印）。
  0 行の日（休日等）でも空の Parquet を書くので、翌日以降は再走査されない。
- **dbt からの参照**: dbt-duckdb の `source` + `meta.external_location`
  （`models/cleansed/_cleansed__sources.yml`）で `read_parquet(glob, hive_partitioning=false)`
  を直接 source として使う。

### DuckDB の Hive パーティショニング自動検出（実機で踏んだ落とし穴）

`file_date=YYYY-MM-DD` というディレクトリ名を使っているため、**DuckDB は
`read_parquet()` で単一ファイルを指定しても Hive パーティショニングを自動検出し、
ファイル内の `file_date` 列（VARCHAR で書いている）を DATE 型の値で上書きして返す**。
書き込み自体は常に正しい（pyarrow で直接読めば VARCHAR のまま）。読み取り側の挙動。

対処: 生の文字列が必要な読み取りは `hive_partitioning=false` を明示する。逆に
DATE 型が欲しければ Hive 推論に任せてもよい（今回の cleansed モデルは
`cast(file_date as date)` で明示的に型付けしているので実害は無いが、意図せず
発火することを知っておく必要がある）。

## cleansed（型付け・名寄せ、Parquet 出力）

dbt-duckdb の `materialized='external'`（`location=<path>.parquet`, `format='parquet'`）
で Parquet へ直接書く。Postgres 版にあった **incremental（delete+insert・
`_landing_loaded_at` 透かし・既存行の再結合）は廃止し、常に全量 rebuild にした**。
理由: DuckDB は landing の全量読み込み自体が速く（後述の実測）、複雑な仕組みを
維持するコストに見合わなくなったため。実データ規模（2000万行超）で遅くなったら、
landing と同じ「対象日だけ書き直す」パーティション単位の再構築に戻す。

- `cleansed__edinet__documents`: `raw__edinet_document_index` から型付け・上場会社
  （`sec_code` あり）のみに絞る。同じ docID が複数日の `document_list.json` に
  出現しうる（後日メタデータが編集され再掲載されるため）ので、docID ごとに最新の
  掲載（`file_date` → `ope_date_time`）だけ残す。
- `cleansed__edinet__facts`: `landing.edinet_csv_facts` を `cleansed__edinet__documents`
  と結合して上場会社ぶんに絞り、連結/個別マッピング（CSVの「連結・個別」列をそのまま
  使う）・数値化（`try_cast(... as decimal(38,4))`、失敗は NULL）・名寄せ
  （`(edinet_code, element_id, context_id, consolidation)` で最新提出のみ）を行う。

## 移行前の実測（PostgreSQL + file_fdw、2026-09-11、Mac Mini 2012、レイク全量）

`file_fdw` は述語プッシュダウン不可で、`SELECT`（`count(*)` 含む）のたびにレイク
全体を再走査していた。

| クエリ | 結果 |
|---|---|
| `raw__jpx_file_catalog` の `count(*)`（`stat()` のみ） | 9,961 行 / 約1秒 |
| `raw__edinet_document_index` の `count(*)`（983 JSON） | 162,701 行 / 約11秒 |
| `raw__edinet_csv_facts` の `count(*)`（82,631 gzip） | 20,572,522 行 / **約12分** |
| landing（Postgres 版）初回投入 | 20.5M行 / 35.5分 |
| `cleansed__edinet__facts` 初回 incremental ビルド | 9.2M行 / **1,807秒（約30分）**（HDD 上の外部ソート） |
| dedup の singular test（9.2M行フルスキャン） | 275秒 |

DuckDB + Parquet 移行後、同じデータ（ローカルサンプル規模）で `dbt build` 全体が
0.2〜8秒（コンテナ起動込み）。実スケールでの数値は Mac Mini 反映後に確認する。

## 既知の論点・残作業

- **SIGPIPE**: `edinet_csv_fdw.py` は `LIMIT` 等で下流が早期にパイプを閉じたとき
  `BrokenPipeError` で終了コード 120 になる不具合が file_fdw 時代にあった。
  `main()` 冒頭で `signal.signal(signal.SIGPIPE, SIG_DFL)` して対処済み（landing の
  load はパイプを使わないため実害は無いが、ラッパー単体を手で叩くときのために残す）。
- **jpx**: 未移行。Postgres 版の `jpx_catalog_fdw.py`（ファイル目録のみ）は削除済み
  （git 履歴に残る）。再着手時は DuckDB の `glob()` を使えば大幅に簡潔になる見込み。
- **doc index の landing 化**: 現状 11秒で十分速いので不要。将来遅くなったら
  edinet_csv_facts と同じ方式に寄せる。

## テスト

- `transform/tests/test_edinet_csv_fdw.py`: CSV 抽出ロジック（合成フィクスチャ）。
- `transform/tests/test_load_edinet.py`: landing への書き込み（`disk_dates` /
  `landing_logged_dates` / `dates_to_load` は純粋関数、`load_one` は実 DuckDB で
  end-to-end。長大フィールド（3万文字）の往復・空日・破損ファイル耐性を検証）。
- dbt 側: `dbt build` を実データ（レイクのサンプル）に対して実行し、Postgres 版と
  完全一致する行数・値を確認済み（documents 93 / facts 10,470 / consolidation 内訳等）。
