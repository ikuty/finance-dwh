-- cleansed__mufg__company_name_changes は (code, change_date) で一意であること。
-- 行が返れば失敗。

select
    code,
    change_date,
    count(*) as n
from {{ ref('cleansed__mufg__company_name_changes') }}
group by 1, 2
having count(*) > 1
