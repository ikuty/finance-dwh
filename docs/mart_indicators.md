# mart層 指標定義一覧（2026-10-11時点）

mart層の各指標について、どのような計算式・ソースで集計しているかをまとめる。
実装が変わったらこのドキュメントも合わせて更新すること（正とするのは常に
`dbt/models/mart/*.sql`・`dbt/models/intermediate/*.sql`本体で、このドキュメントは
その要約。詳細な根拠・実機確認の経緯は各SQLのコメント、またはJ-Quants比較検証の
経緯は`docs/jquants_validation.md`を参照）。

## mart__edinet__financial_indicators

grain: `(edinet_code, fiscal_year, period_type)`。訂正報告書がある場合は
`submit_date_time`最新の1件を採用。

### 基本ルール

| ルール | 内容 |
|---|---|
| 会計基準の優先順位 | 企業自身の`accounting_standard`（DEI由来）を優先。無ければIFRS→J-GAAP→US GAAPの順でフォールバック |
| 連結/個別 | 連結値を優先。個別値へのフォールバックは`has_consolidated=false`の企業のみ（例外は下表） |
| 通貨単位 | 金額系は`unit_id='JPY'`必須（1株当たり指標は`JPYPerShares`）。比率系（pure）は対象外 |
| 連結/個別ルールの例外 | `dividend_per_share`は常に個別値へフォールバック（連結決算企業でも経営指標等表では個別コンテキストのみタグ付けされるのが通例、99.9%）。`shares_outstanding`は下記専用ロジック |

### 指標一覧

| 列名 | 説明 | 単位 | J-GAAP候補item_name（経営指標等） | IFRS候補item_name | US GAAP候補item_name | 特殊処理 |
|---|---|---|---|---|---|---|
| `total_assets` | 総資産額 | 円 | 総資産額 | 総資産額（IFRS） | 総資産額（US GAAP） | - |
| `net_assets` | 純資産額 | 円 | 純資産額 | 親会社の所有者に帰属する持分（IFRS） | 純資産額（US GAAP） | - |
| `equity_ratio` | 自己資本比率 | pure | 自己資本比率 | 親会社所有者帰属持分比率（IFRS） | 自己資本比率（US GAAP） | - |
| `ordinary_income` | 経常利益 | 円 | 経常利益又は経常損失（△） | 対応概念なし | 対応概念なし | - |
| `net_income` | 当期純利益 | 円 | 親会社株主に帰属する当期純利益→無ければ当期純利益の順でcoalesce | 当期利益：親会社の所有者に帰属（IFRS） | 当社株主に帰属する純利益（US GAAP） | F1 |
| `sales` | 売上高 | 円 | 売上高→営業収益→経常収益→営業収入→営業総収入の順でcoalesce | 売上収益（IFRS）→売上収益の順でcoalesce | 売上高（US GAAP） | F1, F2, F3 |
| `eps` | 1株当たり当期純利益（実績） | 円/株 | １株当たり当期純利益又は当期純損失（△） | 基本的１株当たり利益（IFRS） | 基本的１株当たり利益（US GAAP） | F1, F4 |
| `bps` | 1株当たり純資産額 | 円/株 | １株当たり純資産額 | １株当たり親会社所有者帰属持分（IFRS） | １株当たり株主資本（US GAAP） | F4 |
| `roe` | 自己資本利益率 | pure | 自己資本利益率 | 親会社所有者帰属持分利益率（IFRS） | 株主資本利益率（US GAAP） | - |
| `per` | 株価収益率（実績EPSベース、詳細後述） | pure | 株価収益率 | 株価収益率（IFRS） | 株価収益率（US GAAP） | - |
| `operating_cf` | 営業活動CF | 円 | 営業活動によるキャッシュ・フロー | 同左（IFRS） | 同左（US GAAP） | - |
| `investing_cf` | 投資活動CF | 円 | 投資活動によるキャッシュ・フロー | 同左（IFRS） | 同左（US GAAP） | - |
| `financing_cf` | 財務活動CF | 円 | 財務活動によるキャッシュ・フロー | 同左（IFRS） | 同左（US GAAP） | - |
| `capital` | 資本金 | 円 | 資本金 | 対応概念なし | 対応概念なし | - |
| `payout_ratio` | 配当性向 | pure | 配当性向 | 対応概念なし | 対応概念なし | - |
| `diluted_eps` | 潜在株式調整後EPS | 円/株 | 潜在株式調整後１株当たり当期純利益 | 希薄化後１株当たり利益（IFRS） | 希薄化後１株当たり利益（US GAAP） | F1(IFRS/US GAAPのみ), F4(J-GAAPのみ) |
| `comprehensive_income` | 包括利益 | 円 | 包括利益 | 当期包括利益：親会社の所有者に帰属（IFRS）→無ければ当期包括利益 | 当社株主に帰属する包括利益（US GAAP）→無ければ包括利益 | F1(IFRS/US GAAPのみ) |
| `cash_and_equivalents` | 現金及び現金同等物残高 | 円 | 現金及び現金同等物の残高 | 現金及び現金同等物（IFRS） | 現金及び現金同等物（US GAAP） | - |
| `dividend_per_share` | 1株当たり配当額 | 円/株 | １株当たり配当額 | 同左（3会計基準共通item_name） | 同左 | - |

