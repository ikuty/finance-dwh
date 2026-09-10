-- 書類一覧インデックスの型付け版。上場会社の書類（sec_code あり）のみに絞る。
-- raw の FDW は欠損を空文字で返す（SQL NULL ではない）ため nullif で正規化する。

with src as (
    select * from {{ source('raw', 'raw__edinet_document_index') }}
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
    nullif(issuer_edinet_code, '')                                   as issuer_edinet_code,
    nullif(subject_edinet_code, '')                                  as subject_edinet_code,
    nullif(subsidiary_edinet_code, '')                               as subsidiary_edinet_code,
    nullif(current_report_reason, '')                                as current_report_reason,
    nullif(parent_doc_id, '')                                        as parent_doc_id,
    (xbrl_flag = '1')                                                as has_xbrl,
    (pdf_flag = '1')                                                 as has_pdf,
    (csv_flag = '1')                                                 as has_csv,
    (english_doc_flag = '1')                                         as has_english_doc,
    (withdrawal_status <> '0')                                       as is_withdrawn
from src
where nullif(sec_code, '') is not null
