-- cleansed__mufg__stock_consolidations は (code, effective_date) で一意であること。
-- 行が返れば失敗。

select
    code,
    effective_date,
    count(*) as n
from {{ ref('cleansed__mufg__stock_consolidations') }}
group by 1, 2
having count(*) > 1
