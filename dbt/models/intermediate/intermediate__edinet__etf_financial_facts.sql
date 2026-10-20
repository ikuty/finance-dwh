-- 書類(doc_id)単位のETF(上場投資信託)財務指標。REITと同様、経営指標等(5期比較)の
-- 構造化タグを持たないため、EDINETの生タグ(jppfs_cor:、通常企業の非経営指標等
-- ファクトと共通の名前空間)から取得する。
--
-- REITとの違い(2026-10-18決定):
--   - 1口当たり指標(基準価額・分配金)は対象外(cleansed__edinet__report_periods
--     冒頭コメント参照。TDnet/JPX決算短信がPDFのみでiXBRL化されておらず、ETFは
--     市場価格が基準価額とほぼ一致する設計上の性質があるため、絶対額指標のみで
--     十分と判断した)。よってir_disclosure系iXBRLとの結合は無い。
--   - 税引前当期純利益(jppfs_cor:IncomeBeforeIncomeTaxes等)に相当するタグが
--     ETFの開示には存在しない(実機確認、2026-10-18。REITのintermediateには
--     income_before_taxがあるが、こちらには無い)。
--   - 投資信託特有の会計項目(元本・剰余金又は欠損金・分配準備積立金)を追加する
--     (REITには無い。投資法人は株式会社と同じ出資構造、ETFは信託のため元本等の
--     概念を持つ)。
--
-- REITと共通の点:
--   - 連結決算を行わない(実機確認: 連結・個別列が{'non_consolidated','other'}の
--     みで'consolidated'が一度も出現しない。REITと同じ理由でhas_consolidated
--     判定・個別値フォールバックは不要)。
--   - context_idパターンはREITと同一(doc_type_code 120/130は
--     'CurrentYearInstant/Duration_NonConsolidatedMember'、160/170(半期報告書、
--     実在151件確認済み)は'InterimInstant/Duration_NonConsolidatedMember')。
--
-- 粒度(重要、REITと異なる): ETFは同一edinet_codeで複数ファンドを運用する運用会社が
-- 存在する(edinet_dl側Phase 0調査で判明)ため、doc_id単位であってもedinet_codeだけ
-- では銘柄を一意に識別できない。fund_codeを必ず一緒に保持する
-- (cleansed__edinet__report_periods.fund_code、2026-10-18追加)。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/edinet_etf_financial_facts.parquet',
    format='parquet'
) }}

with target_docs as (
    select doc_id, edinet_code, fund_code, doc_type_code, period_end
    from {{ ref('cleansed__edinet__report_periods') }}
    where filer_category = 'etf'
),

relevant_facts as (
    select f.doc_id, f.item_name, f.context_id, f.value_num, td.doc_type_code
    from {{ ref('cleansed__edinet__facts') }} f
    inner join target_docs td on td.doc_id = f.doc_id
    where f.item_name in (
        '資産',
        '負債',
        '純資産',
        '営業利益又は営業損失（△）',
        '経常利益又は経常損失（△）',
        '当期純利益又は当期純損失（△）（平成26年3月28日財規等改正後）',
        '元本、投資信託',
        '剰余金又は欠損金（△）、投資信託',
        '（分配準備積立金）、投資信託'
    )
),

current_period_facts as (
    select
        doc_id, item_name, value_num,
        case
            when doc_type_code in ('120', '130') then context_id = 'CurrentYearInstant_NonConsolidatedMember'
                or context_id = 'CurrentYearDuration_NonConsolidatedMember'
            when doc_type_code in ('160', '170') then context_id = 'InterimInstant_NonConsolidatedMember'
                or context_id = 'InterimDuration_NonConsolidatedMember'
            else false
        end as is_current_period
    from relevant_facts
),

aggregated as (
    select
        doc_id,
        max(case when item_name = '資産' and is_current_period then value_num end) as total_assets,
        max(case when item_name = '負債' and is_current_period then value_num end) as liabilities,
        max(case when item_name = '純資産' and is_current_period then value_num end) as net_assets,
        max(case when item_name = '営業利益又は営業損失（△）' and is_current_period then value_num end) as operating_income,
        max(case when item_name = '経常利益又は経常損失（△）' and is_current_period then value_num end) as ordinary_income,
        max(case when item_name = '当期純利益又は当期純損失（△）（平成26年3月28日財規等改正後）' and is_current_period then value_num end) as net_income,
        max(case when item_name = '元本、投資信託' and is_current_period then value_num end) as principal,
        max(case when item_name = '剰余金又は欠損金（△）、投資信託' and is_current_period then value_num end) as surplus_deficit,
        max(case when item_name = '（分配準備積立金）、投資信託' and is_current_period then value_num end) as reserve_for_distribution
    from current_period_facts
    where is_current_period
    group by doc_id
)

select
    td.doc_id,
    td.edinet_code,
    td.fund_code,
    td.doc_type_code,
    td.period_end,
    a.total_assets,
    a.liabilities,
    a.net_assets,
    a.operating_income,
    a.ordinary_income,
    a.net_income,
    a.principal,
    a.surplus_deficit,
    a.reserve_for_distribution
from target_docs td
left join aggregated a on a.doc_id = td.doc_id
