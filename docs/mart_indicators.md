# mart層 指標定義一覧（2026-09-25時点）

mart層の各指標について、どのような計算式・ソースで集計しているかをまとめる。
実装が変わったらこのドキュメントも合わせて更新すること（正とするのは常に
`dbt/models/mart/*.sql`・`dbt/models/intermediate/*.sql`本体で、このドキュメントは
その要約）。

## mart__edinet__financial_indicators

grain: `(edinet_code, fiscal_year, period_type)`。訂正報告書がある場合は
`submit_date_time`最新の1件を採用。

### 会計基準またぎの共通ルール

以下の指標は、会計基準ごとに独立したintermediateモデル
（`intermediate__edinet__{jgaap,ifrs,usgaap}_financial_facts`）で個別に抽出した値を、
martで次のルールにより1列へ統合する。

1. 企業自身の`accounting_standard`（DEI「会計基準、DEI」由来）と一致する会計基準の値を優先
2. 無ければ他の会計基準の値へフォールバック（IFRS→J-GAAP→US GAAPの順）

各intermediateモデル内では、さらに次のルールで連結/個別・通貨単位を判定する。

- **連結優先・個別フォールバック**: 連結値があれば採用。無い場合、`has_consolidated=false`
  （DEI「連結決算の有無、DEI」由来。連結決算を作成しない企業）の場合のみ個別値へ
  フォールバックする。連結決算を作成する企業の個別値は、連結の代替として比較可能でない
  ケースがあるため対象外（日本ハム(2282)の実例で判明、詳細は
  `intermediate__edinet__jgaap_financial_facts.sql`コメント参照）。
- **通貨単位**: 金額系指標は`unit_id='JPY'`（1株当たり指標は`JPYPerShares`）を必須条件と
  する。一部IFRS採用企業はUSD建てでタグ付けするため（三井海洋開発(6269)の実例）、これを
  満たさない値は採用しない（無ければNULL、他の会計基準側でJPY建ての値が見つかればそちらが
  採用される）。比率系（`equity_ratio`/`roe`/`per`/`payout_ratio`）は無単位(pure)のため
  この条件は適用しない。

### 指標一覧

