# finance-dwh

`finance-lake`（レイク層）の後段を担う**ウェアハウス層**のリポジトリ。raw / cleansed /
mart の 3 層からなる DWH を、同じ Mac Mini 上に **DuckDB + dbt Core（dbt-duckdb）+
Prefect + docker compose** で構築する。全体像は `docs/architecture.md`、raw/landing の
抽出詳細は `docs/raw_landing_design.md`、デプロイは `docs/deployment_design.md`。

**mart層の指標（算出方法・計算式・企業固有パッチ等）は常に `docs/mart_indicators.md`
に記載する。** `dbt/models/mart/*.sql`・`dbt/models/intermediate/*.sql`の指標定義を
追加・変更した場合は、同じ変更の中で必ず`docs/mart_indicators.md`も最新の状態に
更新すること（正とするのは常にSQL本体、このドキュメントはその要約）。常に可読性を
意識し、**表で構造を理解させることを優先し、表の外の文章は最小限**にすること。

**2026-09-11: PostgreSQL + file_fdw から DuckDB + Parquet へ全面移行した**（下記参照）。
旧 Postgres 実装は git 履歴（`afc45c7`〜`5ea9b52`）に残る。

## ブランチ戦略（2026-09-16決定）

git-flowの修正版。従来の「mainへ直接コミット」は廃止した。

- `main`: リリース対象のみ。
- `dev`: `main`から派生。日常の開発はここに積む。
- `feature/*`: `dev`から派生。新規開発・機能改修用。PRのbaseは`dev`。
- リリース時: `dev`→`release`→`main`の順にmergeしてデプロイする。

マージ方式（GitHubにはマージ先ブランチごとの強制設定は無いため、運用上の約束事として
手動で選択する。squash・merge commitとも両リポジトリでリポジトリ設定上は有効化済み）:

| 遷移 | マージ方式 |
|---|---|
| `feature/*` → `dev` | squash merge |
| `dev` → `release` | merge commit |
| `release` → `main` | merge commit |

GitHub上のdefault branchは`main`のまま変更していない。PRのbaseは都度明示的に`dev`を
指定すること（省略すると`main`向けになってしまう）。

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
- **mufg-corporate-actions（株式分割・株式併合・商号変更、2026-09-16追加）**:
  レイク層は週次（月曜）で3ページの生HTMLをそのまま保存するのみ。DWH側で
  `transform/fdw/mufg_corporate_actions.py`（BeautifulSoup、`html.parser`
  バックエンド）がパースし、`load_mufg_corporate_actions.py`が
  `landing.mufg_stock_splits` / `mufg_stock_consolidations` /
  `mufg_company_name_changes`へ書く。3ページとも**列構成が異なる**
  （分割7列・併合5列・商号変更4列）ため独立したdataclass・パース関数を持つ。
  **各ページはその時点での全履歴＋今後の予定を毎回まるごと再掲載する形式**
  （実機確認: 3ページとも2002年8月まで遡る）のため、EDINET/JPXのような
  「日付ごとに独立したファイル」ではなく「バックフィル」という概念が存在
  しない。landingは週ごとに`file_date=YYYY-MM-DD`で蓄積する（監査証跡）が、
  **cleansedはlandingの最新file_dateのみを読む**（古い週のスナップショットは
  新しい週に完全に包含されるため）。比率列（"1：2"、"10株→1株"）はcleansedで
  `ratio_before`/`ratio_after`のdecimal 2列に分割する（実データで書式が完全に
  一貫していることを確認済み）。個人利用限定
  （`services/mufg-corporate-actions/CLAUDE.md`参照、kabu.com利用規約により
  商用利用・第三者への再配信は不可）。処理量が常に小さい（週次・3ファイルの
  み）ため、JPX-C/EDINETのようなrecent/backlog分割は不要で常に`dbt_build`
  より前に置く。
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

## 2026-09-13以降の主な変更

- **`cleansed__edinet__facts`をincrementalへ変更**（2026-09-17〜20）: landingが
  813日・2.8GBまで積み上がった結果、`materialized='external'`での毎回全量
  rebuildがdbt build全体の95%（980秒）を占めるようになったため。DuckDBネイティブ
  テーブル（`materialized='incremental'`）＋`post_hook`でのParquetエクスポートに
  変更した。粒度もcontext_idの意味の誤解（相対年度ラベルを絶対年度と誤認）を
  修正し、doc_id単位（1行=1書類内の1項目）に変更した。詳細はモデルSQL冒頭コメント
  参照。
- **`cleansed__edinet__report_periods`を新設**（2026-09-19）: 銘柄×期の期間
  ディメンション。`filer_category`（company/fund/reit/infra_fund/etf）・
  `regime`（旧制度/移行期/新制度）の判定を持つ。
- **mart層の拡充**: `mart__edinet__financial_indicators`（2026-09-20、J-GAAP/IFRS/
  US GAAPをcoalesce）を皮切りに、`mart__jpx_edinet__disclosed_fundamentals`・
  `mart__jpx_edinet__daily_valuation_indicators`（2026-09-26、JPX株価×開示指標）・
  `mart__jpx_edinet__quarterly_fundamentals`（J-Quants比較検証用）を追加。J-Quants
  API（第三者データ）との比較検証でF1〜F4の特殊処理（企業固有パッチ）を発見・
  修正した（詳細は`docs/jquants_validation.md`・`docs/mart_indicators.md`参照）。
