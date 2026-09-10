# 変換ランナー。フェーズ1では dbt のみ（Prefect フロー・レポート・S3 はフェーズ2で追加）。
# ビルドコンテキストはリポジトリルート（compose の build.context: ..）。
FROM python:3.12-slim

WORKDIR /dbt

COPY transform/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# dbt プロジェクト本体。ローカル開発では compose が ../dbt を /dbt に bind mount して上書きする。
COPY dbt/ .

ENV DBT_PROFILES_DIR=/dbt

ENTRYPOINT ["dbt"]
CMD ["build"]
