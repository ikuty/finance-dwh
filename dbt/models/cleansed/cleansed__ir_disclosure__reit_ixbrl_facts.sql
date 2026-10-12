-- ir-disclosure-dlが取得するREIT(投資法人)決算短信iXBRLの型付け版。
-- landing.ir_disclosure_reit_ixbrl_facts（Parquet、日付ごとにPrefectが更新）を
-- 型付けするだけのexternal materialization（cleansed__ir_disclosure__kessan_tanshin
-- と同じ理由、常に全量rebuild）。
--
-- 粒度は1行=1書類(docid)内の1要素×1context（cleansed__edinet__factsと同じ縦持ち）。
-- 事業会社向けのcleansed__ir_disclosure__kessan_tanshin（1行=1書類、固定列）とは
-- 形が異なる。REITのiXBRLは要素数・contextRef(当期/前期/予想等)の組み合わせが
-- 書類ごとに変わりうるため（詳細はir_disclosure_reit_ixbrl.pyのdocstring参照）。
--
-- どのcontext_id（当期の実績か等）を使うかの解釈はここでは行わない（レイク層・
-- landing層と同じ「解釈しすぎない」方針）。intermediate層（Phase 3の新規モデル）が
-- edinet_code×period_endで解釈する。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/ir_disclosure_reit_ixbrl_facts.parquet',
    format='parquet'
) }}

select
    cast(file_date as date)        as file_date,
    docid,
    edinet_code,
    sec_code,
    try_cast(period_end as date)   as period_end,
    element_id,
    context_id,
    unit_ref,
    try_cast(value_num as double)  as value_num
from {{ source('landing', 'ir_disclosure_reit_ixbrl_facts') }}
