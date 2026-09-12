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

## JPX 形式C（株式相場表・詳細日次）: PDF のテキスト化＋構造化（2026-09-13）

JPX（`services/jpx-daily-pdf-dl`）が取得する形式C（直近13ヶ月ローリングの日次
PDF、`detailed-daily/{yyyy}/{mm}/{dd}/stq.pdf`）から、セクション1（立会市場
普通取引）の銘柄別明細を構造化する。EDINET と同じ「レイク層は生データの解釈を
持たない」方針に従い、PDF のテキスト化・構造化は finance-dwh 側（landing）で
行う（判断の経緯はセッション履歴参照。要旨: PDF のテキスト化は不可逆な変換で
あり「解釈」の領域に入るため、レイクではなく landing に置く）。

### 単純なテキスト抽出では列順が保持されない（実機データで確認した重要な事実）

`pypdf` 等で素朴にページのテキストを抽出すると、**視覚上の列順と実際の抽出順が
一致しない**（PDF 生成時の描画順序に依存するため）。実データで確認した例:

```
期待する順序: 前場OHLC → 後場OHLC → 最終気配 → 前日比 → VWAP → 売買高 → 売買代金
実際の抽出順: 後場OHLC → 前日比 → VWAP → 前場OHLC → 売買高 → 売買代金+単位(連結) → 最終気配
```

このため、**座標（`x0`）とフォントサイズを保持した抽出**（`pdfplumber` の
`extract_words()`）が必須と判断した。

### 3段構成

```
jpx-daily-pdf-dl/raw/detailed-daily/{yyyy}/{mm}/{dd}/stq.pdf   （レイク、変更なし）
  ↓ Stage1: jpx_stq_pdf.iter_words()  … PDF→座標付き単語データ、機械的変換のみ
{DATA_DIR}/landing/jpx_stq_words/file_date=YYYY-MM-DD/part.parquet
  ↓ Stage2: jpx_stq_facts.run()      … セクション1の判定・列の意味づけ・見出し継承
{DATA_DIR}/landing/jpx_stq_facts/file_date=YYYY-MM-DD/part.parquet
  ↓ dbt: cleansed__jpx__stq_prices.sql（型付けのみ、external materialization）
{DATA_DIR}/cleansed/jpx_stq_prices.parquet
```

`transform/flows/load_jpx_stq.py`（`load_edinet.py` と同じ設計）が両方の
Parquet を日付単位・アトミック書き込みで生成する。Stage1 を分けている理由は
Stage2（セクション境界判定・列の意味づけ等、今後何度も変わる想定）を直すたびに
Stage1（PDF 抽出、実測 約36秒/日）を再実行しなくて済むようにするため。

- **列境界**: セクション1データ行の実測 `x0` の中点から算出した定数
  （`jpx_stq_facts._COLUMN_BOUNDS`）。ヘッダーラベルの `x0` と実データの `x0` が
  微妙にずれる（例: 「銘柄名」ラベルは `x0=203` だが実データの「単位+銘柄名」は
  `x0=140.7`）ため、ハードコードした定数を使う（ヘッダーからの動的算出は
  この理由で採用しなかった）。
- **見出し行の判定**: フォントサイズ（見出し10pt・本文6pt）で判別。ただし
  「見出しのように見えるが違う行」が複数あり、実機データで発見して除外した:
  1. **繰り返し列ヘッダー行**（各ページ先頭の「始値/高値/安値/終値」等）
  2. **午前/午後セッションラベル行**
  3. **日付行**（「2026年9月10日(木曜日)」）— 全レコードの78%を汚染する最も
     深刻なバグだった。市場区分の判定を「"市場"を含むか」の部分一致にしていた
     副作用で「立会市場普通取引」等の取引種別見出しにも誤反応したため、
     既知の値（プライム市場/スタンダード市場/グロース市場/TOKYO PRO Market）
     への完全一致に変更した。
- **業種の日英分離**: 業種見出し行は日本語・英語が同一行（同じ `top`）に
  並んで出現する。単語ごとに ASCII のみかどうかで判定し、
  `industry_sector_ja` / `industry_sector_en` に分けて保持する。
- **銘柄コード**: 4〜5桁数字が大半だが、REIT・投資証券の投資口コードは
  末尾に英字1文字を含む（例: `256A`）。正規表現 `[0-9]{3,5}[A-Za-z0-9]?` で
  両方を受理する。該当銘柄は「売買単位」欄も無い（`trading_unit` が NULL）。
- **2行1レコード**: 数値行（コード・銘柄名(日)・OHLC等）の直後に英語社名だけの
  継続行が来る。直前レコードの `name_en` が未設定なら結合する。

### `_loaded_at` の設計

cleansed は EDINET と同じ理由（後述）で毎回全量 rebuild にしたため、
`created_at`/`updated_at` のような「前回ビルドとの比較」はモデル側に持たせない
（それをやるとせっかく廃止した incremental の複雑さが戻ってしまう）。代わりに
**landing の書き込み時点で `_loaded_at`（単一のタイムスタンプ）を都度 `now()` で
埋める**（`load_jpx_stq.py`、初回書き込みでも backfill 等による再書き込みでも
同じ扱い）。cleansed はこの列をそのまま透過するだけ。