`shares_outstanding`（発行済株式総数、普通株式、shares）は抽出元が異なる:

| 優先順位 | ソース | 判定条件 | カバレッジ |
|---|---|---|---|
| 1 | `intermediate__edinet__dei_facts`「株式の総数等」 | 会計基準・連結スコープに依存しない企業単位の法的事実 | 77,666書類 |
| 2 | 経営指標等「発行済株式総数（普通株式）」（会計基準別モデル、連結優先・個別フォールバック） | 1が無い場合のみ | 35,045書類 |

`accounting_standard`・`has_consolidated`は`intermediate__edinet__dei_facts`から
そのまま引き継ぐ。

### 特殊処理一覧（企業固有パッチ・構造的な例外ルール）

| コード | 処理内容 | 発動条件・対象 | 実装箇所 |
|---|---|---|---|
| F1 | 四半期報告書で「単独四半期(CurrentQuarter)」と「期初来累計(CurrentYTD)」の両コンテキストが共存する場合、累計を優先（単純な`max()`だと数値が大きい方を機械的に選び、損益が四半期で符号反転する企業で誤る） | 全企業（四半期報告書のみ）。対象列は指標一覧表の「特殊処理」列を参照 | 各`intermediate__edinet__{jgaap,ifrs,usgaap}_financial_facts.sql` |
| F2 | 裸の`営業収入`タグ(`jppfs_cor:OperatingRevenue2`、経営指標等の接尾辞なし)を売上高に加算 | seed `edinet_sales_operating_revenue_addback`（17社、個別確認済み） | `intermediate__edinet__jgaap_financial_facts.sql` |
| F3 | `、営業活動による収益`で終わるタグ群（複数件ありうる）の合計を売上高に加算 | seed `edinet_sales_sibling_revenue_addback`（2社、個別確認済み） | `intermediate__edinet__jgaap_financial_facts.sql` |
| F4 | `\|値\|>100万`の異常値を近似値に差し替え（提出者側のXBRL生成不具合と推定）。eps=`net_income÷(発行済株式数−自己株式数)`、bps=`(net_assets−非支配株主持分)÷(発行済株式数−自己株式数)`、diluted_eps=フォールバック後のepsと同値 | seed `edinet_eps_bps_implausible_fallback`（4社、個別確認済み）かつ異常値の期のみ | `intermediate__edinet__jgaap_financial_facts.sql` |

F2・F3は同じ「加算対象は`売上高、経営指標等`タグ自体が存在する期のみ」という
ガード（`net_sales_tag_value is not null`）を共有する。経営指標等が既に`営業収益`
等の完全な集計値にフォールバックしている期に加算すると二重計上になるため
（髙島屋(8233)の半期報告書で実機確認）。

F1〜F4はいずれも、J-Quants API（第三者データ）との比較検証で発見した。詳細な
調査過程・反例企業・全量検証の結果は`docs/jquants_validation.md`参照。

## mart__jpx_edinet__disclosed_fundamentals

grain: `(jpx_code, doc_id)`（実質的にはdoc_id単位、jpx_codeは各doc_idに対して一意に
決まる属性）。`mart__jpx_edinet__daily_valuation_indicators`との責務分離のため新設:
株価(`close`)と連動せず開示が変わるまで一定の指標を、日次grainに冗長に持たせず
この開示grainに一元化する。`mart__edinet__financial_indicators`は純粋なEDINET開示
martとして維持し、JPX価格調整の概念を持ち込まない。

銘柄コードは`mart__edinet__financial_indicators.sec_code`（EDINET、5桁）の末尾1桁を
除去して`jpx_code`（JPX、4桁）とする。

| 列名 | 説明 |
|---|---|
| `doc_id` | 根拠となった書類のID（一意） |
| `jpx_code` | 銘柄コード（JPX、4桁） |
| `edinet_code`/`sec_code`/`filer_name`/`fiscal_year`/`period_type`/`period_end`/`submit_date_time` | `mart__edinet__financial_indicators`と同一 |
| `eps`/`bps`/`sales`/`shares_outstanding`/`dividend_per_share` | 開示された値そのまま（分割・併合調整前）。`mart__edinet__financial_indicators`と同一 |
| `period_end_cum_adj` | 決算期末日時点（以前で直近の取引日）の累積調整係数。`intermediate__jpx__daily_prices_adjusted.cum_adjustment_factor`をASOF JOINで取得 |

## mart__jpx_edinet__daily_valuation_indicators

grain: `(jpx_code, file_date)`。株価(`close`)と連動して日次で変化する指標のみを持つ
（開示ベースの値は`mart__jpx_edinet__disclosed_fundamentals`へ分離）。`doc_id`で
結合すれば開示メタデータ・無調整値を取得できる。

### 「参照可能な最新の開示」の判定

