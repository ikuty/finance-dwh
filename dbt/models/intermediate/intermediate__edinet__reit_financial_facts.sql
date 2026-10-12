-- 書類(doc_id)単位のREIT(投資法人)財務指標。他の3つの会計基準別intermediateモデル
-- （J-GAAP/IFRS/US GAAP）とは全く別の設計が必要（REITは経営指標等(5期比較)の
-- 構造化タグを持たず、1口当たり指標はEDINET側に一切存在しないため。詳細は
-- services/ir-disclosure-dl/docs/reit_support_design.md@finance-lake参照）。
--
-- 絶対額系（純資産・当期純利益等）: EDINETの生タグ（jppfs_cor:、通常企業の非
-- 経営指標等ファクトと共通の名前空間）から取得する。REITは常に非連結
-- （実機確認: 連結・個別列が{'個別','その他'}のみ、filer_category='reit'の
-- 全書類で'連結'が一度も出現しない）のため、他モデルのhas_consolidated判定・
-- 個別値フォールバックは不要。
--
-- 1口当たり系（EPS/BPS/分配金相当）・発行済投資口総数: EDINET側に構造化タグが
-- 存在しないため、ir-disclosure-dl経由のTDnet/JPX決算短信iXBRL（`tse-re-t:`
-- タクソノミ）から取得する。EDINETのdoc_id空間とは異なるため、edinet_code×
-- period_endで結合する（両者とも「当該REITの決算期末の月末日」を指すため同じ値に
-- なるはずだが、全量での一致率は未検証。既知の制限）。
--
-- context_idパターン（実機確認、2026-10-12、REIT全書類で検証済み）:
--   EDINET側: doc_type_code 120/130(有報/訂正有報)は'CurrentYearInstant/Duration_
--   NonConsolidatedMember'(当期)・'Prior1Year...'(前期)。160/170(半期報告書/訂正)は
--   'InterimInstant/Duration_NonConsolidatedMember'(当期)・'Prior1Interim...'
--   （前期同期）・'Prior1Year...'（前期末/前期通期）。通常企業向け3モデルの
--   Interim/CurrentQuarter/CurrentYTD判定と同根だが、REITは常に非連結のため
--   _NonConsolidatedMemberサフィックスを必須とする点が異なる。
--   ir_disclosure側: 'CurrentYear{Instant,Duration}_NonConsolidatedMember_
--   ResultMember'固定（REITの決算短信には四半期相当が無く、タイトルの「期」が
--   開示対象期間そのものを指すため、半期報告書相当のラベル分岐は不要）。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/edinet_reit_financial_facts.parquet',
    format='parquet'
) }}

with target_docs as (
    select doc_id, edinet_code, doc_type_code, period_end
    from {{ ref('cleansed__edinet__report_periods') }}
    where filer_category = 'reit'
),

edinet_relevant_facts as (
    select f.doc_id, f.item_name, f.context_id, f.value_num, td.doc_type_code
    from {{ ref('cleansed__edinet__facts') }} f
    inner join target_docs td on td.doc_id = f.doc_id
    where f.item_name in (
        '資産',
        '負債純資産',
        '純資産',
        '営業利益又は営業損失（△）',
        '経常利益又は経常損失（△）',
        '税引前当期純利益又は税引前当期純損失（△）',
        '当期純利益又は当期純損失（△）（平成26年3月28日財規等改正後）'
    )
),

edinet_current_period_facts as (
    select
        doc_id, item_name, value_num,
        case
            when doc_type_code in ('120', '130') then context_id = 'CurrentYearInstant_NonConsolidatedMember'
                or context_id = 'CurrentYearDuration_NonConsolidatedMember'
            when doc_type_code in ('160', '170') then context_id = 'InterimInstant_NonConsolidatedMember'
                or context_id = 'InterimDuration_NonConsolidatedMember'
            else false
        end as is_current_period
    from edinet_relevant_facts
),

edinet_aggregated as (
    select
        doc_id,
        max(case when item_name = '資産' and is_current_period then value_num end) as total_assets,
        max(case when item_name = '純資産' and is_current_period then value_num end) as net_assets,
        max(case when item_name = '営業利益又は営業損失（△）' and is_current_period then value_num end) as operating_income,
        max(case when item_name = '経常利益又は経常損失（△）' and is_current_period then value_num end) as ordinary_income,
        max(case when item_name = '税引前当期純利益又は税引前当期純損失（△）' and is_current_period then value_num end) as income_before_tax,
        max(case when item_name = '当期純利益又は当期純損失（△）（平成26年3月28日財規等改正後）' and is_current_period then value_num end) as net_income
    from edinet_current_period_facts
    where is_current_period
    group by doc_id
),

reit_ixbrl_relevant_facts as (
    select edinet_code, period_end, element_id, value_num
    from {{ ref('cleansed__ir_disclosure__reit_ixbrl_facts') }}
    where element_id in (
        'tse-re-t:NetIncomePerUnitREIT',
        'tse-re-t:NetAssetsPerUnitREIT',
        'tse-re-t:DistributionsPerUnitExcludingDistributionsInExcessOfProfitREIT',
        'tse-re-t:DistributionsInExcessOfProfitPerUnitREIT',
        'tse-re-t:NumberOfInvestmentUnitsIncludingTreasuryInvestmentUnitsIssuedAtEndOfPeriodREIT',
        'tse-re-t:NumberOfTreasuryInvestmentUnitsIssuedAtEndOfPeriodREIT'
    )
    and context_id in (
        'CurrentYearInstant_NonConsolidatedMember_ResultMember',
        'CurrentYearDuration_NonConsolidatedMember_ResultMember'
    )
    and edinet_code is not null
    and period_end is not null
),

reit_ixbrl_aggregated as (
    select
        edinet_code,
        period_end,
        max(case when element_id = 'tse-re-t:NetIncomePerUnitREIT' then value_num end) as net_income_per_unit,
        max(case when element_id = 'tse-re-t:NetAssetsPerUnitREIT' then value_num end) as net_assets_per_unit,
        max(case when element_id = 'tse-re-t:DistributionsPerUnitExcludingDistributionsInExcessOfProfitREIT' then value_num end) as distribution_per_unit,
        max(case when element_id = 'tse-re-t:DistributionsInExcessOfProfitPerUnitREIT' then value_num end) as distribution_in_excess_of_profit_per_unit,
        max(case when element_id = 'tse-re-t:NumberOfInvestmentUnitsIncludingTreasuryInvestmentUnitsIssuedAtEndOfPeriodREIT' then value_num end) as investment_units_outstanding,
        max(case when element_id = 'tse-re-t:NumberOfTreasuryInvestmentUnitsIssuedAtEndOfPeriodREIT' then value_num end) as treasury_investment_units
    from reit_ixbrl_relevant_facts
    group by edinet_code, period_end
)

select
    td.doc_id,
    td.edinet_code,
    td.doc_type_code,
    td.period_end,
    ea.total_assets,
    ea.net_assets,
    ea.operating_income,
    ea.ordinary_income,
    ea.income_before_tax,
    ea.net_income,
    ria.net_income_per_unit,
    ria.net_assets_per_unit,
    ria.distribution_per_unit,
    ria.distribution_in_excess_of_profit_per_unit,
    ria.investment_units_outstanding,
    ria.treasury_investment_units
from target_docs td
left join edinet_aggregated ea on ea.doc_id = td.doc_id
left join reit_ixbrl_aggregated ria
    on ria.edinet_code = td.edinet_code and ria.period_end = td.period_end
