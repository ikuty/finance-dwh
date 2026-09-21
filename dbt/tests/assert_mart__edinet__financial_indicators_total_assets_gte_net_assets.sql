-- 総資産額(total_assets) = 負債 + 純資産額(net_assets)であり、負債が負になることは
-- 無いため、常に total_assets >= net_assets が成立するはず。異なるconsolidation
-- scope（連結/個別）から誤って組み合わせた場合にこの関係が崩れうる。外部データ不要。
-- 行が返れば失敗。

select doc_id, edinet_code, fiscal_year, period_type, total_assets, net_assets
from {{ ref('mart__edinet__financial_indicators') }}
where total_assets is not null
  and net_assets is not null
  and total_assets < net_assets