| 列名 | 説明 | 単位 | J-GAAP候補item_name（経営指標等） | IFRS候補item_name | US GAAP候補item_name |
|---|---|---|---|---|---|
| `total_assets` | 総資産額 | 円 | 総資産額 | 総資産額（IFRS） | 総資産額（US GAAP） |
| `net_assets` | 純資産額 | 円 | 純資産額 | 親会社の所有者に帰属する持分（IFRS） | 純資産額（US GAAP） |
| `equity_ratio` | 自己資本比率 | 無単位(pure) | 自己資本比率 | 親会社所有者帰属持分比率（IFRS） | 自己資本比率（US GAAP） |
| `ordinary_income` | 経常利益 | 円 | 経常利益又は経常損失（△） | 対応概念なし（常にNULL） | 対応概念なし（常にNULL） |
| `net_income` | 当期純利益 | 円 | 親会社株主に帰属する当期純利益又は…（△）→無ければ当期純利益又は当期純損失（△）の順でcoalesce | 当期利益又は当期損失（△）：親会社の所有者に帰属（IFRS） | 当社株主に帰属する純利益又は純損失（△）（US GAAP） |
| `sales` | 売上高 | 円 | 売上高→営業収益→経常収益→営業収入→営業総収入の順でcoalesce | 売上収益（IFRS）→売上収益の順でcoalesce | 売上高（US GAAP） |
| `eps` | 1株当たり当期純利益 | 円/株(JPYPerShares) | １株当たり当期純利益又は当期純損失（△） | 基本的１株当たり利益又は損失（△）（IFRS） | 基本的１株当たり当社株主に帰属する利益又は損失（△）（US GAAP） |
| `bps` | 1株当たり純資産額 | 円/株(JPYPerShares) | １株当たり純資産額 | １株当たり親会社所有者帰属持分（IFRS） | １株当たり株主資本（US GAAP） |
| `roe` | 自己資本利益率 | 無単位(pure) | 自己資本利益率 | 親会社所有者帰属持分利益率（IFRS） | 株主資本利益率（US GAAP） |
| `per` | 株価収益率（企業自己申告） | 無単位(pure) | 株価収益率 | 株価収益率（IFRS） | 株価収益率（US GAAP） |
| `operating_cf` | 営業活動によるキャッシュ・フロー | 円 | 営業活動によるキャッシュ・フロー | 同左（IFRS） | 同左（US GAAP） |
| `investing_cf` | 投資活動によるキャッシュ・フロー | 円 | 投資活動によるキャッシュ・フロー | 同左（IFRS） | 同左（US GAAP） |
| `financing_cf` | 財務活動によるキャッシュ・フロー | 円 | 財務活動によるキャッシュ・フロー | 同左（IFRS） | 同左（US GAAP） |
| `capital` | 資本金 | 円 | 資本金 | 対応概念なし（常にNULL） | 対応概念なし（常にNULL） |
| `payout_ratio` | 配当性向 | 無単位(pure) | 配当性向 | 対応概念なし（常にNULL） | 対応概念なし（常にNULL） |
| `diluted_eps` | 潜在株式調整後EPS（希薄化後EPS） | 円/株(JPYPerShares) | 潜在株式調整後１株当たり当期純利益 | 希薄化後１株当たり利益又は損失（△）（IFRS） | 希薄化後１株当たり当社株主に帰属する利益又は損失（△）（US GAAP） |
| `comprehensive_income` | 包括利益 | 円 | 包括利益 | 当期包括利益：親会社の所有者に帰属（IFRS）→無ければ当期包括利益（IFRS） | 当社株主に帰属する包括利益（US GAAP）→無ければ包括利益（US GAAP） |
| `cash_and_equivalents` | 現金及び現金同等物の残高 | 円 | 現金及び現金同等物の残高 | 現金及び現金同等物（IFRS） | 現金及び現金同等物（US GAAP） |

`shares_outstanding`（発行済株式総数、普通株式、単位: shares）のみ他の指標と抽出元が異なる。

1. **優先**: `intermediate__edinet__dei_facts`が「株式の総数等」開示（`事業年度末現在発行数
   （株）、発行済株式、株式の総数等`、`context_id='FilingDateInstant_OrdinaryShareMember'`）
   から抽出した値。会計基準・連結決算スコープに依存しない企業単位の法的事実のため、3つの
   intermediateモデルではなくdei_factsに一元化している（カバレッジ77,666書類）。
2. **フォールバック**: 上記が無い場合のみ、経営指標等ベース（`発行済株式総数（普通株式）、
   経営指標等`、会計基準別モデルで連結優先・個別フォールバック抽出、カバレッジ35,045書類）
   を会計基準またぎのcoalesceで使用。

`accounting_standard`・`has_consolidated`は`intermediate__edinet__dei_facts`から
そのまま引き継ぐ（DEI「会計基準、DEI」「連結決算の有無、DEI」）。

## mart__jpx_edinet__daily_valuation_indicators

grain: `(jpx_code, file_date)`。銘柄×期grainだった`mart__jpx_edinet__valuation_indicators`
（決算期末日/直近営業日という性質の異なる2基準が1行に同居し解釈が難しいと判明）を置き換えた
（2026-09-25、ユーザー判断）。日次grainにすることで「最新」は単に最終行になり、決算期末日
時点のスナップショットも日次系列から該当日を1行引くだけで得られる。任意日の推移も取得できる。

銘柄コードは`mart__edinet__financial_indicators.sec_code`（EDINET、5桁）の末尾1桁を除去して
`intermediate__jpx__daily_prices_adjusted`の`jpx_code`（JPX、4桁）と結合する。

### 「参照可能な最新の開示」の判定

