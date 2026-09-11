-- EDINET CSV 明細の型付け・名寄せ版。
--
-- source は landing.edinet_csv_facts（Parquet、日付ごとに Prefect が更新）。
-- Postgres 版では file_fdw の全量スキャン（実測 約12分）を避けるために incremental
-- （delete+insert・透かし・既存行の再結合）にしていたが、DuckDB + Parquet は
-- 全量の読み込み自体が速い（landing を直接 glob で読むだけ）ため、
-- まずは常に全量 rebuild する external materialization にする。実データ規模
-- （2026-09-11時点 Postgres 実測: landing 20.5M行）で許容できない遅さになったら
-- 日付パーティション単位の再構築（landing と同じ「対象日だけ書き直す」方式）へ
-- 切り替える（docs/raw_landing_design.md 参照）。
--
-- 連結/個別は EDINET CSV の「連結・個別」列をそのまま使う（EDINET 算出済み）。
-- 数値化は try_cast に任せる（"－" 等の非数値は NULL、decimal(38,4) で桁落ちしない）。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/edinet_facts.parquet',
    format='parquet'
) }}

with facts as (
    select * from {{ source('landing', 'edinet_csv_facts') }}
),

docs as (
    select doc_id, sec_code, submit_date_time
    from {{ ref('cleansed__edinet__documents') }}
),

typed as (
    select
        cast(f.file_date as date)               as file_date,
        f.edinet_code,
        f.doc_id,
        d.sec_code,
        f.element_id,
        f.item_name,
        coalesce(nullif(f.context_id, ''), '')   as context_id,
        nullif(f.relative_year, '')              as relative_year,
        case f.consolidated_individual
            when '連結' then 'consolidated'
            when '個別' then 'non_consolidated'
            else 'other'
        end                                      as consolidation,
        nullif(f.period_instant, '')             as period_instant,
        nullif(f.unit_id, '')                    as unit_id,
        nullif(f.unit, '')                       as unit,
        f.value                                  as value_text,
        try_cast(f.value as decimal(38, 4))      as value_num,
        d.submit_date_time
    from facts f
    join docs d on d.doc_id = f.doc_id
),

deduped as (
    select
        *,
        row_number() over (
            partition by edinet_code, element_id, context_id, consolidation
            order by submit_date_time desc nulls last, file_date desc, doc_id desc
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
