-- 書類一覧インデックスの型付け版（上場会社・REITの書類）。
-- raw__edinet_document_index（DuckDB でレイクの JSON を毎回直接読む view）を
-- 元に、型付け・名寄せして Parquet へ書き出す（external materialization、
-- 常に全量 rebuild。DuckDB は高速なので incremental 化は不要と判断）。
--
-- 同じ docID が複数日の document_list.json に出現しうる（後日メタデータが編集され
-- 再掲載されるため）ので、docID ごとに最新の掲載（file_date → ope_date_time）だけ残す。
--
-- REIT（投資法人）対応（2026-10-12追加）:
--   EDINETの書類一覧APIはREIT（投資法人）のsec_codeを常にnullで返す（実機確認、
--   edinet-dl側のCLAUDE.md参照）ため、sec_code is not nullだけの絞り込みでは
--   REITの書類が一件も残らなかった。form_codeが'07B'/'10B'（REIT等の投資証券の
--   有報/半期報告書、訂正含む）で始まる書類も対象に含める。詳細・実機確認結果は
--   edinet-dl側の services/ir-disclosure-dl/docs/reit_support_design.md 参照。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/edinet_documents.parquet',
    format='parquet'
) }}

with src as (
    select * from {{ ref('raw__edinet_document_index') }}
),

ranked as (
    select
        *,
        row_number() over (
            partition by doc_id
            order by file_date desc, nullif(ope_date_time, '') desc nulls last
        ) as _rn
    from src
    where nullif(sec_code, '') is not null
       or form_code like '07B%'
       or form_code like '10B%'
)

select
    cast(file_date as date)                          as file_date,
    try_cast(seq_number as integer)                  as seq_number,
    doc_id,
    edinet_code,
    sec_code,
    nullif(jcn, '')                                  as jcn,
    filer_name,
    nullif(fund_code, '')                            as fund_code,
    ordinance_code,
    form_code,
    doc_type_code,
    try_cast(nullif(period_start, '') as date)       as period_start,
    try_cast(nullif(period_end, '') as date)         as period_end,
    try_strptime(nullif(submit_date_time, ''), '%Y-%m-%d %H:%M') as submit_date_time,
    doc_description,
    nullif(parent_doc_id, '')                        as parent_doc_id,
    (xbrl_flag = '1')                                as has_xbrl,
    (pdf_flag = '1')                                 as has_pdf,
    (csv_flag = '1')                                 as has_csv,
    (withdrawal_status <> '0')                       as is_withdrawn
from ranked
where _rn = 1
