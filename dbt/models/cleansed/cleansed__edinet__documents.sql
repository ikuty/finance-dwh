-- 書類一覧インデックスの型付け版（上場会社の書類のみ）。
-- FDW の raw__edinet_document_index を毎回フル rebuild する（983 JSON、実測 約11秒）。
-- 同じ docID が複数日の document_list.json に出現しうる（後日メタデータが編集され再掲載
-- されるため）ので、docID ごとに最新の掲載（file_date → ope_date_time）だけを残す。
-- raw の FDW は欠損を空文字で返す（SQL NULL ではない）ため nullif で正規化する。

{{ config(
    materialized='table',
    indexes=[
        {'columns': ['doc_id'], 'unique': true},
        {'columns': ['edinet_code']},
    ],
) }}

with src as (
    select * from {{ source('raw', 'raw__edinet_document_index') }}
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
)

select
    file_date::date                                                  as file_date,
    nullif(seq_number, '')::int                                      as seq_number,
    doc_id,
    edinet_code,
    sec_code,
    nullif(jcn, '')                                                  as jcn,
    filer_name,
    nullif(fund_code, '')                                            as fund_code,
    ordinance_code,
    form_code,
    doc_type_code,
    nullif(period_start, '')::date                                   as period_start,
    nullif(period_end, '')::date                                     as period_end,
    to_timestamp(nullif(submit_date_time, ''), 'YYYY-MM-DD HH24:MI') as submit_date_time,
    doc_description,
    nullif(parent_doc_id, '')                                        as parent_doc_id,
    (xbrl_flag = '1')                                                as has_xbrl,
    (pdf_flag = '1')                                                 as has_pdf,
    (csv_flag = '1')                                                 as has_csv,
    (withdrawal_status <> '0')                                       as is_withdrawn
from ranked
where _rn = 1
