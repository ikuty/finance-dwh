-- edinet_etf_fund_issuers は (edinet_code, fund_code) の複合キーで一意であること
-- (edinet_codeだけでは同一運用会社が複数ETFを持つため一意にならない)。行が返れば失敗。

select
    edinet_code,
    fund_code,
    count(*) as n
from {{ ref('edinet_etf_fund_issuers') }}
group by 1, 2
having count(*) > 1
