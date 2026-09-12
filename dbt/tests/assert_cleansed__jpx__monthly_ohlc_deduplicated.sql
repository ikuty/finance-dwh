-- cleansed__jpx__monthly_ohlc は (code, file_date) で一意であること。行が返れば失敗。

select
    code,
    file_date,
    count(*) as n
from {{ ref('cleansed__jpx__monthly_ohlc') }}
group by 1, 2
having count(*) > 1
