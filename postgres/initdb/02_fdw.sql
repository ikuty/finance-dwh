-- raw 層の外部テーブル。レイクのファイルツリーを file_fdw の `program` オプションで
-- 読み、Python ラッパー(/opt/fdw/*.py)が CSV(全フィールドをクォート)を stdout に出す。
--
-- すべての列を text で受ける（raw は生データに忠実にし、型付けは dbt cleansed が行う）。
-- 列名・列順は各ラッパーの OUTPUT_HEADER と一致させること。
-- program のレイクルート引数は /lake 固定（docker compose がレイクをここへ bind mount する）。
--
-- 注意: file_fdw の program は述語プッシュダウン不可のため、SELECT のたびに
-- レイク全体を再走査する。詳細と回避策は docs/fdw_raw_layer_design.md。

CREATE EXTENSION IF NOT EXISTS file_fdw;

CREATE SERVER lake_files FOREIGN DATA WRAPPER file_fdw;


-- EDINET CSV(type=5) の全書類明細（縦持ち）。
CREATE FOREIGN TABLE raw.edinet_csv_facts (
    file_date               text,
    edinet_code             text,
    doc_id                  text,
    element_id              text,
    item_name               text,
    context_id              text,
    relative_year           text,
    consolidated_individual text,
    period_instant          text,
    unit_id                 text,
    unit                    text,
    value                   text
)
SERVER lake_files
OPTIONS (program 'python3 /opt/fdw/edinet_csv_fdw.py /lake', format 'csv');

COMMENT ON FOREIGN TABLE raw.edinet_csv_facts IS
    'レイクの edinet-dl/raw/{date}/{ec}/csv/{doc}/*.csv.gz を平坦化したもの。ラッパー: /opt/fdw/edinet_csv_fdw.py';


-- EDINET 書類一覧 API の生レスポンス results[]（1 行 = 1 書類）。
CREATE FOREIGN TABLE raw.edinet_document_index (
    file_date              text,
    seq_number             text,
    doc_id                 text,
    edinet_code            text,
    sec_code               text,
    jcn                    text,
    filer_name             text,
    fund_code              text,
    ordinance_code         text,
    form_code              text,
    doc_type_code          text,
    period_start           text,
    period_end             text,
    submit_date_time       text,
    doc_description        text,
    issuer_edinet_code     text,
    subject_edinet_code    text,
    subsidiary_edinet_code text,
    current_report_reason  text,
    parent_doc_id          text,
    ope_date_time          text,
    withdrawal_status      text,
    doc_info_edit_status   text,
    disclosure_status      text,
    xbrl_flag              text,
    pdf_flag               text,
    attach_doc_flag        text,
    english_doc_flag       text,
    csv_flag               text,
    legal_status           text
)
SERVER lake_files
OPTIONS (program 'python3 /opt/fdw/edinet_docindex_fdw.py /lake', format 'csv');

COMMENT ON FOREIGN TABLE raw.edinet_document_index IS
    'レイクの edinet-dl/raw/response/document_list_*.json の results[]。ラッパー: /opt/fdw/edinet_docindex_fdw.py';


-- JPX 日次相場表 PDF/TIFF のファイル目録（内容は含まない）。
CREATE FOREIGN TABLE raw.jpx_file_catalog (
    format        text,
    granularity   text,
    period        text,
    file_kind     text,
    relative_path text,
    byte_size     text,
    modified_at   text
)
SERVER lake_files
OPTIONS (program 'python3 /opt/fdw/jpx_catalog_fdw.py /lake', format 'csv');

COMMENT ON FOREIGN TABLE raw.jpx_file_catalog IS
    'レイクの jpx-daily-pdf-dl/raw/{形式}/... のファイル目録。ラッパー: /opt/fdw/jpx_catalog_fdw.py';
