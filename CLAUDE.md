# finance-dwh

`finance-lake`（レイク層）の後段を担う**ウェアハウス層**のリポジトリ。raw / cleansed /
mart の 3 層からなる DWH を、同じ Mac Mini 上に PostgreSQL + dbt Core + Prefect +
docker compose で構築する。全体像は `docs/architecture.md`、raw 層の詳細は
`docs/fdw_raw_layer_design.md`、デプロイは `docs/deployment_design.md`。

## 固有の設計判断

- **レイクとは共有ストレージ境界で疎結合**。レイクのデータディレクトリを Postgres
  コンテナに `read-only` bind mount し、`file_fdw` + stdlib Python ラッパー
  （`postgres/fdw/*.py`）で外部テーブル `raw.raw__*` として読む。HTTP API 等は無し。
- **`file_fdw` の全量スキャン対策（2026-09-11）**: `raw__edinet_csv_facts` は `count(*)`
  すら実測 約12分（述語プッシュダウン不可）。そこで FDW は「日付指定の抽出専用」に
  格下げし、`edinet_csv_fdw.py --date` で 1 日ぶんずつ native の `landing.edinet_csv_facts`
  へ COPY（Prefect の `flows/load_edinet.py`、dbt の外）、dbt の `cleansed__edinet__facts`
  は landing 由来の **incremental**（delete+insert、`_landing_loaded_at` 透かしで駆動）。
  対象日 = 直近 `LANDING_LOOKBACK_DAYS`(7) 日 ∪（ディスクにあるが `_load_log` に無い日）。
  詳細は `docs/fdw_raw_layer_design.md`。
- **cleansed の現状**: `cleansed__edinet__documents`（table、FDW から毎回 rebuild、
  上場会社のみ、docID で名寄せ）と `cleansed__edinet__facts`（incremental、上記）のみ。
  jpx・doc index の cleansed / mart は用途が固まってから。
- **命名**: 全リレーションにスキーマ名を prefix する。`raw.raw__edinet_csv_facts` /
  `cleansed.cleansed__...`。landing / mart は prefix 無し。
- **JPX は「ファイル目録」のみ**（`raw__jpx_file_catalog`: format/period/path/byte_size/
  mtime）。PDF/TIFF の中身（相場数値）は `file_fdw` では読めないため取り込まない。必要に
  なったら Prefect に pdftotext+パーサの load タスクを足す（現状スコープ外）。
- **Postgres だけ通電枠のあいだ常駐**（`finance-dwh-postgres.service`、`RemainAfterExit`）。
  「コンテナ内にデーモンを置かない」方針の唯一の例外。変換ジョブは `docker compose run
  --rm` の使い捨て。
- **Prefect は ephemeral 実行**（常駐サーバ・ワーカーなし）。フロー `daily_transform` を
  `python -m flows.daily_transform` で単発実行。UI が要るようになったら通電枠限定の
  `prefect-server` compose サービスを後付け。
- **実行レポート**: フローが `dbt build` の後に HTML を生成し、S3（`ikuty-finance`
  バケット、キー `finance-dwh/run_report.html`、finance-lake と共用）へ上げて Slack へ
  URL 付き通知。`upload_report_to_s3` / `send_slack_notification` は finance-lake の
  `fetch_documents.py` と同実装（失敗はログのみ、`unfurl` 無効）。dbt 失敗時も
  レポート/S3/Slack まで実行してから非ゼロ終了する。
  レポートに載せる行数は cleansed の native テーブル（`cleansed__edinet__documents` /
  `cleansed__edinet__facts`）と `raw__jpx_file_catalog`（~1秒）。FDW の
  `raw__edinet_csv_facts`（約12分）・`raw__edinet_document_index`（約11秒）は直接数えない。

## 依存

Python 3.12 + stdlib（FDW ラッパー）。外部依存は `transform/` のみ:
`dbt-core` / `dbt-postgres` / `prefect` / `boto3`。開発は `requirements-dev.txt`
（+ `mypy` / `pytest` / `types-psycopg2` / `boto3-stubs`）。`mypy --strict` + `pytest`。

## Mac Mini 上のパス

- リポジトリ: `/home/ikuty/finance-dwh`
- compose + `.env`: `/home/ikuty/finance-dwh/compose/`
- Postgres データ: `/home/ikuty/finance-dwh/data/pgdata`（HDD 側）
- systemd unit: `/etc/systemd/system/finance-dwh-{postgres.service,transform.service,transform.timer}`
- systemd テンプレートは `<user>` プレースホルダを持たず `/home/ikuty/...` で確定済み
  （finance-lake で `<user>` 未置換により 2 日ジョブが止まった事故を踏まえた判断）。

## 実行順序（通電枠 04:00-06:00 JST）

jpx 04:01:00 → edinet 04:01:30 → **transform 04:01:45** → shutdown 04:02:00。
`finance-dwh-transform.service` は `After=` で lake 両ジョブの完了を待ち、共有シャット
ダウンは `After=` で transform 完了を待つ。**`finance-lake/systemd/
finance-lake-shutdown.service` の `After=` に `finance-dwh-transform.service` を追記
すること**（別リポジトリの変更、未実施なら要実施）。

## 現状（2026-09-11 時点）

- 実装・デプロイ済み: raw 層（外部テーブル3 + ラッパー3）、landing（edinet_csv_facts）+
  `load_edinet` タスク、cleansed（edinet__documents / edinet__facts incremental）、
  Prefect フロー（load → dbt → レポート/S3/Slack）、docker compose、systemd 3 unit、
  GitHub Actions 4 本、GitHub リポジトリ（public）、Mac Mini（systemd 登録済み、
  raw 層まで実機動作確認済み）、finance-lake の shutdown unit 追記。
- ローカルでレイクの実構造サンプルに対し pytest / mypy --strict / フルフロー（incremental
  含む）緑。
- 未了: Mac Mini で landing の初回バックフィル（全 ~459 日、実質 ~30〜45分。通電枠外で
  手動実施）、jpx / mart の設計。
