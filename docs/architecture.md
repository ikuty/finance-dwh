# finance-dwh アーキテクチャ（2026-09-10）

`finance-lake`（レイク層）が生データのまま保存した書類・データを入力として、
raw / cleansed / mart の 3 層からなる DWH を構築する。tech スタック（PostgreSQL /
dbt Core / Prefect / docker compose / GitHub Actions）はレイヤ全体方針と別途決定済み。

## レイク層との関係（疎結合）

`finance-lake` の root `CLAUDE.md` の方針に従い、**共有ストレージ（ローカルファイル）を
境界にした疎結合**とする。HTTP API やメッセージキューは導入しない。

- レイクのデータディレクトリ（Mac Mini の `/home/ikuty/finance-lake/data`）を、DWH の
  Postgres コンテナに **read-only bind mount**（`/lake:ro`）する。
- Postgres の `file_fdw` + stdlib Python ラッパーで、レイクのファイルツリーを外部テーブル
  `raw.raw__*` として読む。書き込みは一切しない（ro マウントで物理的に保証）。
- レイクの取得ジョブと DWH の変換ジョブは、別リポジトリ・別コンテナ・別スケジュール。

## 層

| 層 | スキーマ | 実体 | 現状 |
|---|---|---|---|
| raw | `raw` | `file_fdw` 外部テーブル（`initdb/02_fdw.sql` が作成）。全列 text、生データに忠実 | **実装済み** |
| landing | `landing` | 日付単位 load で作る native テーブル（`file_fdw` の全量スキャン回避用）。Prefect の `load_edinet` が管理 | **edinet_csv_facts のみ** |
| cleansed | `cleansed` | dbt モデル（型付け・名寄せ） | **edinet__documents / edinet__facts のみ**（後者は incremental） |
| mart | `mart` | dbt モデル（業務エンティティ） | **未設計** |

- 命名: 全リレーションにスキーマ名を prefix（`raw.raw__edinet_csv_facts` /
  `cleansed.cleansed__...`）。landing / mart は prefix 無し。
- `file_fdw` は述語プッシュダウン不可で `count(*)` すら全量スキャン（実測: EDINET CSV は
  約12分）。そのため **edinet_csv_facts は FDW を「日付指定の抽出専用」に格下げし、
  `landing.edinet_csv_facts`（native）を挟んで cleansed を incremental で作る**
  （`fdw_raw_layer_design.md`）。jpx（1秒）・doc index（11秒）は当面 FDW 直読み。
- `macros/generate_schema_name.sql` で `cleansed` を接頭辞なしスキーマに出す。

## raw 層の内容

| 外部テーブル | 中身 | ラッパー |
|---|---|---|
| `raw.raw__edinet_csv_facts` | EDINET CSV(type=5) 全書類の明細（縦持ち、provenance 3列 + 9列） | `postgres/fdw/edinet_csv_fdw.py` |
| `raw.raw__edinet_document_index` | 書類一覧 API 生レスポンス `results[]`（1行=1書類、29項目 + file_date） | `postgres/fdw/edinet_docindex_fdw.py` |
| `raw.raw__jpx_file_catalog` | JPX 相場表 PDF/TIFF の**ファイル目録のみ**（format/period/path/byte_size/mtime）。相場数値は含まない | `postgres/fdw/jpx_catalog_fdw.py` |

JPX が目録だけなのは、元データが PDF/TIFF で `file_fdw` が中身を読めないため。相場数値が
必要になったら、Prefect フローに pdftotext + パーサの load タスクを足して native テーブル
へ COPY する（現状スコープ外）。詳細は `fdw_raw_layer_design.md`。

## 実行モデル（Mac Mini）

`finance-lake` と同じ Mac Mini（Tapo スマートプラグで毎日 04:00-06:00 JST のみ通電）。

- **Postgres だけは通電枠のあいだ常駐**（`finance-dwh-postgres.service`、`RemainAfterExit`）。
  「コンテナ内にデーモンを置かない」方針の唯一の例外（DB は本質的に常駐プロセスのため）。
- **変換ジョブは使い捨て**: `finance-dwh-transform.service` が `docker compose run --rm
  transform` で Prefect フロー `daily_transform` を単発実行（ephemeral モード、常駐サーバ・
  ワーカーなし）。
- フローの流れ: Postgres 起動待ち → `dbt build` → 実行レポート HTML 生成 → S3 アップロード
  → Slack 通知。dbt 失敗時もレポート/S3/Slack まで実行してから非ゼロ終了。
- タイミング: jpx 04:01:00 → edinet 04:01:30 → **transform 04:01:45** → shutdown 04:02:00。
  transform は `After=` で lake の両ジョブ完了を待ち、共有シャットダウンは `After=` で
  transform 完了を待つ。詳細は `deployment_design.md`。

## 実装状況（2026-09-11 時点）

- raw 層（外部テーブル3 + ラッパー3 + pytest）、dbt source 定義、Prefect フロー
  （レポート/S3/Slack）、docker compose、systemd 3 unit、GitHub Actions 4 本まで実装。
- ローカルでレイクのサンプルに対し pytest / mypy --strict / フルフロー緑。
- 未了: GitHub リポジトリ作成 + Secrets 登録、Mac Mini 初回セットアップ、
  `finance-lake` の共有シャットダウン unit への `finance-dwh-transform.service` 追記。
- cleansed / mart は用途が固まってから設計。
