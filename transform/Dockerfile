# 変換ランナー。既定エントリポイントは Prefect フロー（daily_transform）。
# 素の dbt を叩きたいときは `docker compose run --rm --entrypoint dbt transform <args>`。
# DuckDB は組み込み型（サーバなし）。このイメージ単体で load・dbt build・レポート生成まで完結する。
# ビルドコンテキストはリポジトリルート（compose の build.context: ..）。
FROM python:3.12-slim

WORKDIR /app

COPY transform/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dbt/ /app/dbt/
COPY transform/flows/ /app/flows/
COPY transform/report/ /app/report/
# landing への日付単位 load で使う CSV 抽出ロジック（edinet_csv_fdw.py 由来）。
COPY transform/fdw/edinet_csv_fdw.py /app/fdw/edinet_csv_fdw.py

ENV DBT_PROJECT_DIR=/app/dbt \
    DBT_PROFILES_DIR=/app/dbt \
    PYTHONPATH=/app/fdw \
    PREFECT_HOME=/tmp/prefect \
    PREFECT_SERVER_ANALYTICS_ENABLED=false

# Prefect の ephemeral サーバが状態を書く先。使い捨て（コンテナと共に消える）。
# /root/.prefect も作る（block 自動登録が memo_store.toml をそこへ書こうとするため）。
RUN mkdir -p /tmp/prefect /root/.prefect

ENTRYPOINT ["python", "-m", "flows.daily_transform"]
