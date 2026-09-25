-- mart__jpx_edinet__daily_valuation_indicators は (jpx_code, file_date) で
-- 一意であること。ASOF JOINは各左行(取引日)に対して高々1件の開示しかマッチしない
-- 設計だが、結合ロジックの誤りで行が増幅していないか別途検証する。行が返れば失敗。

select
    jpx_code,
    file_date,
    count(*) as n
from {{ ref('mart__jpx_edinet__daily_valuation_indicators') }}
group by 1, 2
having count(*) > 1
