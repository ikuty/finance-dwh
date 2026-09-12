# finance-dwh

`finance-lake`（レイク層）の後段を担う**ウェアハウス層**のリポジトリ。raw / cleansed /
mart の 3 層からなる DWH を、同じ Mac Mini 上に **DuckDB + dbt Core（dbt-duckdb）+
Prefect + docker compose** で構築する。全体像は `docs/architecture.md`、raw/landing の
抽出詳細は `docs/raw_landing_design.md`、デプロイは `docs/deployment_design.md`。

**2026-09-11: PostgreSQL + file_fdw から DuckDB + Parquet へ全面移行した**（下記参照）。
旧 Postgres 実装は git 履歴（`afc45c7`〜`5ea9b52`）に残る。

## 固有の設計判断

- **DuckDB は組み込み型（サーバなし）**。永続化される実体はホスト上の **Parquet
  ファイル**（+ スキーマ情報だけの小さい `.duckdb` カタログファイル）。「コンテナ内に
  デーモンを置かない」方針に完全に沿う（Postgres 版で唯一の常駐例外だった
  `finance-dwh-postgres.service` は不要になり廃止した）。
- **移行の理由（実測、2026-09-11）**: Postgres + `file_fdw` は `raw__edinet_csv_facts`
  の `count(*)` が実機で **約12分**（20.5M行・82,631個の gzip・述語プッシュダウン不可）、
  `cleansed__edinet__facts` の初回 incremental ビルドが **約30分**（HDD 上の外部ソート）
  だった。同じデータを DuckDB + Parquet に移したところ、**dbt build 全体が 0.2〜8秒**
  （コンテナ起動込み）に短縮。詳細は `docs/raw_landing_design.md`。
- **raw 層は用途によって方式が違う**（無理に統一しない）:
  - `raw__edinet_document_index`（書類一覧 JSON）: DuckDB のネイティブ JSON リーダーで
    レイクを **直接 glob 読み**する dbt view モデル。中間ステップ不要（JSON は軽量）。
  - EDINET CSV 明細: DuckDB 自身の CSV パーサーが EDINET の長大なテキストブロック
    （引用符付きフィールドが数万文字）で解析エラーになることを実機で確認したため、
    **CSV を DuckDB に読ませない**。`edinet_csv_fdw.py`（Python の csv モジュール、
    テスト済み）でパースし、日付単位で Parquet（`landing.edinet_csv_facts`）へ書く。
- **landing 層**（CSV facts のみ）: `transform/flows/load_edinet.py`（Prefect task、
  dbt の外）が `{DATA_DIR}/landing/edinet_csv_facts/file_date=YYYY-MM-DD/part.parquet`
  を日付ごとに書く。対象日 = 直近 `LANDING_LOOKBACK_DAYS`(7) 日 ∪（レイクに日付
  ディレクトリがあるが landing にまだ無い日）。1 日ぶんは列指向集約 + pyarrow Table
  経由で DuckDB `COPY` → 一時ファイル→rename でアトミックに置換。実測 15〜20万行/秒。
- **cleansed 層**: dbt-duckdb の `materialized='external'`（`location=...parquet`）で
  Parquet へ直接書く。Postgres 版にあった incremental（delete+insert・透かし・既存行
  再結合）は**廃止し、常に全量 rebuild**にした。DuckDB は landing の全量読み込み自体が
  速く、複雑な仕組みを持つ理由が無くなったため（実データ規模で遅くなったら日付
  パーティション単位の再構築に戻す）。現状は `cleansed__edinet__documents`（毎回
  rebuild、上場会社のみ、docID で名寄せ）と `cleansed__edinet__facts`（同上）のみ。
  jpx・mart は用途が固まってから。
- **landing の source 宣言**: dbt-duckdb の `source` + `meta.external_location`
  （`read_parquet(...)`）で、DuckDB の `read_parquet` を直接 source として使う。
  `hive_partitioning=false` を明示すること（`file_date=YYYY-MM-DD` というディレクトリ名
  から DuckDB が Hive パーティショニングを自動検出し、ファイル内の file_date 列
  （VARCHAR で書いている）を DATE 型の値で上書きしてしまう実機確認済みの挙動がある。
  書き込み自体は常に正しい）。
