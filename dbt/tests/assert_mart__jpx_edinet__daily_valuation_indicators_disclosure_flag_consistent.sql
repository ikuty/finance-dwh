-- 開示の目印の整合性（#129）。行が返れば失敗。
--   1. 同じ(銘柄, 可視な最新の期)で is_first_day_after_disclosure が true の日は、高々1日。
--   2. disclosure_period_end が無いのに fiscal_quarter だけある行は無い（期種が取れない決算短信の
--      期は fiscal_quarter が NULL になりうるので、逆は許容する）。
--   3. BPSの期末(pbr_bps_period_end)が、可視な最新の期(disclosure_period_end)より新しいことはない
--      （BPSの元になる開示は、必ず開示イベントにも含まれる）。

with flagged as (
    select jpx_code, disclosure_period_end, count(*) filter (where is_first_day_after_disclosure) as n_first
    from {{ ref('mart__jpx_edinet__daily_valuation_indicators') }}
    where disclosure_period_end is not null
    group by 1, 2
)
select jpx_code, disclosure_period_end, n_first, 'multiple_first_days' as reason
from flagged
where n_first > 1

union all

select jpx_code, disclosure_period_end, null, 'quarter_mismatch'
from {{ ref('mart__jpx_edinet__daily_valuation_indicators') }}
where disclosure_period_end is null and fiscal_quarter is not null

union all

select jpx_code, pbr_bps_period_end, null, 'bps_newer_than_disclosure'
from {{ ref('mart__jpx_edinet__daily_valuation_indicators') }}
where pbr_bps_period_end is not null
  and (disclosure_period_end is null or pbr_bps_period_end > disclosure_period_end)
