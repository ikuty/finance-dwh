-- EDINET CSV 明細の型付け・名寄せ版。
--
-- source は landing.edinet_csv_facts（Parquet、日付ごとに Prefect が更新）。
-- 当初は「DuckDB + Parquetは全量の読み込み自体が速い」という前提でexternal
-- materializationにより毎回全量rebuildしていたが、EDINETバックフィルが進み
-- landingが813日・2.8GBまで積み上がった結果、dbt build全体の所要時間の約95%
-- （980秒）をこのモデル単体が占めるようになった(2026-09-17実機判明)。incremental化した。
--
-- incremental化の要点:
--   - EDINETのlandingは未来方向だけでなく過去方向にも成長する(2022年分365日を
--     後からバックフィルした実績あり)。よって「file_date > 既存最大値」という
--     素朴な透かし方式は使えない。landingに存在するfile_date集合と{{ this }}に
--     存在するfile_date集合の差分を毎回計算し、フォワード・バックフィルを
--     問わず「まだ処理していないfile_date」だけを対象にする。
--   - 重複排除キー(edinet_code, element_id, context_id, consolidation)は
--     file_dateをまたいで成立する(原本と訂正報告書が別file_dateにあり得るため)。
--     新規file_date分の候補行は、対象キーで{{ this }}の現在の勝者と突き合わせ、
--     submit_date_timeがより新しい場合のみ置き換える(古ければ何も返さず、
--     既存行をそのまま温存する)。
--   - materialized='external'はDuckDB内部テーブルを持たないため incremental
--     戦略を使えない(dbt-duckdbのexternalマテリアライゼーションにincremental
--     相当が無いことを確認済み)。materialized='incremental'(DuckDBカタログ内の
--     ネイティブテーブル)に変更し、post_hookで従来通りのParquetファイルへ
--     エクスポートする(run_report.py等、下流はParquetファイルを直接読む契約の
--     ため)。
--   - landing側で既に処理済みのfile_dateの内容が後から`--force`等で修正された
--     場合(過去に2022年分EDINET一覧APIフレーキネスの復旧で発生した実績あり)、
--     file_date集合差分方式では検知できない。発生したら
--     `dbt run --full-refresh --select cleansed__edinet__facts`で手動フル
--     再構築すること。
--
-- 連結/個別は EDINET CSV の「連結・個別」列をそのまま使う（EDINET 算出済み）。
-- 数値化は try_cast に任せる（"－" 等の非数値は NULL、decimal(38,4) で桁落ちしない）。

{{ config(
    materialized='incremental',
    unique_key=['edinet_code', 'element_id', 'context_id', 'consolidation'],
    incremental_strategy='delete+insert',
    post_hook="COPY (select * from {{ this }}) TO '" ~ env_var('CLEANSED_ROOT', '/data/cleansed') ~ "/edinet_facts.parquet' (FORMAT PARQUET)"
) }}

with new_file_dates as (
    select distinct cast(file_date as date) as file_date
    from {{ source('landing', 'edinet_csv_facts') }}
    {% if is_incremental() %}
    except
    select distinct file_date from {{ this }}
    {% endif %}
),

facts as (
    select * from {{ source('landing', 'edinet_csv_facts') }}
    where cast(file_date as date) in (select file_date from new_file_dates)
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
),

candidates as (
    select * from deduped where _rn = 1
)

{% if is_incremental() %}

select
    c.file_date,
    c.edinet_code,
    c.doc_id,
    c.sec_code,
    c.element_id,
    c.item_name,
    c.context_id,
    c.relative_year,
    c.consolidation,
    c.period_instant,
    c.unit_id,
    c.unit,
    c.value_text,
    c.value_num,
    c.submit_date_time
from candidates c
left join {{ this }} existing
    on existing.edinet_code = c.edinet_code
    and existing.element_id = c.element_id
    and existing.context_id = c.context_id
    and existing.consolidation = c.consolidation
where existing.edinet_code is null
   or c.submit_date_time > existing.submit_date_time
   or (c.submit_date_time is not null and existing.submit_date_time is null)

{% else %}

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
from candidates

{% endif %}
