-- cleansed__jpx__monthly_ohlc は前場・後場それぞれ「安値 <= 始値・終値 <= 高値」
-- (安値 <= 高値 を含む)であること。実データ2025-09分・83,195件で実測検証済み
-- （x1(右端)ベースの列判定に修正後は違反0件、詳細はdocs/raw_landing_design.md
-- 参照）。行が返れば失敗。

with sessions as (
    select code, file_date, am_open as open, am_high as high, am_low as low, am_close as close
    from {{ ref('cleansed__jpx__monthly_ohlc') }}
    where am_open is not null and am_high is not null and am_low is not null and am_close is not null

    union all

    select code, file_date, pm_open as open, pm_high as high, pm_low as low, pm_close as close
    from {{ ref('cleansed__jpx__monthly_ohlc') }}
    where pm_open is not null and pm_high is not null and pm_low is not null and pm_close is not null
)

select *
from sessions
where not (low <= open and open <= high and low <= close and close <= high and low <= high)
