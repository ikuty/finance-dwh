-- 直接値(estimate_status='direct')が、推定(bps_raw_estimate = 年次BPS×調整比×純資産比、
-- ±10%の安全装置なし)と5倍超(0.2倍未満)ずれている行を警告で数える(#139 パート3)。
-- 直接値と推定のずれは、増資・自己株式取得などの株数の変化で正当に起きる(J-Quantsとの照合で
-- 安全弁に掛かった61行のうち短信のBPSが一致したのは60行)ため、0.5〜2倍の安全弁は外した。
-- 5倍超のずれは、抽出器の誤抽出(列の取り違え等)の再発の目印として残す。
-- severity=warn(buildは失敗させない、目視確認用)。行が返れば対象。

{{ config(severity='warn') }}

select
    edinet_code,
    docid,
    period_type,
    period_end,
    accounting_standard,
    bps_actual,
    bps_raw_estimate,
    bps_actual / bps_raw_estimate as ratio
from {{ ref('intermediate__ir_disclosure__bps_estimates') }}
where estimate_status = 'direct'
  and bps_raw_estimate > 0
  and bps_actual / bps_raw_estimate not between 0.2 and 5
