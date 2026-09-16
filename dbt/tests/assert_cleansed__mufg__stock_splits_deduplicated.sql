-- cleansed__mufg__stock_splits は (code, allotment_date) で一意であること
-- （同一銘柄が複数回に分けて分割することがあるため、code単独ではなく割当日との
-- 組み合わせで判定する）。行が返れば失敗。

select
    code,
    allotment_date,
    count(*) as n
from {{ ref('cleansed__mufg__stock_splits') }}
group by 1, 2
having count(*) > 1
