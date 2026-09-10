# raw 層（file_fdw）の設計（2026-09-10）

レイクのファイルツリーを PostgreSQL の外部テーブルとして読み、dbt source として扱う。

## 方式: `file_fdw` + stdlib Python ラッパー

`postgres` 公式イメージに同梱の `file_fdw` を使う。外部テーブルの `program` オプションから
Python ラッパー（`postgres/fdw/*.py`、標準ライブラリのみ）を起動し、レイクのツリーを走査して
CSV を stdout に吐かせる。ラッパーはパスから provenance 列（file_date 等）を前置する。

```sql
CREATE SERVER lake_files FOREIGN DATA WRAPPER file_fdw;
CREATE FOREIGN TABLE raw.raw__edinet_csv_facts (... 全列 text ...)
  SERVER lake_files
  OPTIONS (program 'python3 /opt/fdw/edinet_csv_fdw.py /lake', format 'csv');
```

- `/lake` は docker compose がレイクを bind mount する固定パス。
- ラッパーの起動主体はコンテナ内の `postgres` ユーザー。公式イメージに `python3` が無いため
  `postgres/Dockerfile` で `apt-get install python3` する（pip 依存なし）。
- 出力は全フィールドをクォートした CSV（値に改行・タブが入りうる EDINET のテキストブロックに
  対応するため）。外部テーブルは `format 'csv'`。

### 検討したが不採用: multicorn2

Python 製 FDW の `multicorn2` は述語プッシュダウンができるが、PostgreSQL 拡張のビルドが
必要で依存が増える。EDINET CSV(type=5) は全書類が同一 9 列スキーマなので、`file_fdw` +
薄いラッパーで十分表現でき、追加拡張なしで済む。性能が問題化したとき初めて再検討する。

## 3 つの外部テーブルとラッパー

### `raw__edinet_csv_facts` ← `edinet_csv_fdw.py`

- 走査対象: `/lake/edinet-dl/raw/{yyyy}/{mm}/{dd}/{edinetCode}/csv/{docID}/*.csv.gz`
  （file_date は `{yyyy}-{mm}-{dd}` として再構成する）
- 各 `.csv.gz`: **BOM 付き UTF-16LE・CRLF・タブ区切り・全フィールドをダブルクォート・固定 9 列**
  （要素ID / 項目名 / コンテキストID / 相対年度 / 連結・個別 / 期間・時点 / ユニットID / 単位 / 値）。
- 値にテキストブロック（改行入りの長文）が入りうるので `csv` モジュールでパースし、
  provenance 3 列（file_date / edinet_code / doc_id）を前置して `csv.writer` で再出力する
  （行単位のテキスト連結はしない）。
- 破損ファイルは stderr に警告して読み飛ばし、走査全体は止めない。

### `raw__edinet_document_index` ← `edinet_docindex_fdw.py`

- 走査対象: `/lake/edinet-dl/raw/response/{yyyy}/{mm}/{dd}/document_list.json`
  （ファイル名は常に `document_list.json`。file_date はパス階層 `{yyyy}/{mm}/{dd}` から）
- `{"metadata":..., "results":[ {...29 キー固定...} ]}` の `results[]` を 1 行ずつ、
  キー順に snake_case 列へ。null は空文字。

### `raw__jpx_file_catalog` ← `jpx_catalog_fdw.py`

- 走査対象（形式ごとにトップレベル分離、詳細は finance-lake の
  `jpx-daily-pdf-dl/docs/file_download_design.md`）:
  ```
  /lake/jpx-daily-pdf-dl/raw/legacy-daily/{yyyy}/{mm}/{dd}/stq.pdf|stq.tif
  /lake/jpx-daily-pdf-dl/raw/monthly-ohlc/{yyyy}/{mm}/stq_monthly.pdf
  /lake/jpx-daily-pdf-dl/raw/detailed-daily/{yyyy}/{mm}/{dd}/stq.pdf
  ```
- 出力は **ファイル目録のみ**: format / granularity / period（日次=YYYY-MM-DD、月次=YYYY-MM）/
  file_kind（pdf/tif）/ relative_path / byte_size / modified_at。`path.stat()` するだけで
  PDF は開かない。
