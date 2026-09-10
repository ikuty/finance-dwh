# finance-dwh

個人運用の財務データ分析基盤における**ウェアハウス層**。レイク層
（`finance-lake`）が生データのまま保存した書類・データを入力とし、
raw / cleansed / mart の 3 層からなる DWH を構築する。

- **DB**: PostgreSQL
- **変換**: dbt Core
- **オーケストレーション**: Prefect
- **raw 層**: PostgreSQL の FDW（`file_fdw`）でレイクのファイルシステムを
  read-only 接続し、dbt source として扱う
- **構築**: docker compose
- **実行基盤**: `finance-lake` と同じ Mac Mini（Tailnet 上）。GitHub Actions で CI/CD

詳細な設計は `docs/` を参照。
