-- 「どの期の決算が、いつから開示済みとして可視になるか」を、銘柄×期末日で1行にしたもの
-- （毎回フル rebuild、Parquet。2026-11-16新設、#129）。EDINETの報告書と決算短信を合わせ、
-- 同じ期に両方があれば、**早いほうが可視になる日時**を採る（通常は決算短信）。後から届く
-- EDINETの確定値は、別のイベントにしない（値は各指標の時系列が置き換える）。
--
-- 用途: 日次マートが、各日で「可視な最新の期」とその会計期（1Q〜4Q）、「その期が最初に反映される
-- 営業日」の目印を出す（mart__jpx_edinet__daily_valuation_indicators）。バリュエーション履歴を
-- 四半期ごとに取得するときの起点になる。
--
-- 可視になる日時（既存の可視ルールと同じ）:
--   - EDINET: submit_date_time（日付との比較で、提出の翌日から参照される）
--   - 決算短信: 開示日(file_date)の翌日の0時（当日の引け後の開示を先読みしない）
--
-- 会計期の表記: 期種(period_type)から機械的に決まる。
--   q1→1Q、q2/half/q2_half→2Q、q3→3Q、annual/q4→4Q（通期は4Q）。
--   期種が取れない決算短信（タイトルが「第4四半期」等の変則、約15件）も、期末日があれば
--   イベントにする（会計期はNULL）。BPSの推定などは期種を問わずその期を使うため、イベントから
--   外すと、BPSの期末が「可視な最新の期」より新しくなる銘柄が出るため。同じ(jpx_code, period_end)
--   にEDINETなど、会計期が分かる出所があれば、そのラベルを使う。
--
-- 同じ(jpx_code, period_end)にEDINETと決算短信が両方ある場合、期末日は同じなので会計期も
-- 同じはず。可視になる日時は早いほう、会計期は、分かる出所のものを使う。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/disclosure_events.parquet',
    format='parquet'
) }}

with edinet as (
    select
        jpx_code,
        period_end,
        period_type,
        case period_type
            when 'q1' then '1Q'
            when 'q2' then '2Q'
            when 'half' then '2Q'
            when 'q3' then '3Q'
            when 'annual' then '4Q'
            when 'q4' then '4Q'
        end                                   as fiscal_quarter,
        submit_date_time                      as visible_from,
        'edinet'                              as source
    from {{ ref('mart__jpx_edinet__disclosed_fundamentals') }}
    where jpx_code is not null and period_end is not null
),

tanshin as (
    select
        left(sec_code, 4)                     as jpx_code,
        period_end,
        period_type,
        case period_type
            when 'q1' then '1Q'
            when 'q2_half' then '2Q'
            when 'q3' then '3Q'
            when 'annual' then '4Q'
        end                                   as fiscal_quarter,
        cast(file_date as timestamp) + interval 1 day as visible_from,
        'tanshin'                             as source
    from {{ ref('cleansed__ir_disclosure__kessan_tanshin') }}
    where extraction_status = 'ok'
      and sec_code is not null
      and period_end is not null
),

events as (
    select * from edinet
    union all
    select * from tanshin
),

labeled as (
    select
        *,
        -- 会計期は、同じ(jpx_code, period_end)の出所のうち、分かるものを使う
        coalesce(fiscal_quarter, max(fiscal_quarter) over (partition by jpx_code, period_end)) as fiscal_quarter_any
    from events
)

select
    jpx_code,
    period_end,
    fiscal_quarter_any as fiscal_quarter,
    period_type,
    visible_from,
    source as first_source
from labeled
qualify row_number() over (
    partition by jpx_code, period_end
    order by visible_from, case source when 'tanshin' then 0 else 1 end
) = 1
