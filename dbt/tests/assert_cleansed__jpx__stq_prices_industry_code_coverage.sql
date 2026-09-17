-- industry_sector_jaが非NULLなのにindustry_code_33/17が解決できていない(=seedとの
-- 名称マッチングに失敗した)行が無いこと。行が返れば失敗。

select
    code,
    file_date,
    industry_sector_ja,
    industry_code_33,
    industry_code_17
from {{ ref('cleansed__jpx__stq_prices') }}
where industry_sector_ja is not null
  and (industry_code_33 is null or industry_code_17 is null)
