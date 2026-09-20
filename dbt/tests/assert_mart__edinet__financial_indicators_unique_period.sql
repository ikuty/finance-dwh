-- mart__edinet__financial_indicators は (edinet_code, fiscal_year, period_type) で
-- 一意であること（target_periodsのdedupが正しく機能しているかの検証）。行が返れば失敗。

select
    edinet_code,
    fiscal_year,
    period_type,
    count(*) as n
from {{ ref('mart__edinet__financial_indicators') }}
group by 1, 2, 3
having count(*) > 1
