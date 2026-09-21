-- salesはYTD累計のため、同一(edinet_code, fiscal_year)内で
-- q1 <= q2/half <= q3 <= annual となるはず（売上高が期中に減少することは無い）。
-- 2026-09-21判明のSMFGバグ（複数候補項目名のうち個別のみの値を誤って採用し、
-- q1のsalesがannualを上回っていた）の再発防止テスト。外部データ不要、
-- 当システム内部の整合性のみで検証する。行が返れば失敗。

with ordered as (
    select
        edinet_code,
        fiscal_year,
        period_type,
        sales,
        case period_type
            when 'q1' then 1
            when 'half' then 2
            when 'q2' then 2
            when 'q3' then 3
            when 'annual' then 4
        end as period_order
    from {{ ref('mart__edinet__financial_indicators') }}
    where sales is not null
),

pairs as (
    select
        a.edinet_code,
        a.fiscal_year,
        a.period_type as earlier_period,
        a.sales as earlier_sales,
        b.period_type as later_period,
        b.sales as later_sales
    from ordered a
    join ordered b
        on a.edinet_code = b.edinet_code
        and a.fiscal_year = b.fiscal_year
        and a.period_order < b.period_order
)

select *
from pairs
where later_sales < earlier_sales
