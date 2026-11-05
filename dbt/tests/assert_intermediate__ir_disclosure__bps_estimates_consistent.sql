-- 推定BPSが「年次BPS × 調整比 × 純資産比」と一致し、純資産比が±10%以内であること。
-- 推定式や安全装置（±10%）を変更したときの取りこぼしを検知する。行が返れば失敗。

select
    edinet_code,
    period_end,
    bps_estimated,
    annual_bps * (annual_cum_adj / period_end_cum_adj) * net_assets_ratio as expected,
    net_assets_ratio
from {{ ref('intermediate__ir_disclosure__bps_estimates') }}
where estimate_status = 'ok'
  and (
      bps_estimated is null
      or abs(bps_estimated / (annual_bps * (annual_cum_adj / period_end_cum_adj) * net_assets_ratio) - 1) > 1e-9
      or abs(net_assets_ratio - 1) > 0.10
  )
