-- 三菱UFJ eスマート証券(kabu.com)の株式併合ページの型付け版。
-- landing.mufg_stock_consolidations(Parquet)を型付けするだけのexternal
-- materialization。cleansed__mufg__stock_splitsと同じ理由で、cleansedは
-- landingの最新file_dateのみを読む。
--
-- 併合比率は"10株→1株"のような文字列。ratio_before/ratio_afterに分割して
-- decimalへ型付けする(生の文字列もratio_rawとして残す)。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/mufg_stock_consolidations.parquet',
    format='parquet'
) }}

select
    cast(file_date as date)                                              as file_date,
    try_cast(effective_date as date)                                     as effective_date,
    code,
    name,
    ratio                                                                as ratio_raw,
    try_cast(regexp_extract(ratio, '^([0-9.]+)株', 1) as decimal(18, 4)) as ratio_before,
    try_cast(regexp_extract(ratio, '→([0-9.]+)株$', 1) as decimal(18, 4)) as ratio_after,
    try_cast(last_cum_rights_date as date)                               as last_cum_rights_date,
    try_cast(_loaded_at as timestamptz)                                  as _loaded_at
from {{ source('landing', 'mufg_stock_consolidations') }}
where file_date = (select max(file_date) from {{ source('landing', 'mufg_stock_consolidations') }})
