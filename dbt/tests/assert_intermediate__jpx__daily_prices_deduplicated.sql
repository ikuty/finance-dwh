-- intermediate__jpx__daily_prices は (code, file_date) で一意であること。
-- stq_prices/daily_ohlcそれぞれの重複排除は各cleansedモデル側のテストで担保済みだが、
-- 結合時の日付境界処理（stq_prices優先・daily_ohlc側の除外条件）に誤りがあると
-- 重複が発生しうるため、結合後の粒度も別途検証する。行が返れば失敗。

select
    code,
    file_date,
    count(*) as n
from {{ ref('intermediate__jpx__daily_prices') }}
group by 1, 2
having count(*) > 1
