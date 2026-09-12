-- JPX形式C(株式相場表・詳細日次)セクション1(立会市場普通取引)の型付け版。
-- landing.jpx_stq_facts(Parquet、日付ごとにPrefectのload_jpx_stq_pricesタスクが
-- 更新)を型付けするだけの external materialization。EDINETと同じ理由
-- (DuckDBでのlanding全量読み込みが十分速く、複雑さを持つ理由が無い)で
-- 常に全量rebuildする。詳細はdocs/raw_landing_design.md参照。
--
-- 数値列はカンマ区切り("124,710.500")を除去してからtry_castする。未約定・
-- 非数値("－")はtry_castでNULLになる(EDINETのcleansedと同じ考え方)。
-- 業種の日英分離はlanding側(jpx_stq_facts.py)で既に完了している。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/jpx_stq_prices.parquet',
    format='parquet'
) }}

select
    cast(file_date as date)                                  as file_date,
    code,
    try_cast(trading_unit as integer)                        as trading_unit,
    name_ja,
    name_en,
    market_segment,
    industry_sector_ja,
    industry_sector_en,
    try_cast(replace(am_open, ',', '') as decimal(18, 4))            as am_open,
    try_cast(replace(am_high, ',', '') as decimal(18, 4))            as am_high,
    try_cast(replace(am_low, ',', '') as decimal(18, 4))             as am_low,
    try_cast(replace(am_close, ',', '') as decimal(18, 4))           as am_close,
    try_cast(replace(pm_open, ',', '') as decimal(18, 4))            as pm_open,
    try_cast(replace(pm_high, ',', '') as decimal(18, 4))            as pm_high,
    try_cast(replace(pm_low, ',', '') as decimal(18, 4))             as pm_low,
    try_cast(replace(pm_close, ',', '') as decimal(18, 4))           as pm_close,
    try_cast(replace(final_special_quote, ',', '') as decimal(18, 4)) as final_special_quote,
    try_cast(replace(net_change, ',', '') as decimal(18, 4))          as net_change,
    try_cast(replace(vwap, ',', '') as decimal(18, 4))                as vwap,
    try_cast(replace(trading_volume, ',', '') as decimal(20, 4))      as trading_volume,
    try_cast(replace(trading_value, ',', '') as decimal(20, 4))       as trading_value,
    try_cast(_loaded_at as timestamptz)                       as _loaded_at
from {{ source('landing', 'jpx_stq_facts') }}
