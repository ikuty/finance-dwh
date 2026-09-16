-- 三菱UFJ eスマート証券(kabu.com)の商号変更ページの型付け版。
-- landing.mufg_company_name_changes(Parquet)を型付けするだけのexternal
-- materialization。cleansed__mufg__stock_splitsと同じ理由で、cleansedは
-- landingの最新file_dateのみを読む。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/mufg_company_name_changes.parquet',
    format='parquet'
) }}

select
    cast(file_date as date)             as file_date,
    try_cast(change_date as date)       as change_date,
    code,
    old_name,
    new_name,
    try_cast(_loaded_at as timestamptz) as _loaded_at
from {{ source('landing', 'mufg_company_name_changes') }}
where file_date = (select max(file_date) from {{ source('landing', 'mufg_company_name_changes') }})
