-- 抽出が正常な決算短信の大半で、実績12か月配当が算出できていること。抽出器や再抽出の退行
-- （配当列が丸ごとNULLになる等）を検知する。実データ（2026-11-12）では、配当表が取れた
-- 決算短信のうちstatus='ok'は約93%。ここでは余裕を見て85%を下限にする。行が返れば失敗。

with t as (
    select
        count(*) filter (where dividend_ttm_status != 'no_dividend_table') as with_table,
        count(*) filter (where dividend_ttm_status = 'ok') as ok
    from {{ ref('intermediate__ir_disclosure__dividend_ttm') }}
)
select with_table, ok, ok * 1.0 / nullif(with_table, 0) as ok_rate
from t
where with_table > 1000 and ok * 1.0 / with_table < 0.85
