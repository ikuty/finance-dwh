-- JPX形式C(株式相場表・詳細日次)セクション1(立会市場普通取引)の型付け版。
-- landing.jpx_stq_facts(Parquet、日付ごとにPrefectのload_jpx_stq_pricesタスクが
-- 更新)を型付けするだけの external materialization。EDINETと同じ理由
-- (DuckDBでのlanding全量読み込みが十分速く、複雑さを持つ理由が無い)で
-- 常に全量rebuildする。詳細はdocs/raw_landing_design.md参照。
--
-- 数値列はカンマ区切り("124,710.500")を除去してからtry_castする。未約定・
-- 非数値("－")はtry_castでNULLになる(EDINETのcleansedと同じ考え方)。
-- 業種の日英分離はlanding側(jpx_stq_facts.py)で既に完了している。
--
-- 業種・市場区分のコード化(2026-09-17決定):
--   - PDF由来のindustry_sector_ja/enには、東証33業種(普通株式の事業分類、33種)と
--     ファンド型4特殊区分(内国投資信託受益証券・内国投資証券・外国投資証券・出資証券、
--     事業を営まないため業種概念が無い)が混在している。独立した属性として分離し、
--     ファンド型4種はsecurity_category_ja列へ、industry_sector_ja/enはNULLにする。
--   - industry_code_33・industry_code_17・market_segment_codeは、PDFに印字されて
--     いないため、seed(jpx_industry_33_17・jpx_market_segment_code)と業種名/
--     市場区分名でJOINして解決する。生の名称列(industry_sector_ja/en・
--     market_segment)は削除せずコード列と並存させる(cleansed__edinet__factsの
--     value_text/value_numと同じ「生データ＋型付け済み値を両方保持する」パターン)。
--   - ファンド型4種のindustry_code_33/17は「9999/99(その他)」に寄せず、明示的に
--     NULLとする(「その他業種に分類される」という誤解を避けるため)。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/jpx_stq_prices.parquet',
    format='parquet'
) }}

with base as (
    select
        cast(file_date as date)                                  as file_date,
        code,
        try_cast(trading_unit as integer)                        as trading_unit,
        name_ja,
        name_en,
        market_segment,
        industry_sector_ja                                       as raw_industry_sector_ja,
        industry_sector_en                                       as raw_industry_sector_en,
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
),

typed as (
    select
        b.*,
        case
            when raw_industry_sector_ja in
                ('内国投資信託受益証券', '内国投資証券', '外国投資証券', '出資証券')
            then raw_industry_sector_ja
        end as security_category_ja,
        case
            when raw_industry_sector_ja in
                ('内国投資信託受益証券', '内国投資証券', '外国投資証券', '出資証券')
            then null
            else raw_industry_sector_ja
        end as industry_sector_ja,
        case
            when raw_industry_sector_ja in
                ('内国投資信託受益証券', '内国投資証券', '外国投資証券', '出資証券')
            then null
            else raw_industry_sector_en
        end as industry_sector_en
    from base b
)

select
    t.file_date,
    t.code,
    t.trading_unit,
    t.name_ja,
    t.name_en,
    t.market_segment,
    ms.market_segment_code,
    t.industry_sector_ja,
    t.industry_sector_en,
    ind.industry_code_33,
    ind.industry_code_17,
    t.security_category_ja,
    t.am_open,
    t.am_high,
    t.am_low,
    t.am_close,
    t.pm_open,
    t.pm_high,
    t.pm_low,
    t.pm_close,
    t.final_special_quote,
    t.net_change,
    t.vwap,
    t.trading_volume,
    t.trading_value,
    t._loaded_at
from typed t
left join {{ ref('jpx_market_segment_code') }} ms
    on ms.market_segment_ja = t.market_segment
left join {{ ref('jpx_industry_33_17') }} ind
    on ind.industry_name_33_ja = t.industry_sector_ja
