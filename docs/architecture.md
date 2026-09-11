# finance-dwh アーキテクチャ（2026-09-11、DuckDB/Parquet 化）

`finance-lake`（レイク層）が生データのまま保存した書類・データを入力として、
raw / landing / cleansed / mart の層からなる DWH を構築する。

**tech スタック**: DuckDB（組み込み型、サーバなし）+ dbt Core（dbt-duckdb アダプタ）+
Prefect（ephemeral 実行）+ docker compose + GitHub Actions。

2026-09-10 に PostgreSQL + `file_fdw` で実装したが、実機検証（下記）で全量スキャンが
致命的に遅いことが判明し、2026-09-11 に DuckDB + Parquet へ全面移行した。旧実装は
git 履歴（`afc45c7`〜`5ea9b52`）に残る。

## レイク層との関係（疎結合）

`finance-lake` の root `CLAUDE.md` の方針に従い、**共有ストレージ（ローカルファイル）を
境界にした疎結合**とする。HTTP API やメッセージキューは導入しない。

- レイクのデータディレクトリ（Mac Mini の `/home/ikuty/finance-lake/data`）を、
  transform コンテナに **read-only bind mount**（`/lake:ro`）する。
- DuckDB でレイクのファイルツリーを直接読む（JSON はネイティブリーダーで、CSV は
  Python でパースしてから）。書き込みは一切しない（ro マウントで物理的に保証）。
- レイクの取得ジョブと DWH の変換ジョブは、別リポジトリ・別コンテナ・別スケジュール。

## 層

| 層 | 実体 | 現状 |
|---|---|---|
| raw | DuckDB の view モデル（doc index）。レイクを直接 glob 読み | **doc index のみ** |
| landing | Parquet（`{DATA_DIR}/landing/edinet_csv_facts/file_date=*/part.parquet`）。Prefect の `load_edinet` が日付単位で書く | **edinet_csv_facts のみ** |
| cleansed | dbt-duckdb の external materialization（Parquet）。型付け・名寄せ、毎回 rebuild | **edinet__documents / edinet__facts のみ** |
| mart | dbt モデル（業務エンティティ） | **未設計** |

- 命名: raw/cleansed は `<層>__<内容>`（例: `raw__edinet_document_index`、
  `cleansed__edinet__documents`）。landing はディレクトリ構造で表現。
- なぜ raw と landing が別方式か: DuckDB のネイティブ **JSON** リーダーは書類一覧
  （`document_list.json`）を問題なく直接読めるので raw の view で十分。一方 DuckDB
  自身の **CSV** リーダーは EDINET の長大なテキストブロック（引用符付きフィールドが
  数万文字）で解析エラーになることを実機で確認したため、CSV は Python
  （`edinet_csv_fdw.py`、テスト済みの csv モジュールベース）でパースし、landing に
  Parquet として書いてから DuckDB に読ませる。
- `macros/generate_schema_name.sql` で `cleansed` を接頭辞なしスキーマ（DuckDB
  カタログ内の schema）に出す。

## raw / landing の内容

| 名前 | 中身 | 実体 |
|---|---|---|
| `raw__edinet_document_index` | 書類一覧 API 生レスポンス `results[]`（1行=1書類、29項目 + file_date） | dbt view モデル。`read_json_auto()` でレイクの `response/*/*/*/document_list.json` を直接 glob 読み |
| `landing.edinet_csv_facts`（source） | EDINET CSV(type=5) 全書類の明細（縦持ち、provenance 3列 + 9列） | Parquet、`edinet_csv_fdw.py` が日付ごとにパースして書く |

jpx（相場表 PDF/TIFF のファイル目録）は今回のスコープ外。Postgres 版にあった
`jpx_catalog_fdw.py` は削除済み（git 履歴に残る、再着手時は DuckDB の `glob()` で
書き直せる見込み）。

## 実行モデル（Mac Mini）

`finance-lake` と同じ Mac Mini（Tapo スマートプラグで毎日 04:00-06:00 JST のみ通電）。

- **常駐サービスは無い**。DuckDB は組み込み型なので、Postgres 版で唯一の常駐例外
  だった `finance-dwh-postgres.service` は不要になった。
- **変換ジョブは使い捨て**: `finance-dwh-transform.service` が `docker compose run --rm
  transform` で Prefect フロー `daily_transform` を単発実行（ephemeral モード、常駐サーバ・
  ワーカーなし）。
- フローの流れ: landing 取り込み（`load_edinet`）→ `dbt build` → 実行レポート HTML 生成
  → S3 アップロード → Slack 通知。dbt 失敗時もレポート/S3/Slack まで実行してから非ゼロ終了。
- タイミング: jpx 04:01:00 → edinet 04:01:30 → **transform 04:01:45** → shutdown 04:02:00。
  transform は `After=` で lake の両ジョブ完了を待ち、共有シャットダウンは `After=` で
  transform 完了を待つ。詳細は `deployment_design.md`。

## 実測（性能比較、2026-09-11）

| | Postgres + file_fdw | DuckDB + Parquet |
|---|---|---|
| `raw__edinet_csv_facts` の `count(*)`（20.5M行・全量） | 約12分 | （landing 経由のため直接該当せず。cleansed の行数カウントは高速） |
| landing/cleansed の初回フルビルド | load 35.5分 + dbt build 35分 ≈ 71分 | 同等データで dbt build 0.2秒（ローカルサンプル規模） |
| 日次実行（コンテナ起動込み） | 数十秒〜数分（実機実測 56秒） | 8.2秒（ローカルサンプル規模、コンテナ込み） |

実スケール（2000万行超）での DuckDB 側の性能は Mac Mini 反映後に確認する
（`docs/raw_landing_design.md` の「既知の制約」参照）。

## 実装状況（2026-09-11 時点）

- raw（doc index の view）、landing（edinet_csv_facts、日付単位 Parquet）、cleansed
  （edinet__documents / edinet__facts、毎回 rebuild）、Prefect フロー、docker compose
  （transform 単一サービス）、systemd（transform のみ）、GitHub Actions 3 本まで実装・
  ローカル検証（コンテナ含む）済み。
- 未了: コミット → push → Mac Mini への反映・実スケールでの性能検証、jpx / mart の
  DuckDB 移行、Mac Mini 上の Postgres 版データ（pgdata）の後始末。
