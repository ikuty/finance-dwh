-- 配当利回りが現実的な範囲に収まっていること（警告）。種類株（優先株）の配当が配当表に載る会社や
-- 新規上場前の配当等で、利回りが100%を超える異常値になることがある（実データ2026-11-12:
-- 8303・300A・410A、EDINET側は3223等）。値は隠さず、銘柄を警告で可視化する。
-- 行が返れば警告（失敗にはしない）。

{{ config(severity='warn') }}

select
    jpx_code,
    dividend_basis,
    count(*)                 as days,
    max(dividend_yield)      as max_dividend_yield,
    max(forecast_dividend_yield) as max_forecast_dividend_yield
from {{ ref('mart__jpx_edinet__daily_valuation_indicators') }}
where dividend_yield > 1 or forecast_dividend_yield > 1
group by 1, 2
