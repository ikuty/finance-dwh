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
- **JPX 形式C（株式相場表・詳細日次）は PDF テキスト化＋構造化まで実装済み**（2026-09-13）。
  レイクの PDF（`jpx-daily-pdf-dl/raw/detailed-daily/{yyyy}/{mm}/{dd}/stq.pdf`）から
  セクション1（立会市場普通取引）の銘柄別明細を取り出す。単純なテキスト抽出では
  PDF 生成時の描画順序により列順が保持されないことを実機で確認したため、
  `pdfplumber` で座標（x0）とフォントサイズを保持した単語データを取り出す
  Stage1（`jpx_stq_pdf.py`、landing.jpx_stq_words）と、そこから銘柄別明細に
  構造化する Stage2（`jpx_stq_facts.py`、landing.jpx_stq_facts）に分離した
  （EDINET と同じ「重い処理と変わりやすいロジックを分ける」考え方）。cleansed
  （`cleansed__jpx__stq_prices`）は毎回全量 rebuild（EDINET と同じ理由。実測
  28日分・124,403行の型付けが0.024秒で、incremental 化の理由が無い）。
  検証として dbt の singular テストで **OHLC整合性**（前場・後場それぞれ
  安値<=始値・終値<=高値）と **VWAP再計算**（売買代金÷売買高との照合、列の
  取り違えを検出できる検算）を追加した。詳細は `docs/raw_landing_design.md`
  「JPX 形式C」参照。セクション1以外の取引種別（ToSTNeT等）は未対応（Stage1
  は全ページ分保持しているため対象を広げても Stage1 の再実行は不要）。
  Postgres 版にあった `jpx_catalog_fdw.py`（ファイル目録のみ）は削除済み
  （git 履歴に残る）。
- **JPX 形式B（株式相場表・月次簡易OHLC、確定済み過去アーカイブ、2020-01〜
  2025-09の69ヶ月）も実装済み**（2026-09-13）。形式Cより単純（市場区分・
  業種の見出しが無い単一フラット表、日付が各行にある）だが、実機で3つの
  重大な落とし穴を発見した:
  1. OHLC8列は数値が右揃えのため**x0ではなくx1（右端）で列判定する必要が
     ある**（低位株の短い数値はx0が隣列の境界を越えて誤分類される）。
  2. **列座標は固定できない**。PDF生成ソフトウェアが2022年12月→2023年1月
     でAntennaHouse→iTextに切り替わるのに加え、AntennaHouse世代内でも
     月によって座標が変動する（69ヶ月一括バックフィルを実行して初めて
     発覚。2020-01分だけ想定の1/1600以下の行数しか取り込めなかった）。
     固定座標定数を諦め、**各月PDFのヘッダー行から列境界を動的に算出する**
     方式に設計変更した。AntennaHouse世代はコード・名称がスペース無しで
     1トークンに結合される（例:"13010極洋"）ため、「数字の後に非ASCII
     文字が来る」ことを目印に分離する。
  3. **メモリ逼迫でMac Miniが到達不能になった**（物理メモリ7.7GB）。
     1ヶ月ぶんの単語（実測100万語超）を`list()`で一括保持していたことに
     加え、**pdfplumberがページごとの解析結果を内部キャッシュし続ける**
     ため、ページ単位でストリーム処理するだけではピークメモリが下がら
     なかった（実測約9.8GB）。`page.flush_cache()`を追加してピーク約
     173MB（約56分の1）に削減した（`jpx_stq_pdf.py`、形式Cとも共通の
     Stage1のため一括対応）。
  銘柄コードも新体系（2024年1月導入、英字が途中に混在する5桁コード）に
  対応。1ファイル=1ヶ月ぶんを日付ごとの`landing.jpx_monthly_ohlc_facts`
  パーティションへ書き分け、「取り込み済み」は本体と別ディレクトリの
  月次マーカーで判定する（確定済みアーカイブのため、EDINET/形式Cのような
  遡及窓は不要）。4世代サンプル（2020-01/2022-12/2023-01/2025-09）で
  OHLC違反0件を確認済み。詳細は `docs/raw_landing_design.md`「JPX 形式B」
  参照。
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
`pyarrow` / `prefect` / `boto3` / `pdfplumber`（JPX PDF のテキスト化、`py.typed`
同梱のため追加 stub 不要）。開発は `requirements-dev.txt`（+ `mypy` / `pytest` /
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

## 現状（2026-09-13 時点）

- DuckDB/Parquet 化・Mac Mini への反映・実スケール性能検証まで完了（2026-09-12、
  EDINET 460日/20,583,925行の landing 取り込みが約17.5分、`dbt build` が9分11秒
  で完走、Postgres 版初回backfillの約71分から半減）。
- JPX 形式C（株式相場表・詳細日次）の PDF テキスト化・構造化を実装・実データ
  28日分で検証済み、Mac Mini 実機への反映・検証も完了（`raw_landing_design.md`
  「JPX 形式C」参照）。
- JPX 形式B（株式相場表・月次簡易OHLC、2020-01〜2025-09の69ヶ月）も実装・
  実データ1ヶ月分（88,184行）で検証済み（`raw_landing_design.md`「JPX 形式B」
  参照）。`daily_transform`フロー・レポートに組み込み済み。Mac Mini への
  反映はこれから（69ヶ月分の初回バックフィルは電源枠を跨ぐ見込み）。
- pytest 73件 / mypy --strict パス。
- 未了: JPX 形式B分のコミット → push → Mac Mini への反映（69ヶ月バックフィル）、
  mart 層の設計、jpx のファイル目録（raw層）・形式A・セクション1以外の取引
  種別、Postgres 版の landing データ（Mac Mini 上の pgdata）の後始末。