- **JPX 形式C（株式相場表・詳細日次）は PDF テキスト化＋構造化まで実装済み**（2026-09-13）。
  レイクの PDF（`jpx-daily-pdf-dl/raw/detailed-daily/{yyyy}/{mm}/{dd}/stq.pdf`）から
  セクション1（立会市場普通取引）の銘柄別明細を取り出す。単純なテキスト抽出では
  PDF 生成時の描画順序により列順が保持されないことを実機で確認したため、
  `pdfplumber` で座標（x0）とフォントサイズを保持した単語データを取り出す
  Stage1（`jpx_stq_pdf.py`、landing.jpx_stq_words）と、そこから銘柄別明細に
  構造化する Stage2（`jpx_stq_facts.py`、landing.jpx_stq_facts）に分離した
  （EDINET と同じ「重い処理と変わりやすいロジックを分ける」考え方）。cleansed
  （`cleansed__jpx__stq_prices`）は毎回全量 rebuild（EDINET と同じ理由。実測
  28日分・124,403行の型付けが0.024秒で、incremental 化の理由が無い）。
  検証として dbt の singular テストで **OHLC整合性**（前場・後場それぞれ
  安値<=始値・終値<=高値）と **VWAP再計算**（売買代金÷売買高との照合、列の
  取り違えを検出できる検算）を追加した。詳細は `docs/raw_landing_design.md`
  「JPX 形式C」参照。形式A・形式B、セクション1以外の取引種別（ToSTNeT等）は
  未対応（今回のスコープ外、Stage1 は全ページ分保持しているため対象を広げても
  Stage1 の再実行は不要）。Postgres 版にあった `jpx_catalog_fdw.py`（ファイル
  目録のみ）は削除済み（git 履歴に残る）。
- **Prefect は ephemeral 実行**（常駐サーバ・ワーカーなし）。フロー `daily_transform` を
  `python -m flows.daily_transform` で単発実行。UI が要るようになったら通電枠限定の
  `prefect-server` compose サービスを後付け。
- **実行レポート**: フローが `dbt build` の後に HTML を生成し、S3（`ikuty-finance`
  バケット、キー `finance-dwh/run_report.html`、finance-lake と共用）へ上げて Slack へ
  URL 付き通知。`upload_report_to_s3` / `send_slack_notification` は finance-lake の
  `fetch_documents.py` と同実装（失敗はログのみ、`unfurl` 無効）。dbt 失敗時も
  レポート/S3/Slack まで実行してから非ゼロ終了する。レポートは cleansed の Parquet
  （`edinet_documents.parquet` / `edinet_facts.parquet`）の行数を数える（DuckDB での
  読み取りは高速なので、Postgres 版のような「直接数えない」制約は無い）。jpx は未移行
  のためレポートに含めない。

## 依存

Python 3.12。外部依存は `transform/` のみ: `dbt-core` / `dbt-duckdb` / `duckdb` /
`pyarrow` / `prefect` / `boto3` / `pdfplumber`（JPX PDF のテキスト化、`py.typed`
同梱のため追加 stub 不要）。開発は `requirements-dev.txt`（+ `mypy` / `pytest` /
`boto3-stubs` / `pyarrow-stubs`）。`mypy --strict` + `pytest`。CSV 抽出ロジック
（`transform/fdw/edinet_csv_fdw.py`）は標準ライブラリのみ。

## Mac Mini 上のパス

- リポジトリ: `/home/ikuty/finance-dwh`
- compose + `.env`: `/home/ikuty/finance-dwh/compose/`
- DuckDB カタログ・landing・cleansed の Parquet: `/home/ikuty/finance-dwh/data/`
  （HDD 側。`DATA_DIR` としてコンテナに `/data` で bind mount）
- systemd unit: `/etc/systemd/system/finance-dwh-{transform.service,transform.timer}`
  （常駐サービスは無い。旧 `finance-dwh-postgres.service` は廃止）
- systemd テンプレートは `<user>` プレースホルダを持たず `/home/ikuty/...` で確定済み
  （finance-lake で `<user>` 未置換により 2 日ジョブが止まった事故を踏まえた判断）。

## 実行順序（通電枠 04:00-06:00 JST）

jpx 04:01:00 → edinet 04:01:30 → **transform 04:01:45** → shutdown 04:02:00。
`finance-dwh-transform.service` は `After=` で lake 両ジョブの完了を待ち、共有シャット
ダウンは `After=` で transform 完了を待つ。`finance-lake/systemd/
finance-lake-shutdown.service` の `After=` には `finance-dwh-transform.service` が
既に追記済み（`finance-dwh-postgres.service` への参照はそもそも含まれていないため、
今回の Postgres 撤去にあたって finance-lake 側の変更は不要だった）。

## 現状（2026-09-13 時点）

- DuckDB/Parquet 化・Mac Mini への反映・実スケール性能検証まで完了（2026-09-12、
  EDINET 460日/20,583,925行の landing 取り込みが約17.5分、`dbt build` が9分11秒
  で完走、Postgres 版初回backfillの約71分から半減）。
- JPX 形式C（株式相場表・詳細日次）の PDF テキスト化・構造化を実装・実データ
  28日分で検証済み（`raw_landing_design.md`「JPX 形式C」参照）。`daily_transform`
  フローに組み込み済み、レポート（`run_report.py`）にも JPX の最新日・銘柄数を
  追加。
- pytest 60件 / mypy --strict パス（コンテナでのフルフロー含む実データ検証は
  ローカルで実施、Mac Mini への反映はこれから）。
- 未了: JPX 追加分のコミット → push → Mac Mini への反映、mart 層の設計、
  jpx のファイル目録（raw層）・形式A/B・セクション1以外の取引種別、Postgres 版の
  landing データ（Mac Mini 上の pgdata）の後始末。
