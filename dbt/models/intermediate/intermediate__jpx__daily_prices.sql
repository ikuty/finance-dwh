-- cleansed__jpx__stq_prices（形式C、2025-09-01〜、詳細日次）と
-- cleansed__jpx__daily_ohlc（形式B、2020-01-06〜2025-09-30、簡易日次）を結合した
-- 普通株式の連続日次株価。単純な型付けではなく複数cleansedソースの結合・重複排除・
-- 銘柄コード正規化という業務ロジックを含むため、cleansed層ではなくintermediate層に
-- 置く（EDINET側のintermediate層と同じ考え方）。
--
-- 対象を普通株式に限定する理由(2026-09-25、実データで確認):
--   - cleansed__jpx__daily_ohlcのcodeは主に5桁（末尾0付き、例:'13010'）だが、
--     900から始まる9桁のcodeも存在し、これは転換社債型新株予約権付社債（普通株式
--     ではない）だった。length(code)=5の行のみを対象とする。
--   - cleansed__jpx__stq_pricesのcodeは主に4桁だが、一部5桁（末尾0以外、例:
--     '25935'）が存在し、これは優先株だった。length(code)=4の行のみを対象とする。
--   - 銘柄コードの表記ゆれ: daily_ohlcの5桁(末尾0)はstq_pricesの4桁に対応する
--     （例: daily_ohlcの'13010' = stq_pricesの'1301'）。daily_ohlc側をsubstr(code,1,4)
--     で4桁に正規化して結合する。
--
-- 実データ検証(2026-09-25): 両モデルが重複する2025年9月の全営業日で、コード変換後の
-- 終値を突合したところ88,184件中、前場終値94.8%・後場終値94.5%が完全一致した
-- （残差はPDF形式間の丸め方の違い等と考えられ、同一の実株価を異なるPDF形式で
-- 表現しているだけであることが裏付けられた）。
--
-- 期間の優先順位: stq_pricesの方が列数が多く直近パイプラインの正のため、重複する
-- 期間（stq_pricesの最小file_date以降）はstq_pricesを優先し、daily_ohlc側は
-- それより前の期間のみを採用する（ハードコードした日付ではなく、stq_pricesの
-- 実際の最小file_dateを動的に参照することで、将来stq_pricesの遡及範囲が変わっても
-- 自動的に追従する）。
--
-- 列構成: stq_pricesの全列を採用し、daily_ohlc期間（列が存在しない項目）はNULL
-- 埋めとする（列を共通部分に絞ると情報量が減るため、stq_prices優先で保持する方針。
-- 2026-09-25、ユーザー判断）。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/jpx_daily_prices.parquet',
    format='parquet'
) }}

with stq_prices as (
    select
        file_date,
        code,
        trading_unit,
        name_ja,
        name_en,
        market_segment,
        market_segment_code,
        industry_sector_ja,
        industry_sector_en,
        industry_code_33,
        industry_code_17,
        security_category_ja,
        am_open, am_high, am_low, am_close,
        pm_open, pm_high, pm_low, pm_close,
        final_special_quote,
        net_change,
        vwap,
        trading_volume,
        trading_value,
        'stq_prices' as source_model
    from {{ ref('cleansed__jpx__stq_prices') }}
    where length(code) = 4
),

stq_prices_min_date as (
    select min(file_date) as min_date from stq_prices
),

daily_ohlc as (
    select
        d.file_date,
        substr(d.code, 1, 4) as code,
        cast(null as integer) as trading_unit,
        d.name_ja,
        cast(null as varchar) as name_en,
        cast(null as varchar) as market_segment,
        cast(null as varchar) as market_segment_code,
        cast(null as varchar) as industry_sector_ja,
        cast(null as varchar) as industry_sector_en,
        cast(null as varchar) as industry_code_33,
        cast(null as varchar) as industry_code_17,
        cast(null as varchar) as security_category_ja,
        d.am_open, d.am_high, d.am_low, d.am_close,
        d.pm_open, d.pm_high, d.pm_low, d.pm_close,
        cast(null as decimal(18, 4)) as final_special_quote,
        cast(null as decimal(18, 4)) as net_change,
        cast(null as decimal(18, 4)) as vwap,
        cast(null as decimal(20, 4)) as trading_volume,
        cast(null as decimal(20, 4)) as trading_value,
        'daily_ohlc' as source_model
    from {{ ref('cleansed__jpx__daily_ohlc') }} d
    cross join stq_prices_min_date m
    where length(d.code) = 5
      and d.file_date < m.min_date
)

select * from stq_prices
union all
select * from daily_ohlc
