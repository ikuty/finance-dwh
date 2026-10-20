-- 銘柄(edinet_code×fund_code)×期(period_end)ごとのETF(上場投資信託)主要財務指標
-- （毎回フル rebuild、Parquet）。intermediate__edinet__etf_financial_facts
-- （doc_id単位）を期単位に集約する薄い層。mart__edinet__reit_financial_indicators
-- と同じ考え方(案B)で、事業会社向けmart__edinet__financial_indicatorsとは
-- 完全に独立したモデルとする。
--
-- 粒度(重要、REITと異なる): REITはedinet_code×period_endで一意だが、ETFは同一
-- edinet_codeで複数ファンドを運用する運用会社が存在するため、edinet_code×
-- fund_code×period_endをgrainとする(詳細はintermediate側のモデルコメント参照)。
--
-- sec_code: REITは書類一覧APIのsecCodeが常にNULLでDEIタグによる補完が必要だったが、
-- ETFはPhase 1(2026-10-17)でcleansed__edinet__documents側がedinet_etf_fund_issuers
-- seedから既にsec_codeを補完済みのため、cleansed__edinet__report_periods.sec_code
-- がそのまま使える(DEI補完は不要)。
--
-- fund_name: documents.filer_name は運用会社名(例: ブラックロック・ジャパン株式
-- 会社)であり、ファンドそのものの名称ではない。edinet_etf_fund_issuers seedの
-- fund_name(例: ｉシェアーズ・コア　Ｊリート　ＥＴＦ)をfund_code単位で結合し、
-- 利用者が実際に識別しやすい名称を別列として提供する。
--
-- 訂正報告書対応: mart__edinet__financial_indicatorsと同じ「列単位のlatest-non-null」
-- 方式(doc_id/submit_date_time等のメタデータ列は最新書類を代表値とするが、個々の
-- 指標列はその書類に値が無ければ古い書類から遡る)。

{{ config(
    materialized='external',
    location=env_var('MART_ROOT', '/data/mart') ~ '/edinet_etf_financial_indicators.parquet',
    format='parquet'
) }}

with periods as (
    select
        doc_id, edinet_code, fund_code, sec_code, filer_name, period_type,
        period_start, period_end, submit_date_time
    from {{ ref('cleansed__edinet__report_periods') }}
    where filer_category = 'etf' and period_end is not null
),

fund_names as (
    select edinet_code, fund_code, fund_name
    from {{ ref('edinet_etf_fund_issuers') }}
),

per_doc as (
    select
        p.doc_id,
        p.edinet_code,
        p.fund_code,
        p.sec_code,
        p.filer_name,
        fn.fund_name,
        p.period_type,
        p.period_start,
        p.period_end,
        p.submit_date_time,
        ef.total_assets,
        ef.liabilities,
        ef.net_assets,
        ef.operating_income,
        ef.ordinary_income,
        ef.net_income,
        ef.principal,
        ef.surplus_deficit,
        ef.reserve_for_distribution
    from periods p
    left join fund_names fn on fn.edinet_code = p.edinet_code and fn.fund_code = p.fund_code
    left join {{ ref('intermediate__edinet__etf_financial_facts') }} ef on ef.doc_id = p.doc_id
)

select
    edinet_code,
    fund_code,
    period_end,
    (list(doc_id order by submit_date_time desc))[1] as doc_id,
    (list(sec_code order by submit_date_time desc) filter (where sec_code is not null))[1] as sec_code,
    (list(filer_name order by submit_date_time desc) filter (where filer_name is not null))[1] as filer_name,
    (list(fund_name order by submit_date_time desc) filter (where fund_name is not null))[1] as fund_name,
    (list(period_type order by submit_date_time desc) filter (where period_type is not null))[1] as period_type,
    (list(period_start order by submit_date_time desc) filter (where period_start is not null))[1] as period_start,
    (list(submit_date_time order by submit_date_time desc))[1] as submit_date_time,
    (list(total_assets order by submit_date_time desc) filter (where total_assets is not null))[1] as total_assets,
    (list(liabilities order by submit_date_time desc) filter (where liabilities is not null))[1] as liabilities,
    (list(net_assets order by submit_date_time desc) filter (where net_assets is not null))[1] as net_assets,
    (list(operating_income order by submit_date_time desc) filter (where operating_income is not null))[1] as operating_income,
    (list(ordinary_income order by submit_date_time desc) filter (where ordinary_income is not null))[1] as ordinary_income,
    (list(net_income order by submit_date_time desc) filter (where net_income is not null))[1] as net_income,
    (list(principal order by submit_date_time desc) filter (where principal is not null))[1] as principal,
    (list(surplus_deficit order by submit_date_time desc) filter (where surplus_deficit is not null))[1] as surplus_deficit,
    (list(reserve_for_distribution order by submit_date_time desc) filter (where reserve_for_distribution is not null))[1] as reserve_for_distribution
from per_doc
group by edinet_code, fund_code, period_end
