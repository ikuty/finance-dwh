-- EDINET CSV 明細の型付け版。書類（cleansed__edinet__documents）と結合して
-- 上場会社ぶんに絞り、同一データポイントは最新提出の書類の値だけを残す。
--
-- 連結/個別は EDINET CSV の「連結・個別」列をそのまま使う（EDINET が算出済み。
-- 生 XBRL の contextRef を自前解釈する必要はない）。
-- 数値は「符号付き整数/小数だけ」の値のみ value_num に落とす（"－" 等は NULL）。

with facts as (
    select * from {{ source('raw', 'raw__edinet_csv_facts') }}
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
        nullif(f.context_id, '')     as context_id,
        nullif(f.relative_year, '')  as relative_year,
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
        d.submit_date_time
    from facts f
    join docs d on d.doc_id = f.doc_id
),

deduped as (
    select
        *,
        row_number() over (
            partition by edinet_code, element_id, context_id, consolidation
            order by submit_date_time desc nulls last, doc_id desc
        ) as _rn
    from typed
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
    submit_date_time
from deduped
where _rn = 1
