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

## mart__jpx_edinet__valuation_indicators

grain: `mart__edinet__financial_indicators`と同じ`(edinet_code, fiscal_year, period_type)`。
銘柄コードは`sec_code`（EDINET、5桁）の末尾1桁を除去して`intermediate__jpx__daily_prices`の
`code`（JPX、4桁）と結合する（末尾1桁は証券の種類を表す予備コード、0=普通株式）。

株価は2種類の基準日で参照する。いずれも`intermediate__jpx__daily_prices`のraw（分割・併合
未調整）終値（`coalesce(pm_close, am_close)`、後場引け優先）を使う。EPS/BPS自体が開示時点の
オリジナルな株式数ベースのため、split調整後（`daily_prices_adjusted`）ではなくrawを使うのが
整合的（自己申告`per`との突合検証で確認済み）。

- **決算期末日ベース**（`period_end_close`）: `file_date <= period_end`の中で最大の
  `file_date`（休業日なら直前営業日）の終値。ASOF JOINで算出。
- **直近営業日ベース**（列名`_latest`サフィックス）: 銘柄ごとの最新`file_date`の終値
  （`latest_close`）。

| 列名 | 説明 | 計算式 |
|---|---|---|
| `period_end_close_date` / `period_end_close` | 決算期末日ベースの参照日・終値 | （上記ルール参照、株価そのもの） |
| `latest_close_date` / `latest_close` | 直近営業日ベースの参照日・終値 | （上記ルール参照、株価そのもの） |
| `per_disclosed` | 企業自己申告PER | `mart__edinet__financial_indicators.per`をそのまま転記 |
| `pbr` | 株価純資産倍率（決算期末日ベース） | `period_end_close / bps` |
| `pbr_latest` | 株価純資産倍率（直近営業日ベース） | `latest_close / bps` |
| `per_computed` | 実勢PER（決算期末日ベース） | `period_end_close / eps` |
| `per_computed_latest` | 実勢PER（直近営業日ベース） | `latest_close / eps` |
| `market_cap` | 時価総額（決算期末日ベース） | `period_end_close × shares_outstanding` |
| `market_cap_latest` | 時価総額（直近営業日ベース） | `latest_close × shares_outstanding` |
| `psr` | 株価売上高倍率（決算期末日ベース） | `market_cap / sales` |
| `psr_latest` | 株価売上高倍率（直近営業日ベース） | `market_cap_latest / sales` |
| `earnings_yield` | 株式益回り（決算期末日ベース、PERの逆数） | `eps / period_end_close` |

`bps`/`eps`/`sales`/`shares_outstanding`はいずれも`mart__edinet__financial_indicators`から
そのまま引き継ぐ（＝上表の会計基準またぎのcoalesce後の値）。分母が0またはNULLの場合は
該当指標もNULLになる。

## 検証テスト

- `assert_mart__jpx_edinet__valuation_indicators_per_matches_disclosed`（severity=warn、
  相対誤差2%）: `per_disclosed`と`per_computed`を突合し、結合キー・日付ルールの健全性を
  継続的に検証する。
- `assert_mart__edinet__financial_indicators_*`（詳細は`docs/mart_validation.md`参照）:
  `eps`/`bps`の逆算検証（`shares_outstanding`を分母に使用）、`sales`のYTD単調性、
  `total_assets >= net_assets`等、ファンダメンタルズ側の内部整合性テスト。

## 今後の拡張候補（未実装、設計検討中）

- **日次PER/PBR**（`intermediate__jpx__daily_prices_adjusted`の全取引日に対し、その日
  参照可能な最新EPS/BPSで算出するmart）。設計上の主要論点（2026-09-25検討開始）:
  1. 「参照可能な最新」の基準は決算期末日ではなく**開示日**（`submit_date_time`）とする
     必要がある（決算期末日基準だと未開示の数値を先読みしてしまう。実データで通期は
     約87〜90日、四半期でも約42〜43日のラグを確認済み）。現状`mart__edinet__financial_
     indicators`に`submit_date_time`列が無いため、追加が必要。
  2. 分割・併合をまたぐ期間は、EPS/BPS側も`intermediate__jpx__daily_prices_adjusted`の
     `cum_adjustment_factor`を用いて同じ株式数基準に揃える必要がある（開示日時点の係数と
     対象取引日時点の係数の比率を掛け合わせる）。
  3. 通期のみを基準にするか、四半期・半期も「最新」に含めるか（四半期EPSは期首からの
     累計値のため、含める場合はQ1時点のPERが通期基準より高く出る等の性質を前提とする）。
