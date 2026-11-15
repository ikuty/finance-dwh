-- intermediate__disclosure_events は (jpx_code, period_end) で一意であること（EDINETと決算短信の
-- 両方がある期も、最初に可視になる1件に絞っている）。行が返れば失敗。

select
    jpx_code,
    period_end,
    count(*) as n
from {{ ref('intermediate__disclosure_events') }}
group by 1, 2
having count(*) > 1
