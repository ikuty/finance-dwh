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
- **JPX は未移行**（今回のスコープ外）。Postgres 版にあった `jpx_catalog_fdw.py`（ファイル
  目録のみ、PDF/TIFF の中身は扱わない）は削除済み（git 履歴に残る）。再着手時は DuckDB の
  `glob()` で素直に書き直せる見込み。
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
`pyarrow` / `prefect` / `boto3`。開発は `requirements-dev.txt`（+ `mypy` / `pytest` /
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

## 現状（2026-09-11 時点）

- DuckDB/Parquet 化を実装・ローカル検証済み（コンテナ含む）。raw（doc index の直接
  glob 読み）、landing（edinet_csv_facts、日付単位 Parquet）、cleansed
  （edinet__documents / edinet__facts、毎回 rebuild）、Prefect フロー（load → dbt →
  レポート/S3/Slack）、docker compose（transform 単一サービス、常駐なし）、systemd
  （transform のみ）、GitHub Actions 3 本（ci / build-push / deploy）を更新。
- ローカルでレイクの実構造サンプルに対し pytest 38 / mypy --strict / コンテナでの
  フルフロー緑（`✅ 成功 ... cleansed: EDINET明細10,470行・70社`、8.2秒）。
- 未了: コミット → push → Mac Mini への反映・実スケール（2000万行超）での性能検証、
  jpx / mart の DuckDB 移行、Postgres 版の landing データ（Mac Mini 上の pgdata）の
  後始末。