- **REIT（投資法人）対応完了**（2026-10-11〜13）: EDINET書類一覧APIの`secCode`が
  REITで常にNULLになる構造的欠落を`formCode`（`07B`/`10B`始まり）で回避。絶対額系
  指標はEDINETの生タグ（`jppfs_cor:`、通常企業と共通）、1口当たり系（EPS/BPS相当）
  はEDINETに構造化タグが存在しないため`ir-disclosure-dl`経由のTDnet/JPX決算短信
  iXBRL（`tse-re-t:`）から取得する設計にした。`cleansed__ir_disclosure__reit_ixbrl_facts`・
  `intermediate__edinet__reit_financial_facts`・`mart__edinet__reit_financial_indicators`
  を新設。sec_codeはDEIタグで補完（REITもsecCode常時NULLのため、2026-10-13修正）。
  本番デプロイ済み。
- **ETF（上場投資信託）対応完了**（2026-10-17〜18）: REITと異なり`formCode`が他の
  国内投資信託と完全に共有され判別不能、DEI証券コードタグも運用会社依存で
  信頼できないため、`finance-lake`側でJPX公式ETF一覧とのファンド名称照合により
  確認した`edinet_etf_fund_issuers`seed（332件、複合キー`(edinet_code, fund_code)`）
  で判定する。1口当たり指標はTDnet/JPX決算短信がPDFのみ（iXBRL化されていない）かつ
  ETFの市場価格が基準価額とほぼ一致する性質があるため見送り、絶対額指標のみを
  対象にした。`intermediate__edinet__etf_financial_facts`・
  `mart__edinet__etf_financial_indicators`を新設。実装中に`raw__edinet_document_index`の
  JSON型推論バグ（`fund_code`等5列がDuckDBの`read_json_auto`で`JSON`型に推論され
  `cast(... as varchar)`がJSON直列化された値を返す不具合）を発見・修正した。
  `feature/etf-cleansed-layer`ブランチにpush済み、`dev`への昇格は未実施。
- **`cleansed__edinet__facts`のfull-refresh時メモリ問題への対処**（2026-10-18）:
  上記参照、「既知の制約」（`README.md`）参照。

## 依存

Python 3.12。外部依存は `transform/` のみ: `dbt-core` / `dbt-duckdb` / `duckdb` /
`pyarrow` / `prefect` / `boto3` / `pdfplumber`（JPX PDF のテキスト化、`py.typed`
同梱のため追加 stub 不要）/ `beautifulsoup4`（mufg-corporate-actionsのHTMLテーブル
解析、2026-09-16追加。標準ライブラリのみでの実装は現実的でないための例外。
lxml等のC拡張依存は増やさず`html.parser`バックエンドのみ使用）。開発は
`requirements-dev.txt`（+ `mypy` / `pytest` / `boto3-stubs` / `pyarrow-stubs`）。
`mypy --strict` + `pytest`。CSV 抽出ロジック（`transform/fdw/edinet_csv_fdw.py`）は
標準ライブラリのみ。

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

jpx 04:01:00 → edinet 04:01:30 → mufg-corporate-actions 04:01:40（週次・月曜のみ）
→ **transform 04:01:45** → shutdown 04:02:00。
`finance-dwh-transform.service` は `After=` で lake 両ジョブの完了を待ち、共有シャット
ダウンは `After=` で transform 完了を待つ。`finance-lake/systemd/
finance-lake-shutdown.service` の `After=` には `finance-dwh-transform.service` が
既に追記済み（`finance-dwh-postgres.service` への参照はそもそも含まれていないため、
今回の Postgres 撤去にあたって finance-lake 側の変更は不要だった）。

## 現状（2026-10-18 時点）

- DuckDB/Parquet化・JPX形式B/C・mufg-corporate-actions・REIT・ETF対応まで実装・
  本番デプロイ済み（REIT・ETFは`feature/etf-cleansed-layer`のみ未昇格、上記参照）。
  cleansed層は`cleansed__edinet__facts`のみincremental、他は全量rebuild。
- mart層: `mart__edinet__financial_indicators`（事業会社）・
  `mart__edinet__reit_financial_indicators`（REIT）・
  `mart__edinet__etf_financial_indicators`（ETF）・
  `mart__jpx_edinet__disclosed_fundamentals`/`daily_valuation_indicators`/
  `quarterly_fundamentals`が稼働中。指標定義一覧は`docs/mart_indicators.md`。
- 未了: ETF対応（`feature/etf-cleansed-layer`）のPR作成・レビュー・
  `dev`→`release`→`main`への昇格。`cleansed__edinet__facts`の`max_file_date`
  チャンク分割方式の運用手順の整備（現状は手動バッチ実行、スクリプト化は未着手）。
  jpx のファイル目録（raw層）・形式A・セクション1以外の取引種別は未対応。
