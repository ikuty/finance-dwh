-- 現金及び現金同等物の残高(cash_and_equivalents)は資産の一部であり、総資産額
-- (total_assets)を超えることは会計上あり得ない。異なるconsolidation scope
-- （連結/個別）から誤って組み合わせた場合にこの関係が崩れうる。外部データ不要。
-- 行が返れば失敗（total_assets_gte_net_assetsと同じ絶対不変条件、severity既定=error）。

select doc_id, edinet_code, fiscal_year, period_type, cash_and_equivalents, total_assets
from {{ ref('mart__edinet__financial_indicators') }}
where cash_and_equivalents is not null
  and total_assets is not null
  and cash_and_equivalents > total_assets