決算期末日ではなく**開示日**（`submit_date_time`）を基準にする。決算期末日を基準にすると、
実際にはまだ開示されていない数値を先読みしてしまう（実データで通期は約87〜90日、四半期でも
約42〜43日のディスクロージャーラグを確認済み）。`submit_date_time <= file_date`を満たす
直近1件をASOF JOINで採用する。

### 分割・併合をまたぐ期間のEPS/BPS/発行済株式数の調整

株価（`close`）は`intermediate__jpx__daily_prices_adjusted`の累積調整係数
（`cum_adjustment_factor`）で調整済みだが、EPS/BPS/`shares_outstanding`は開示時点の株式数の
まま。ある開示（`period_end`時点）の後に分割・併合が起きると、次の開示までの間は「調整後
株価 ÷ 未調整EPS」で計算が歪む。これを解消するため、開示の決算期末日時点の累積調整係数
（`period_end_cum_adj`、`period_end`以前で直近の取引日の値をASOF JOINで取得）と、対象取引日
自身の累積調整係数（`file_date_cum_adj`）の比を使う。

```
adj_ratio = period_end_cum_adj / file_date_cum_adj
eps_adjusted = eps × adj_ratio
bps_adjusted = bps × adj_ratio
shares_outstanding_adjusted = shares_outstanding ÷ adj_ratio   -- 株数は逆方向
```

分割・併合をまたいでいなければ`period_end_cum_adj = file_date_cum_adj`となり`adj_ratio = 1`
（無調整）。`sales`は企業単位の総額指標で株式数に依存しないため調整不要（開示値をそのまま
使う）。

### 指標一覧

| 列名 | 説明 | 計算式 |
|---|---|---|
| `close` | 当日の終値（raw、無調整） | `coalesce(pm_close, am_close)`（後場引け優先） |
| `adj_ratio` | 分割・併合調整比率 | `period_end_cum_adj / file_date_cum_adj` |
| `eps_adjusted` / `bps_adjusted` | 調整後EPS/BPS | `eps or bps × adj_ratio` |
| `shares_outstanding_adjusted` | 調整後発行済株式数 | `shares_outstanding ÷ adj_ratio` |
| `pbr` | 株価純資産倍率 | `close / bps_adjusted` |
| `per` | 実勢PER | `close / eps_adjusted` |
| `market_cap` | 時価総額 | `close × shares_outstanding_adjusted` |
| `psr` | 株価売上高倍率 | `market_cap / sales` |
| `earnings_yield` | 株式益回り（PERの逆数） | `eps_adjusted / close` |

`eps`/`bps`/`sales`/`shares_outstanding`（無調整の開示値そのまま）も参照用に保持している。
分母が0またはNULLの場合は該当指標もNULLになる。

## 検証テスト

- `assert_mart__jpx_edinet__daily_valuation_indicators_deduplicated`: `(jpx_code, file_date)`
  の一意性を検証する。
- `assert_mart__jpx_edinet__daily_valuation_indicators_per_matches_disclosed`（severity=warn、
  相対誤差2%）: 企業自己申告PERは決算期末日の終値を基準に計算されているため（日次martの
  「開示日基準」の行とは直接比較できない）、決算期末日時点の終値を別途ASOF JOINで求めて
  突合する。結合キー・調整係数ロジックの健全性チェックを兼ねる。
- `assert_mart__edinet__financial_indicators_*`（詳細は`docs/mart_validation.md`参照）:
  `eps`/`bps`の逆算検証（`shares_outstanding`を分母に使用）、`sales`のYTD単調性、
  `total_assets >= net_assets`等、ファンダメンタルズ側の内部整合性テスト。

## 今後の拡張候補（未実装）

- 通期のみを「最新開示」の基準にするか、四半期・半期も含めるかは、現状は問わず直近の開示を
  採用する設計（四半期EPSは期首からの累計値のため、Q1時点のPERが通期基準より高く出る等の
  性質を前提とする）。用途に応じて通期限定版が必要になった場合は別途検討する。
