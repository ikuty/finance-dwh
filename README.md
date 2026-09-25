# finance-dwh

個人運用の財務データ分析基盤における**ウェアハウス層**。金融庁のEDINET API v2
（`https://api.edinet-fsa.go.jp/api/v2/`）から入手した有価証券報告書・四半期報告書
等の開示書類データをデータソースとするレイク層（`finance-lake`、生データのまま保存）
を入力とし、raw / landing / cleansed / intermediate / mart の層からなる DWH を構築する。
将来的には、AI（エージェント・LLM経由の問い合わせ）・BI（ダッシュボード等）双方から指標定義の
一貫性を保てるよう、セマンティックレイヤーの構築を目指している（未着手、mart層と
同様「利用目的が固まってから」着手する方針）。

## 設計思想: 電力コストの最小化

計算資源（クラウドの計算・ストレージ費用）の価格高騰への対策として、**電力使用量
あたりのコスト最小化**を目標にしている。クラウドサービスではなく、ローカルに配置した
低スペックの計算機（Mac Mini 2012、16GBメモリ、HDDストレージ）とスマートプラグによる電源枠管理を
採用しているのはこのため。電源枠管理は、定時ON/OFFによる待機電力の削減に加えて、
**消費電力量の数値化**（スマートプラグの計測機能によりジョブ実行にかかる実消費電力を
把握する）と**消費電力量のキャップ設定**（電源枠自体を稼働時間の上限として使い、
処理量の増加が青天井に電力消費へ跳ね返らないようにする）も目的としている。
今後は低消費電力TPUを用いたエッジAIの利用も予定している。この方針は`finance-lake`
（レイク層）とも共通で、両リポジトリとも同じMac Mini上で動く（詳細は`finance-lake`の
root `CLAUDE.md`参照）。

この方針は、DuckDBの`memory_limit`制限やバッチ分割処理（下記「既知の制約」参照）等、
リポジトリ内の複数の設計判断の前提になっている。

## アーキテクチャ概要

- **DB**: DuckDB（組み込み型、サーバなし）/ **変換**: dbt Core（dbt-duckdb）/
  **オーケストレーション**: Prefect（ephemeral 実行）/ **構築**: docker compose
- **レイク層との関係**: `finance-lake` の root `CLAUDE.md` の方針に従い、
  **共有ストレージ（ローカルファイル）を境界にした疎結合**。HTTP API やメッセージ
  キューは導入せず、レイクのデータディレクトリを read-only bind mount（`/lake:ro`）
  して直接読む。レイクの取得ジョブと DWH の変換ジョブは別リポジトリ・別コンテナ・
  別スケジュールで動く。
- **実行基盤**: `finance-lake` と同じ Mac Mini（Tailnet 上）。常駐サービスは無い
  （DuckDB は組み込み型）。GitHub Actions で CI/CD。

## データフロー / 層構成

| 層 | 実体 | 役割 |
|---|---|---|
| raw | DuckDB の view モデル（レイクを直接 glob 読み） | 書類一覧インデックス等、パース不要なものをそのまま view 化 |
| landing | Parquet（`{DATA_DIR}/landing/{table}/file_date=*/part.parquet`）。Prefect が日付単位で書く | レイクの生データ（CSV/PDF 等）を Python でパースして Parquet に固定化 |
| cleansed | dbt-duckdb モデル（external materialization、一部 incremental） | landing/raw を型付け・名寄せ。ビジネスロジックは最小限 |
| intermediate | dbt モデル（external materialization） | cleansedをビジネスロジックを含めて加工する中間モデル（会計基準ごとの指標抽出等） |
| mart | dbt モデル（external materialization） | 具体的な利用目的を持つ集計・統合（銘柄×期の指標等） |

詳細な設計判断（なぜ raw と landing で読み方式が違うか等）は `docs/architecture.md`
参照。

## リポジトリ構成

```
compose/       docker compose（transform 使い捨てコンテナのみ、常駐サービスなし）
transform/     Dockerfile / fdw/（EDINET CSV 抽出等）/ flows/（Prefect フロー）/ report/（実行レポート）
dbt/           dbt プロジェクト（dbt-duckdb、raw/cleansed/intermediate/mart モデル・seeds・tests）
systemd/       Mac Mini 用 unit テンプレート（transform.service/.timer の2本）
.github/workflows/  ci / build-push / deploy / gitleaks
docs/          architecture / raw_landing_design / deployment_design / mart_validation
CLAUDE.md      固有の設計判断
```

## 主要なデータモデル