### なぜ cleansed を incremental にしないか

「日毎にファイルが追加される」こと自体は incremental 化の理由にならない、と
判断した（EDINET と同じ結論）。JPX 形式Cは13ヶ月ローリングでも最大約280日分・
1日あたり約4,440行で、最大でも124万行程度（EDINET の2000万行の1/16以下）。
実測: 28日分・124,403行の型付け変換が **0.024秒**（DuckDB）。「日毎の追加に
対応する」という incremental 本来の目的は landing 側（`dates_to_load()`、
既存の `file_date` はスキップ）で既に満たされているため、cleansed 側で
複雑さを持つ理由が無い。

### 実機検証（2026-08-03〜2026-09-10、28営業日、117,673件）

| 検証内容 | 結果 |
|---|---|
| クラッシュ・例外 | 0件（28/28日で正常終了） |
| レコード数 | 4,440〜4,446件で安定（銘柄の新規上場/廃止による自然な変動） |
| 重複コード・欠損（market_segment/industry_sector/name_en）・コード形式異常 | いずれも0件 |
| OHLC整合性（前場・後場それぞれ 安値<=始値・終値<=高値） | 違反0件 |
| VWAP再計算（売買代金÷売買高との照合、相対誤差0.1%許容） | 117,673件検証、違反0件 |

VWAP再計算は特に有効な検証で、**列の取り違え（売買高⇔売買代金の混同等）を
数値レベルで検出できる**独立した検算になっている。dbt の singular テスト
（`assert_cleansed__jpx__stq_prices_vwap_reconciles.sql` /
`_ohlc_consistency.sql` / `_deduplicated.sql`）として本実装に組み込み済み。

### スコープ（今回対応した範囲）

対象はセクション1（立会市場普通取引）のみ。PDF には他に11種類のセクション
（ToSTNeT取引・立会外分売・自己株式立会外買付・外国投信等）があるが、対象件数が
少なく（1〜数ページ程度）優先度は低いため今回は対象外とした。Stage1（座標付き
単語データ）は全ページ分を保持しているため、将来対象を広げる場合も Stage1 の
再実行（PDF抽出、約36秒/日）は不要で、Stage2 の対応セクションを増やすだけで済む。

形式A（レガシー日次）・形式B（月次簡易OHLC）は未対応（今回のスコープ外）。

## 既知の論点・残作業

- **SIGPIPE**: `edinet_csv_fdw.py` は `LIMIT` 等で下流が早期にパイプを閉じたとき
  `BrokenPipeError` で終了コード 120 になる不具合が file_fdw 時代にあった。
  `main()` 冒頭で `signal.signal(signal.SIGPIPE, SIG_DFL)` して対処済み（landing の
  load はパイプを使わないため実害は無いが、ラッパー単体を手で叩くときのために残す）。
- **jpx のファイル目録（raw層）**: 形式A・形式Bを含むファイル目録の raw モデルは
  未実装（DuckDB の `glob()` を使えば大幅に簡潔になる見込み、必要になったら着手）。
- **doc index の landing 化**: 現状 11秒で十分速いので不要。将来遅くなったら
  edinet_csv_facts と同じ方式に寄せる。

## テスト

- `transform/tests/test_edinet_csv_fdw.py`: CSV 抽出ロジック（合成フィクスチャ）。
- `transform/tests/test_load_edinet.py`: landing への書き込み（`disk_dates` /
  `landing_logged_dates` / `dates_to_load` は純粋関数、`load_one` は実 DuckDB で
  end-to-end。長大フィールド（3万文字）の往復・空日・破損ファイル耐性を検証）。
- dbt 側: `dbt build` を実データ（レイクのサンプル）に対して実行し、Postgres 版と
  完全一致する行数・値を確認済み（documents 93 / facts 10,470 / consolidation 内訳等）。
- `transform/tests/test_jpx_stq_pdf.py`: Stage1（座標・フォントサイズ抽出）。
  実際の JPX PDF は利用規約上コミットできないため、自前の最小 PDF ビルダー
  （Helvetica・ASCII のみ）で合成 PDF を生成して検証する。
- `transform/tests/test_jpx_stq_facts.py`: Stage2（構造化ロジック）。実測座標を
  使った `Word` を直接組み立てて検証（見出し継承・列ヘッダー等の除外・REIT
  コード・2行1レコード結合等）。
- `transform/tests/test_load_jpx_stq.py`: landing への書き込み。`jpx_stq_pdf.iter_words`
  を monkeypatch（Stage1/Stage2 は個別に検証済みのため）し、`_loaded_at` の付与・
  アトミック書き込みを検証。
- 実データ28日分に対する Stage1/Stage2 通しの検証・dbt build（実PDF→cleansed）は
  セッション履歴参照（本ドキュメント上部の「実機検証」表に要約）。
