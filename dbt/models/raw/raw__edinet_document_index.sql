-- 書類一覧 API の生レスポンス（response/{yyyy}/{mm}/{dd}/document_list.json）を
-- DuckDB のネイティブ JSON リーダーで直接読む。CSV と違い JSON の構造は素直で、
-- Python ラッパー・landing のような中間ステップは不要（file_fdw 版は約11秒/983ファイル
-- だった。DuckDB のネイティブ読み取りはそれよりオーバーヘッドが小さい想定）。
-- 全列を文字列にキャストし、raw 層の「生データに忠実」を踏襲する（型付けは cleansed）。

{{ config(materialized='view') }}

with files as (
    select
        filename,
        regexp_extract(filename, '(\d{4})/(\d{2})/(\d{2})/document_list\.json$', 1) || '-' ||
        regexp_extract(filename, '(\d{4})/(\d{2})/(\d{2})/document_list\.json$', 2) || '-' ||
        regexp_extract(filename, '(\d{4})/(\d{2})/(\d{2})/document_list\.json$', 3) as file_date,
        unnest(results, recursive := true)
    from read_json_auto(
        '{{ env_var("LAKE_ROOT", "/lake") }}/edinet-dl/raw/response/*/*/*/document_list.json',
        filename := true
    )
)

select
    file_date,
    cast("seqNumber" as varchar)  as seq_number,
    cast("docID" as varchar)      as doc_id,
    cast("edinetCode" as varchar) as edinet_code,
    cast("secCode" as varchar)    as sec_code,
    cast("JCN" as varchar)        as jcn,
    cast("filerName" as varchar)  as filer_name,
    cast("fundCode" as varchar)   as fund_code,
    cast("ordinanceCode" as varchar) as ordinance_code,
    cast("formCode" as varchar)      as form_code,
    cast("docTypeCode" as varchar)   as doc_type_code,
    cast("periodStart" as varchar)   as period_start,
    cast("periodEnd" as varchar)     as period_end,
    cast("submitDateTime" as varchar) as submit_date_time,
    cast("docDescription" as varchar) as doc_description,
    cast("issuerEdinetCode" as varchar)    as issuer_edinet_code,
    cast("subjectEdinetCode" as varchar)   as subject_edinet_code,
    cast("subsidiaryEdinetCode" as varchar) as subsidiary_edinet_code,
    cast("currentReportReason" as varchar) as current_report_reason,
    cast("parentDocID" as varchar)   as parent_doc_id,
    cast("opeDateTime" as varchar)   as ope_date_time,
    cast("withdrawalStatus" as varchar)  as withdrawal_status,
    cast("docInfoEditStatus" as varchar) as doc_info_edit_status,
    cast("disclosureStatus" as varchar)  as disclosure_status,
    cast("xbrlFlag" as varchar)      as xbrl_flag,
    cast("pdfFlag" as varchar)       as pdf_flag,
    cast("attachDocFlag" as varchar) as attach_doc_flag,
    cast("englishDocFlag" as varchar) as english_doc_flag,
    cast("csvFlag" as varchar)       as csv_flag,
    cast("legalStatus" as varchar)   as legal_status
from files