| モデル | 層 | 役割 |
|---|---|---|
| `raw__edinet_document_index` | raw | EDINET書類一覧APIの生レスポンス |
| `cleansed__edinet__documents` | cleansed | 書類一覧インデックスの型付け版（上場企業のみ） |
| `cleansed__edinet__facts` | cleansed | EDINET CSV明細の型付け・名寄せ版（doc_id粒度、incremental） |
| `cleansed__edinet__report_periods` | cleansed | 銘柄×期の期間ディメンション（旧制度/移行期/新制度の判定含む） |
| `cleansed__jpx__stq_prices` | cleansed | JPX形式C（株式相場表・詳細日次）の型付け版 |
| `cleansed__jpx__daily_ohlc` | cleansed | JPX形式B（月次簡易OHLC、データ粒度は銘柄×営業日）の型付け版 |
| `cleansed__mufg__*` | cleansed | 商号変更・株式併合・株式分割の履歴 |
| `intermediate__edinet__dei_facts` | intermediate | 書類単位のDEI（会計基準・連結決算の有無） |
| `intermediate__edinet__jgaap_financial_facts` | intermediate | J-GAAP名項目のみで抽出した書類単位の14指標 |
| `intermediate__edinet__ifrs_financial_facts` | intermediate | IFRS名項目のみで抽出した書類単位の14指標 |
| `intermediate__edinet__usgaap_financial_facts` | intermediate | US GAAP名項目のみで抽出した書類単位の14指標 |
| `mart__edinet__financial_indicators` | mart | 銘柄×期の主要財務指標（総資産額・EPS・ROE等）。企業自身のaccounting_standardを優先しつつ3つのintermediateをcoalesce |

各モデルの列定義・テストは `dbt/models/*/_*.yml` を参照。

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
永続化される実体（`.duckdb` カタログ・landing/cleansed/intermediate/mart の Parquet）は `DATA_DIR`
（既定 `../data`）配下。

## 開発ワークフロー（ブランチ戦略）

git-flowの修正版。`main`への直接コミットは廃止。

- `main`: リリース対象のみ。`dev`: 日常の開発。`feature/*`: `dev`から派生、PRのbaseは`dev`。
- リリース時: `dev`→`release`→`main`の順にmergeしてデプロイする。

| 遷移 | マージ方式 |
|---|---|
| `feature/*` → `dev` | squash merge |
| `dev` → `release` | merge commit |
| `release` → `main` | merge commit |

GitHub上のdefault branchは`main`（PRのbaseは都度明示的に`dev`を指定すること）。

## CI/CD（`.github/workflows/`）

| workflow | トリガー | 内容 |
|---|---|---|
| `finance-dwh-ci.yml` | push (main) / PR | `mypy --strict` + `pytest` + `dbt parse` |
| `finance-dwh-build-push.yml` | push (main、`transform/**`・`dbt/**`変更時) | transformイメージをビルドしghcr.ioへpush |
| `finance-dwh-deploy.yml` | 手動（workflow_dispatch） | Tailscale経由でMac Miniへイメージpull・`git pull`反映 |
| `gitleaks.yml` | push (main) | 秘密情報の継続的チェック |

## 運用・デプロイ

Mac Mini（Ubuntu 24.04、16GB RAM）上でsystemdタイマーにより日次実行。スマートプラグ
による固定電源枠（`finance-lake`と共通）の制約があり、処理量が不定なタスク（バックログ
処理等）は日次の本質的な処理（landing取り込み・dbt build・レポート・Slack通知）より
後に回す設計（詳細は`transform/flows/daily_transform.py`のdocstring参照）。デプロイは
`finance-dwh-deploy.yml`の手動トリガーのみ（Mac Miniの稼働状態に依存するため）。詳細は
`docs/deployment_design.md`参照。

## 既知の制約

- **Mac Miniのメモリ制約**: DuckDBの`memory_limit`を明示的に制限している（実機での
  メモリ逼迫・スワップ発生を踏まえた対策）。2026-09-23にメモリを16GBに増設し、
  制限値も3GB→12GBへ引き上げたが、大量データの一括処理（初回full-refresh等）では
  依然バッチ分割が必要になる場合がある。
- mart層は`mart__edinet__financial_indicators`が最初の実装（2026-09-20〜）。今後の
  拡張は利用目的が固まってから追加する方針。
- intermediate層は、会計基準(J-GAAP/IFRS/US GAAP)をまたいだcoalesceが原因のバグを
  受けて2026-09-21に導入（詳細な経緯・実データでの検証は`docs/mart_validation.md`
  参照）。

## ライセンス

MIT License（`LICENSE`参照）

## 参照

詳細な設計は `docs/`（`architecture.md` / `raw_landing_design.md` / `deployment_design.md` /
`mart_validation.md`）と `CLAUDE.md` を参照。
