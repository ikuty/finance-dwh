-- EDINET CSV 明細の型付け・名寄せ版（incremental）。
--
-- source は landing.edinet_csv_facts（native、日付単位 load 済み）。
-- 取り込みは landing._load_log.loaded_at を透かしにして「未処理 or 再ロードされた日」の
-- ぶんだけ。名寄せの正しさのため、対象日のデータポイント・キーに一致する既存
-- （{{ this }}）行も combined に混ぜてから最新提出だけを残す（過去バックフィルで
-- 古い提出が来ても既存の新しい提出が勝つ）。冪等な delete+insert。
--
-- 連結/個別は EDINET CSV の「連結・個別」列をそのまま使う（EDINET 算出済み）。
-- 数値は「符号付き整数/小数だけ」を value_num に落とす（"－" 等は NULL）。

{{ config(
    materialized='incremental',
    unique_key=['edinet_code', 'element_id', 'context_id', 'consolidation'],
    incremental_strategy='delete+insert',
    indexes=[
        {'columns': ['edinet_code', 'element_id', 'context_id', 'consolidation']},
        {'columns': ['file_date']},
    ],
) }}

with load_log as (
    select file_date, loaded_at from {{ source('landing', 'edinet_csv_facts_load_log') }}
),

target_dates as (
    {% if is_incremental() %}
    select file_date
    from load_log
    where loaded_at > (select coalesce(max(_landing_loaded_at), timestamp '1900-01-01') from {{ this }})
    {% else %}
    select file_date from load_log
    {% endif %}
),

src as (
    select f.*, l.loaded_at as _landing_loaded_at
    from {{ source('landing', 'edinet_csv_facts') }} f
    join load_log l on l.file_date = f.file_date
    where f.file_date in (select file_date from target_dates)
),

docs as (
    select doc_id, sec_code, submit_date_time
    from {{ ref('cleansed__edinet__documents') }}
),

typed as (
    select
        f.file_date::date            as file_date,
        f.edinet_code,
        f.doc_id,
        d.sec_code,
        f.element_id,
        f.item_name,
        coalesce(nullif(f.context_id, ''), '')  as context_id,
        nullif(f.relative_year, '')             as relative_year,
        case f.consolidated_individual
            when '連結' then 'consolidated'
            when '個別' then 'non_consolidated'
            else 'other'
        end                          as consolidation,
        nullif(f.period_instant, '') as period_instant,
        nullif(f.unit_id, '')        as unit_id,
        nullif(f.unit, '')           as unit,
        f.value                      as value_text,
        case
            when f.value ~ '^-?[0-9]+(\.[0-9]+)?$' then f.value::numeric
        end                          as value_num,
        d.submit_date_time,
        f._landing_loaded_at
    from src f
    join docs d on d.doc_id = f.doc_id
),

{% if is_incremental() %}
existing as (
    select
        file_date, edinet_code, doc_id, sec_code, element_id, item_name,
        context_id, relative_year, consolidation, period_instant,
        unit_id, unit, value_text, value_num, submit_date_time, _landing_loaded_at
    from {{ this }}
    where (edinet_code, element_id, context_id, consolidation) in (
        select edinet_code, element_id, context_id, consolidation from typed
    )
),
combined as (
    select * from typed
    union all
    select * from existing
),
{% else %}
combined as (select * from typed),
{% endif %}

deduped as (
    select
        *,
        row_number() over (
            partition by edinet_code, element_id, context_id, consolidation
            order by submit_date_time desc nulls last, file_date desc, doc_id desc
        ) as _rn
    from combined
)

select
    file_date,
    edinet_code,
    doc_id,
    sec_code,
    element_id,
    item_name,
    context_id,
    relative_year,
    consolidation,
    period_instant,
    unit_id,
    unit,
    value_text,
    value_num,
    submit_date_time,
    _landing_loaded_at
from deduped
where _rn = 1
