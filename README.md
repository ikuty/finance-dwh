# finance-dwh

個人運用の財務データ分析基盤における**ウェアハウス層**。レイク層
（`finance-lake`）が生データのまま保存した書類・データを入力とし、
raw / landing / cleansed / mart の層からなる DWH を構築する。

- **DB**: DuckDB（組み込み型、サーバなし）/ **変換**: dbt Core（dbt-duckdb）/
  **オーケストレーション**: Prefect / **構築**: docker compose
- **raw 層**: DuckDB でレイクを直接読む（JSON はネイティブリーダー、CSV は
  Python でパースしてから Parquet に書く。理由は `docs/raw_landing_design.md`）。
- **cleansed 層**: dbt-duckdb の external materialization で Parquet へ出力。
  現状は EDINET の書類インデックス・CSV 明細のみ（mart は利用用途が固まってから設計）。
- **実行基盤**: `finance-lake` と同じ Mac Mini（Tailnet 上）。常駐サービスは無い
  （DuckDB は組み込み型）。GitHub Actions で CI/CD。

## リポジトリ構成

```
compose/       docker compose（transform 使い捨てコンテナのみ、常駐サービスなし）
transform/     Dockerfile / fdw/（EDINET CSV 抽出）/ flows/（Prefect フロー）/ report/（実行レポート）
dbt/           dbt プロジェクト（dbt-duckdb、raw/cleansed モデル）
systemd/       Mac Mini 用 unit テンプレート（transform.service/.timer の2本）
.github/workflows/  ci / build-push / deploy / gitleaks
docs/          architecture / raw_landing_design / deployment_design
CLAUDE.md      固有の設計判断
```

## ローカルで動かす

```
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q && .venv/bin/mypy          # テスト + 型チェック

cp .env.example compose/.env                    # LAKE_DIR をレイクの data ディレクトリに
cd compose
docker compose run --rm transform               # Prefect フロー（landing 取り込み → dbt build → レポート → S3/Slack）
```

`.env` の `S3_BUCKET_NAME` / `SLACK_WEBHOOK_URL` を空にすれば S3・Slack はスキップされる。
素の dbt を叩くときは `docker compose run --rm --entrypoint dbt transform <args>`。
永続化される実体（`.duckdb` カタログ・landing/cleansed の Parquet）は `DATA_DIR`
（既定 `../data`）配下。

詳細な設計は `docs/` と `CLAUDE.md` を参照。
