-- TTM補正ロジックの健全性チェック。period_type='annual'の行はeps_ttm/sales_ttm/
-- dividend_per_share_ttmがそれぞれ生のeps/sales/dividend_per_shareと完全一致する
-- はず（補正不要、CASE文の実装ミスを検出する）。行が返れば失敗。

select
    doc_id, edinet_code, fiscal_year, period_type,
    eps, eps_ttm, sales, sales_ttm, dividend_per_share, dividend_per_share_ttm
from {{ ref('mart__jpx_edinet__disclosed_fundamentals') }}
where period_type = 'annual'
  and (
      eps_ttm != eps
      or sales_ttm != sales
      or coalesce(dividend_per_share_ttm, -1) != coalesce(dividend_per_share, -1)
  )
