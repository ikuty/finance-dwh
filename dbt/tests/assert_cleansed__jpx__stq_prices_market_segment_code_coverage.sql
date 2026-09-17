-- market_segmentが非NULLなのにmarket_segment_codeが解決できていない(=seedとの
-- 名称マッチングに失敗した)行が無いこと。行が返れば失敗。

select
    code,
    file_date,
    market_segment,
    market_segment_code
from {{ ref('cleansed__jpx__stq_prices') }}
where market_segment is not null
  and market_segment_code is null
