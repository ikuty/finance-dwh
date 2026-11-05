-- intermediate__ir_disclosure__bps_estimates は (edinet_code, period_end) で一意であること。
-- 同じ期末に決算短信が複数あっても、連結優先・開示日が新しいものの1件に絞っている。行が返れば失敗。

select
    edinet_code,
    period_end,
    count(*) as n
from {{ ref('intermediate__ir_disclosure__bps_estimates') }}
group by 1, 2
having count(*) > 1
