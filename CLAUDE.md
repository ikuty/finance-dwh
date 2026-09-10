# finance-dwh

`finance-lake`（レイク層）の後段を担う**ウェアハウス層**のリポジトリ。raw / cleansed /
mart の 3 層からなる DWH を、同じ Mac Mini 上に PostgreSQL + dbt Core + Prefect +
docker compose で構築する。全体像は `docs/architecture.md`、raw 層の詳細は
`docs/fdw_raw_layer_design.md`、デプロイは `docs/deployment_design.md`。

## 固有の設計判断

- **レイクとは共有ストレージ境界で疎結合**。レイクのデータディレクトリを Postgres
  コンテナに `read-only` bind mount し、`file_fdw` + stdlib Python ラッパー
  （`postgres/fdw/*.py`）で外部テーブル `raw.raw__*` として読む。HTTP API 等は無し。
- **現状は raw 層のみ**（2026-09-10 決定）。dbt は `models/raw/_raw__sources.yml` の
  source 定義だけで、モデル（cleansed / mart）は 0 個。`dbt build` は「Nothing to do」。
  cleansed の形は利用用途が固まってから設計する（一度モデルを作ったが、用途未確定の
  まま持つのは負債になるため撤去した）。`macros/generate_schema_name.sql` は将来用に残置。
- **命名**: 全リレーションにスキーマ名を prefix する。`raw.raw__edinet_csv_facts` /
  `cleansed.cleansed__...`。mart のみ prefix 無し。
- **JPX は「ファイル目録」のみ**（`raw__jpx_file_catalog`: format/period/path/byte_size/
  mtime）。PDF/TIFF の中身（相場数値）は `file_fdw` では読めないため取り込まない。必要に
  なったら Prefect に pdftotext+パーサの load タスクを足す（現状スコープ外）。
- **Postgres だけ通電枠のあいだ常駐**（`finance-dwh-postgres.service`、`RemainAfterExit`）。
  「コンテナ内にデーモンを置かない」方針の唯一の例外。変換ジョブは `docker compose run
  --rm` の使い捨て。
- **Prefect は ephemeral 実行**（常駐サーバ・ワーカーなし）。フロー `daily_transform` を
  `python -m flows.daily_transform` で単発実行。UI が要るようになったら通電枠限定の
  `prefect-server` compose サービスを後付け。
- **実行レポート**: フローが `dbt build` の後に層別行数等の HTML を生成し、S3
  （`ikuty-finance` バケット、キー `finance-dwh/run_report.html`、finance-lake と共用）へ
  上げて Slack へ URL 付き通知。`upload_report_to_s3` / `send_slack_notification` は
  finance-lake の `fetch_documents.py` と同実装（失敗はログのみ、`unfurl` 無効）。
  dbt 失敗時もレポート/S3/Slack まで実行してから非ゼロ終了する。

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

- 実装済み: raw 層（外部テーブル3 + ラッパー3 + pytest）、dbt source 定義、Prefect フロー
  （レポート/S3/Slack）、docker compose、systemd 3 unit、GitHub Actions 4 本。
- ローカルでレイクのサンプルに対し pytest / mypy --strict / フルフロー緑。
- 未了: GitHub リポジトリ作成 + Secrets、Mac Mini 初回セットアップ、finance-lake の
  shutdown unit 追記、cleansed / mart の設計。
