-- intermediate__jpx__daily_prices_adjustedは(code, file_date)で一意であること。
-- ASOF JOINは各左行に対して高々1行の右行しかマッチしない設計だが、結合ロジックの
-- 誤りで行が増幅していないか別途検証する。行が返れば失敗。

select
    code,
    file_date,
    count(*) as n
from {{ ref('intermediate__jpx__daily_prices_adjusted') }}
group by 1, 2
having count(*) > 1
