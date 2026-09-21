-- 銘柄(edinet_code)×期(fiscal_year, period_type)ごとの主要財務指標（案B）。
-- cleansed__edinet__report_periods（期間ディメンション）と、会計基準(J-GAAP/IFRS/
-- US GAAP)ごとに指標を抽出したintermediateモデル3種を doc_id で結合して構築する。
--
-- 会計基準の混在(J-GAAP/IFRS/US GAAP)への対応(2026-09-21、intermediate層導入):
--   当初は1つのmartモデル内で全会計基準の候補item_nameを1つのcoalesce chainに
--   混在させていたが、これが原因のバグが複数見つかった（日本ハム(2282)等:
--   IFRS採用企業でも個別財務諸表は通常J-GAAPのまま作成されるため、1つの書類に
--   J-GAAP名タグとIFRS名タグが混在しうる。優先順位だけで選ぶと会計基準と無関係な
--   値を誤って採用してしまう）。
--
--   会計基準ごとに独立したintermediateモデル
--   （intermediate__edinet__{jgaap,ifrs,usgaap}_financial_facts）で指標を抽出し、
--   このmartは「企業自身のaccounting_standard(DEI由来)を優先し、無ければ他基準へ
--   フォールバックする」だけの薄い結合層にする。連結優先・個別フォールバックの
--   判定（has_consolidated考慮）は各intermediateモデル側で完結しており、この
--   martはそれを意識する必要が無い。
--
-- ordinary_income/capital/payout_ratioはIFRS/US GAAPに対応概念が無いため、
-- それらの会計基準を採用する企業ではNULLになる（意図した挙動、各intermediate
-- モデル側でNULL固定）。
--
-- 訂正報告書対応: report_periodsは書類(doc_id)粒度で訂正報告書も別行として保持する
-- ため、同一(edinet_code, fiscal_year, period_type)にdoc_idが複数あり得る。
-- submit_date_time最新の1件に絞ってから指標を結合する。

{{ config(
    materialized='external',
    location=env_var('MART_ROOT', '/data/mart') ~ '/edinet_financial_indicators.parquet',
    format='parquet'
) }}

with periods as (
    select
        *,
        row_number() over (
            partition by edinet_code, fiscal_year, period_type
            order by submit_date_time desc
        ) as _rn
    from {{ ref('cleansed__edinet__report_periods') }}
    where filer_category = 'company' and fiscal_year is not null
),

target_periods as (
    select
        doc_id, edinet_code, sec_code, filer_name, fiscal_year, period_type,
        period_start, period_end, regime
    from periods
    where _rn = 1
),

dei as (
    select * from {{ ref('intermediate__edinet__dei_facts') }}
),

combined as (
    select
        tp.*,
        d.accounting_standard,
        d.has_consolidated,
        jg.total_assets as jg_total_assets, ifrs.total_assets as ifrs_total_assets, us.total_assets as us_total_assets,
        jg.net_assets as jg_net_assets, ifrs.net_assets as ifrs_net_assets, us.net_assets as us_net_assets,
        jg.equity_ratio as jg_equity_ratio, ifrs.equity_ratio as ifrs_equity_ratio, us.equity_ratio as us_equity_ratio,
        jg.ordinary_income as jg_ordinary_income, ifrs.ordinary_income as ifrs_ordinary_income, us.ordinary_income as us_ordinary_income,
        jg.net_income as jg_net_income, ifrs.net_income as ifrs_net_income, us.net_income as us_net_income,
        jg.sales as jg_sales, ifrs.sales as ifrs_sales, us.sales as us_sales,
        jg.eps as jg_eps, ifrs.eps as ifrs_eps, us.eps as us_eps,
        jg.bps as jg_bps, ifrs.bps as ifrs_bps, us.bps as us_bps,
        jg.roe as jg_roe, ifrs.roe as ifrs_roe, us.roe as us_roe,
        jg.per as jg_per, ifrs.per as ifrs_per, us.per as us_per,
        jg.operating_cf as jg_operating_cf, ifrs.operating_cf as ifrs_operating_cf, us.operating_cf as us_operating_cf,
        jg.investing_cf as jg_investing_cf, ifrs.investing_cf as ifrs_investing_cf, us.investing_cf as us_investing_cf,
        jg.financing_cf as jg_financing_cf, ifrs.financing_cf as ifrs_financing_cf, us.financing_cf as us_financing_cf,
        jg.capital as jg_capital, ifrs.capital as ifrs_capital, us.capital as us_capital,
        jg.payout_ratio as jg_payout_ratio, ifrs.payout_ratio as ifrs_payout_ratio, us.payout_ratio as us_payout_ratio
    from target_periods tp
    left join dei d on d.doc_id = tp.doc_id
    left join {{ ref('intermediate__edinet__jgaap_financial_facts') }} jg on jg.doc_id = tp.doc_id
    left join {{ ref('intermediate__edinet__ifrs_financial_facts') }} ifrs on ifrs.doc_id = tp.doc_id
    left join {{ ref('intermediate__edinet__usgaap_financial_facts') }} us on us.doc_id = tp.doc_id
)