- **相場の数値は含まない**。PDF/TIFF の内容抽出（`pdftotext -layout` + 正規表現、TIFF は
  OCR）は後段の責務だが今回のスコープ外。必要になったら Prefect フローに load タスクを足し、
  差分を native テーブル `raw.jpx_prices` 等へ COPY してから cleansed/mart を載せる。

## 既知の制約

- **述語プッシュダウン不可**: `file_fdw` の `program` はクエリの条件を受け取れないため、
  `SELECT`（`count(*)` 含む）のたびにレイク全体を再走査する（インクリメンタル不可、
  `WHERE` も効かない）。
  - **実測（2026-09-11、Mac Mini 2012 上、レイク全量）**:
    - `raw__jpx_file_catalog` の `count(*)` … 9,961 行 / **約 1 秒**（`stat()` のみ）
    - `raw__edinet_document_index` の `count(*)` … 162,701 行 / **約 11 秒**（983 JSON）
    - `raw__edinet_csv_facts` の `count(*)` … 20,572,522 行 / **約 12 分**
      （82,631 個の `.csv.gz` を毎回解凍・パース。レイクは日々増える）
  - **対応（実装済み、edinet_csv_facts）**: 下記「landing 経由の日付単位 load」。
  - 日次フローの実行レポートは FDW を直接数えない（`raw__edinet_csv_facts` は約12分、
    `raw__edinet_document_index` も約11秒）。cleansed の native テーブルと jpx カタログを数える。

## landing 経由の日付単位 load（edinet_csv_facts、2026-09-11）

FDW を「日付パラメータ付きの抽出専用」に格下げし、native の中間テーブルを挟む。

```
edinet_csv_fdw.py --date YYYY-MM-DD          # glob を raw/{yyyy}/{mm}/{dd}/ に限定（数秒）
  ↓  Prefect: flows/load_edinet.py（dbt の外）
landing.edinet_csv_facts                     # native・全列 text・file_date インデックス
  + landing.edinet_csv_facts_load_log        # file_date PK / row_count / loaded_at
  ↓  dbt
cleansed.cleansed__edinet__facts             # incremental（delete+insert）
```

- **取り込み対象日** = 「直近 `LANDING_LOOKBACK_DAYS`（既定7）日」∪「レイクに日付
  ディレクトリがあるが `_load_log` に無い日」。前者は edinet-dl の `DAYS_WINDOW` による
  遡及取得、後者は過去バックフィルを拾う。
- **冪等性**: 1 日ぶんを `BEGIN; DELETE WHERE file_date=X; COPY; upsert _load_log; COMMIT`。
- **incremental の駆動**: `cleansed__edinet__facts` に `_landing_loaded_at`（各行の
  出所日の `_load_log.loaded_at`）を持たせ、`loaded_at > max(_landing_loaded_at)` の
  日だけ処理。ルックバック窓ではなく透かしなので、古い日を後からバックフィルしても拾える。
- **名寄せの正しさ**: 対象日のデータポイント・キーに一致する既存（`{{ this }}`）行も
  combined に混ぜてから最新提出だけ残す。過去の古い提出が来ても既存の新しい提出が勝つ。
- **既知の非効率**: 対象日の行が全部フィルタで落ちる日（上場会社の CSV が無く大量保有
  だけ、等）は透かしが進まず毎回再スキャンされる。native の index scan で数ミリ秒なので許容。
- **document_index / jpx** は 1〜11 秒なので当面この方式にはしない（将来の候補）。
- **SIGPIPE**: 下流（`file_fdw` / `| head`）が `LIMIT` 等で読み切らずにパイプを閉じると
  Python が `BrokenPipeError` で終了コード 120 になる。各ラッパーは `main()` 冒頭で
  `signal.signal(signal.SIGPIPE, SIG_DFL)` して素直に終了させる（実機で確認済みの不具合）。
- **マウント権限**: bind mount はホストの所有者/パーミッションを保つ。コンテナ内 `postgres`
  ユーザー（uid 999）がレイクを読めるよう、ホスト側は other に読み+traverse 権限が必要。

## テスト

- `postgres/fdw/tests/` に各ラッパーの pytest（合成フィクスチャ → 期待 CSV）。`mypy --strict`。
- 実機相当の確認: `docker compose up -d postgres` 後に
  `select count(*) from raw.raw__edinet_csv_facts` 等がサンプル日の値を返すこと。
