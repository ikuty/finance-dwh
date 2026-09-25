-- intermediate__jpx__daily_pricesに、intermediate__mufg__corporate_actionsから算出した
-- 累積調整係数(cumulative adjustment factor)を付与した株価。J-Quantsの方式に倣う
-- （https://jpx-jquants.com/ja/spec/eq-bars-daily/adj、詳細はintermediate__mufg__
-- corporate_actionsのコメント参照）。
--
-- 累積調整係数の計算方法:
--   日付降順に並べ、各行の累積調整係数＝その行より新しい日付の全adj_factorの積。
--   つまり「古い日付ほど、その後発生した全ての分割・併合の影響を掛け合わせて
--   受ける」。DuckDBには累積積の組み込み集約関数が無いため、
--   exp(sum(ln(adj_factor)))で代用する（adj_factorは常に正のため成立する）。
--   各価格行への適用は、ASOF JOIN（各行の日付より後の直近のイベントを検索）で行う。
--
-- 調整対象列: 始値・高値・安値・終値（前場・後場）・VWAPは調整前値×累積調整係数、
-- 出来高は調整前値÷累積調整係数（J-Quantsと同じ、株数が変わるため）。
-- final_special_quote（特別気配）・net_change（前日比）・trading_value（売買代金、
-- 価格×出来高の調整が相殺されるため無調整で正しい）は調整しない（生値のまま）。
--
-- 対象期間より後に分割・併合が無い銘柄・期間は累積調整係数=1.0（無調整）となる。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/jpx_daily_prices_adjusted.parquet',
    format='parquet'
) }}

with events_with_cum as (
    select
        code,
        ex_rights_date,
        adj_factor,
        exp(sum(ln(adj_factor)) over (
            partition by code
            order by ex_rights_date desc
            rows between unbounded preceding and current row
        )) as cum_adj_from_here
    from {{ ref('intermediate__mufg__corporate_actions') }}
)

select
    p.*,
    coalesce(e.cum_adj_from_here, 1.0) as cum_adjustment_factor,
    p.am_open * coalesce(e.cum_adj_from_here, 1.0) as am_open_adj,
    p.am_high * coalesce(e.cum_adj_from_here, 1.0) as am_high_adj,
    p.am_low * coalesce(e.cum_adj_from_here, 1.0) as am_low_adj,
    p.am_close * coalesce(e.cum_adj_from_here, 1.0) as am_close_adj,
    p.pm_open * coalesce(e.cum_adj_from_here, 1.0) as pm_open_adj,
    p.pm_high * coalesce(e.cum_adj_from_here, 1.0) as pm_high_adj,
    p.pm_low * coalesce(e.cum_adj_from_here, 1.0) as pm_low_adj,
    p.pm_close * coalesce(e.cum_adj_from_here, 1.0) as pm_close_adj,
    p.vwap * coalesce(e.cum_adj_from_here, 1.0) as vwap_adj,
    p.trading_volume / coalesce(e.cum_adj_from_here, 1.0) as trading_volume_adj
from {{ ref('intermediate__jpx__daily_prices') }} p
asof left join events_with_cum e
    on p.code = e.code
    and p.file_date < e.ex_rights_date