select
    doc_id,
    edinet_code,
    sec_code,
    filer_name,
    fiscal_year,
    period_type,
    period_start,
    period_end,
    regime,
    accounting_standard,
    has_consolidated,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_total_assets end,
        case when accounting_standard = 'Japan GAAP' then jg_total_assets end,
        case when accounting_standard = 'US GAAP' then us_total_assets end,
        ifrs_total_assets, jg_total_assets, us_total_assets
    ) as total_assets,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_net_assets end,
        case when accounting_standard = 'Japan GAAP' then jg_net_assets end,
        case when accounting_standard = 'US GAAP' then us_net_assets end,
        ifrs_net_assets, jg_net_assets, us_net_assets
    ) as net_assets,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_equity_ratio end,
        case when accounting_standard = 'Japan GAAP' then jg_equity_ratio end,
        case when accounting_standard = 'US GAAP' then us_equity_ratio end,
        ifrs_equity_ratio, jg_equity_ratio, us_equity_ratio
    ) as equity_ratio,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_ordinary_income end,
        case when accounting_standard = 'Japan GAAP' then jg_ordinary_income end,
        case when accounting_standard = 'US GAAP' then us_ordinary_income end,
        ifrs_ordinary_income, jg_ordinary_income, us_ordinary_income
    ) as ordinary_income,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_net_income end,
        case when accounting_standard = 'Japan GAAP' then jg_net_income end,
        case when accounting_standard = 'US GAAP' then us_net_income end,
        ifrs_net_income, jg_net_income, us_net_income
    ) as net_income,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_sales end,
        case when accounting_standard = 'Japan GAAP' then jg_sales end,
        case when accounting_standard = 'US GAAP' then us_sales end,
        ifrs_sales, jg_sales, us_sales
    ) as sales,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_eps end,
        case when accounting_standard = 'Japan GAAP' then jg_eps end,
        case when accounting_standard = 'US GAAP' then us_eps end,
        ifrs_eps, jg_eps, us_eps
    ) as eps,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_bps end,
        case when accounting_standard = 'Japan GAAP' then jg_bps end,
        case when accounting_standard = 'US GAAP' then us_bps end,
        ifrs_bps, jg_bps, us_bps
    ) as bps,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_roe end,
        case when accounting_standard = 'Japan GAAP' then jg_roe end,
        case when accounting_standard = 'US GAAP' then us_roe end,
        ifrs_roe, jg_roe, us_roe
    ) as roe,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_per end,
        case when accounting_standard = 'Japan GAAP' then jg_per end,
        case when accounting_standard = 'US GAAP' then us_per end,
        ifrs_per, jg_per, us_per
    ) as per,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_operating_cf end,
        case when accounting_standard = 'Japan GAAP' then jg_operating_cf end,
        case when accounting_standard = 'US GAAP' then us_operating_cf end,
        ifrs_operating_cf, jg_operating_cf, us_operating_cf
    ) as operating_cf,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_investing_cf end,
        case when accounting_standard = 'Japan GAAP' then jg_investing_cf end,
        case when accounting_standard = 'US GAAP' then us_investing_cf end,
        ifrs_investing_cf, jg_investing_cf, us_investing_cf
    ) as investing_cf,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_financing_cf end,
        case when accounting_standard = 'Japan GAAP' then jg_financing_cf end,
        case when accounting_standard = 'US GAAP' then us_financing_cf end,
        ifrs_financing_cf, jg_financing_cf, us_financing_cf
    ) as financing_cf,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_capital end,
        case when accounting_standard = 'Japan GAAP' then jg_capital end,
        case when accounting_standard = 'US GAAP' then us_capital end,
        ifrs_capital, jg_capital, us_capital
    ) as capital,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_payout_ratio end,
        case when accounting_standard = 'Japan GAAP' then jg_payout_ratio end,
        case when accounting_standard = 'US GAAP' then us_payout_ratio end,
        ifrs_payout_ratio, jg_payout_ratio, us_payout_ratio
    ) as payout_ratio
from combined
