-- 銘柄(edinet_code)×期(period_end)ごとのREIT(投資法人)主要財務指標（毎回フル
-- rebuild、Parquet）。intermediate__edinet__reit_financial_facts（doc_id単位）を
-- 期（edinet_code×period_end）単位に集約する薄い層。
--
-- mart__edinet__financial_indicators（事業会社向け）とは別モデルにした理由（案B、
-- 2026-10-12決定）: REITは会計基準の混在（J-GAAP/IFRS/US GAAP）・fiscal_year/
-- regime（四半期報告書制度変更）の概念を持たず、事業会社向けmartの大半のロジック
-- （accounting_standard別coalesce等）がそもそも適用対象外になる。両者を1モデルに
-- 混在させるとNULL列ばかりの行が増え、事業会社向けの既存ロジックに手を入れる
-- リスクも生まれるため、既存モデルは一切変更せず完全に独立したモデルとする。
--
-- 訂正報告書対応: mart__edinet__financial_indicatorsと同じ「列単位のlatest-non-null」
-- 方式（doc_id/submit_date_time等のメタデータ列は最新書類を代表値とするが、個々の
-- 指標列はその書類に値が無ければ古い書類から遡る）。REITも訂正有報(130)・訂正半期
-- 報告書(170)が存在するため同じ問題が起きうる（実機確認、130が621件中68件程度）。
--
-- grainはedinet_code×period_end（事業会社のfiscal_year×period_typeに相当。REITは
-- 四半期の概念が無く、period_end自体が一意な「期」を表すため、period_typeを
-- grainに含める必要はない）。

{{ config(
    materialized='external',
    location=env_var('MART_ROOT', '/data/mart') ~ '/edinet_reit_financial_indicators.parquet',
    format='parquet'
) }}

with periods as (
    select
        doc_id, edinet_code, sec_code, filer_name, period_type,
        period_start, period_end, submit_date_time
    from {{ ref('cleansed__edinet__report_periods') }}
    where filer_category = 'reit' and period_end is not null
),

per_doc as (
    select
        p.doc_id,
        p.edinet_code,
        p.sec_code,
        p.filer_name,
        p.period_type,
        p.period_start,
        p.period_end,
        p.submit_date_time,
        rf.total_assets,
        rf.net_assets,
        rf.operating_income,
        rf.ordinary_income,
        rf.income_before_tax,
        rf.net_income,
        rf.net_income_per_unit,
        rf.net_assets_per_unit,
        rf.distribution_per_unit,
        rf.distribution_in_excess_of_profit_per_unit,
        rf.investment_units_outstanding,
        rf.treasury_investment_units
    from periods p
    left join {{ ref('intermediate__edinet__reit_financial_facts') }} rf on rf.doc_id = p.doc_id
)

select
    edinet_code,
    period_end,
    (list(doc_id order by submit_date_time desc))[1] as doc_id,
    (list(sec_code order by submit_date_time desc) filter (where sec_code is not null))[1] as sec_code,
    (list(filer_name order by submit_date_time desc) filter (where filer_name is not null))[1] as filer_name,
    (list(period_type order by submit_date_time desc) filter (where period_type is not null))[1] as period_type,
    (list(period_start order by submit_date_time desc) filter (where period_start is not null))[1] as period_start,
    (list(submit_date_time order by submit_date_time desc))[1] as submit_date_time,
    (list(total_assets order by submit_date_time desc) filter (where total_assets is not null))[1] as total_assets,
    (list(net_assets order by submit_date_time desc) filter (where net_assets is not null))[1] as net_assets,
    (list(operating_income order by submit_date_time desc) filter (where operating_income is not null))[1] as operating_income,
    (list(ordinary_income order by submit_date_time desc) filter (where ordinary_income is not null))[1] as ordinary_income,
    (list(income_before_tax order by submit_date_time desc) filter (where income_before_tax is not null))[1] as income_before_tax,
    (list(net_income order by submit_date_time desc) filter (where net_income is not null))[1] as net_income,
    (list(net_income_per_unit order by submit_date_time desc) filter (where net_income_per_unit is not null))[1] as net_income_per_unit,
    (list(net_assets_per_unit order by submit_date_time desc) filter (where net_assets_per_unit is not null))[1] as net_assets_per_unit,
    (list(distribution_per_unit order by submit_date_time desc) filter (where distribution_per_unit is not null))[1] as distribution_per_unit,
    (list(distribution_in_excess_of_profit_per_unit order by submit_date_time desc) filter (where distribution_in_excess_of_profit_per_unit is not null))[1] as distribution_in_excess_of_profit_per_unit,
    (list(investment_units_outstanding order by submit_date_time desc) filter (where investment_units_outstanding is not null))[1] as investment_units_outstanding,
    (list(treasury_investment_units order by submit_date_time desc) filter (where treasury_investment_units is not null))[1] as treasury_investment_units
from per_doc
group by edinet_code, period_end
