-- JPX ファイル目録の型付け版。period（'YYYY-MM' or 'YYYY-MM-DD'）から
-- 実日付 period_date を作る（月次は月初日）。

with src as (
    select * from {{ source('raw', 'raw__jpx_file_catalog') }}
)

select
    format,
    granularity,
    period,
    case
        when granularity = 'monthly' then to_date(period, 'YYYY-MM')
        else to_date(period, 'YYYY-MM-DD')
    end                          as period_date,
    file_kind,
    relative_path,
    byte_size::bigint            as byte_size,
    modified_at::timestamptz     as modified_at
from src
