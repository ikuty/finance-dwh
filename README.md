# finance-dwh

個人運用の財務データ分析基盤における**ウェアハウス層**。レイク層
（`finance-lake`）が生データのまま保存した書類・データを入力とし、
raw / cleansed / mart の 3 層からなる DWH を構築する。

- **DB**: PostgreSQL / **変換**: dbt Core / **オーケストレーション**: Prefect / **構築**: docker compose
- **raw 層**: `file_fdw` + stdlib Python ラッパーでレイクのファイルツリーを read-only 接続し、
  外部テーブル `raw.raw__*` として公開。dbt はこれを source として扱う。
- **現状は raw 層のみ**（cleansed / mart は利用用途が固まってから設計）。
- **実行基盤**: `finance-lake` と同じ Mac Mini（Tailnet 上）。GitHub Actions で CI/CD。

## リポジトリ構成

```
compose/       docker compose（postgres 常駐 + transform 使い捨て）
postgres/      postgres イメージ: Dockerfile / fdw/*.py（ラッパー3）/ initdb/*.sql
transform/     dbt ランナー: Dockerfile / flows/（Prefect フロー）/ report/（実行レポート）
dbt/           dbt プロジェクト（現状 models/raw/_raw__sources.yml のみ）
systemd/       Mac Mini 用 unit テンプレート（3 本）
.github/workflows/  ci / build-push / deploy / gitleaks
docs/          architecture / fdw_raw_layer_design / deployment_design
CLAUDE.md      固有の設計判断
```

## ローカルで動かす

```
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q && .venv/bin/mypy          # テスト + 型チェック

cp .env.example compose/.env                    # LAKE_DIR をレイクの data ディレクトリに
cd compose
docker compose up -d postgres                   # initdb が raw スキーマ + 外部テーブルを作成
docker compose run --rm transform               # Prefect フロー（dbt build → レポート → S3/Slack）
```

`.env` の `S3_BUCKET_NAME` / `SLACK_WEBHOOK_URL` を空にすれば S3・Slack はスキップされる。
素の dbt を叩くときは `docker compose run --rm --entrypoint dbt transform <args>`。

詳細な設計は `docs/` と `CLAUDE.md` を参照。