決算期末日ではなく**開示日**（`submit_date_time`）を基準にする（決算期末日基準だと
未開示の数値を先読みしてしまう。実データで通期ラグ約87〜90日、四半期約42〜43日）。
`submit_date_time <= file_date`を満たす直近1件をASOF JOINで採用する。

### 分割・併合調整

株価は`intermediate__jpx__daily_prices_adjusted`の累積調整係数で調整済みだが、
EPS/BPS/`shares_outstanding`は開示時点の株式数のまま。開示の決算期末日時点の
累積調整係数(`period_end_cum_adj`)と対象取引日自身の係数(`file_date_cum_adj`)の
比で補正する。

| 計算式 |
|---|
| `adj_ratio = period_end_cum_adj / file_date_cum_adj` |
| `eps_adjusted = eps × adj_ratio` |
| `bps_adjusted = bps × adj_ratio` |
| `shares_outstanding_adjusted = shares_outstanding ÷ adj_ratio`（株数は逆方向） |

分割・併合をまたいでいなければ`adj_ratio = 1`（無調整）。`sales`は株式数に依存しない
ため調整不要（開示値そのまま）。

### 指標一覧

| 列名 | 説明 | 計算式 |
|---|---|---|
| `close` | 当日終値（raw、無調整） | `coalesce(pm_close, am_close)`（後場引け優先） |
| `adj_ratio` | 分割・併合調整比率 | 上表参照 |
| `eps_adjusted` / `bps_adjusted` | 調整後EPS/BPS | `eps or bps × adj_ratio` |
| `shares_outstanding_adjusted` | 調整後発行済株式数 | `shares_outstanding ÷ adj_ratio` |
| `pbr` | 株価純資産倍率 | `close / bps_adjusted` |
| `per` | 実勢PER | `close / eps_adjusted` |
| `market_cap` | 時価総額 | `close × shares_outstanding_adjusted` |
| `psr` | 株価売上高倍率 | `market_cap / sales` |
| `earnings_yield` | 株式益回り | `eps_adjusted / close` |
| `dividend_per_share_adjusted` | 調整後1株当たり配当額 | `dividend_per_share × adj_ratio` |
| `dividend_yield` | 配当利回り | `dividend_per_share_adjusted / close` |

分母が0またはNULLの場合は該当指標もNULLになる。

### 調整済み生値の逆算

無調整値そのものはこのモデルには無いが、`close`と各比率から代数的に逆算できる
（`close`≠0・各比率が非NULLの場合）。

| 逆算式 |
|---|
| `eps_adjusted = close / per` |
| `bps_adjusted = close / pbr` |
| `shares_outstanding_adjusted = market_cap / close` |
| `dividend_per_share_adjusted = dividend_yield × close` |

無調整の`adj_ratio`自体は`mart__jpx_edinet__disclosed_fundamentals`のraw `eps`と
`eps_adjusted / eps`で求められる。

### `per`・`earnings_yield`は「実績EPS」ベース

`eps`（延いては`per`・`earnings_yield`）はEDINET開示に基づく**確定済み実績値**で、
証券会社サイトでデフォルト表示される「予想PER」（会社予想EPS基準）とは異なる。

実機検証（3087 ドトール・日レスHD、株価3,210円時点）:

| サイト | 表示PER | 使用EPS（逆算） | 実際の値 | 基準 |
|---|---:|---:|---:|---|
| かぶたん | 17.9倍 | 179.3円 | 179.6円（会社予想） | 予想EPS |
| Yahoo!ファイナンス | 17.85倍 | 179.8円 | 179.6円（会社予想、UI明記） | 予想EPS |
| みんかぶ | 18.8倍 | 170.7円 | 170.7円（直近確定期実績） | **実績EPS** |

みんかぶの実績PER表示と一致（3210÷170.7≒18.81）。会社予想EPSはEDINET開示に
通常含まれないため、現状のデータソースからは予想PERを算出できない（未対応）。

## 検証テスト

| テスト | 検証内容 |
|---|---|
| `unique`/`not_null` on `mart__jpx_edinet__disclosed_fundamentals.doc_id` | 開示grainの一意性 |
| `assert_mart__jpx_edinet__daily_valuation_indicators_deduplicated` | `(jpx_code, file_date)`の一意性 |
| `assert_mart__jpx_edinet__daily_valuation_indicators_per_matches_disclosed`（warn、相対誤差2%） | 企業自己申告PER（決算期末日終値基準）との整合性。結合キー・調整係数ロジックの健全性チェック |
| `assert_mart__edinet__financial_indicators_*`（詳細は`docs/mart_validation.md`） | eps/bpsの逆算検証、salesのYTD単調性、`total_assets >= net_assets`等 |

## 今後の拡張候補（未実装）

- 通期のみを「最新開示」の基準にするか、四半期・半期も含めるかは、現状は問わず
  直近の開示を採用する設計（四半期EPSは期首からの累計値のため、Q1時点のPERが
  通期基準より高く出る等の性質を前提とする）。用途に応じて通期限定版が必要に
  なった場合は別途検討する。
