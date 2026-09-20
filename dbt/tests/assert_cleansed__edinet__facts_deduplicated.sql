-- cleansed__edinet__facts は (doc_id, element_id, context_id, consolidation) で
-- 一意（1書類内の重複排除の結果、書類をまたいだ収縮はしない）であること。行が返れば失敗。

select
    doc_id,
    element_id,
    context_id,
    consolidation,
    count(*) as n
from {{ ref('cleansed__edinet__facts') }}
group by 1, 2, 3, 4
having count(*) > 1
