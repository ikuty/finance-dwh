-- cleansed_edinet__facts は (edinet_code, element_id, context_id, consolidation) で
-- 一意（最新提出のみ残す名寄せの結果）であること。行が返れば失敗。

select
    edinet_code,
    element_id,
    context_id,
    consolidation,
    count(*) as n
from {{ ref('cleansed_edinet__facts') }}
group by 1, 2, 3, 4
having count(*) > 1
