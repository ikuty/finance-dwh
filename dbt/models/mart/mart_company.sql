-- 企業ディメンション。最新書類の属性 + 書類数。

with docs as (
    select * from {{ ref('cleansed__edinet__documents') }}
),

latest as (
    select distinct on (edinet_code)
        edinet_code,
        sec_code,
        filer_name,
        jcn,
        file_date as last_document_date
    from docs
    order by edinet_code, submit_date_time desc nulls last, file_date desc
),

counts as (
    select edinet_code, count(*) as document_count
    from docs
    group by edinet_code
)

select
    l.edinet_code,
    l.sec_code,
    l.filer_name,
    l.jcn,
    l.last_document_date,
    c.document_count
from latest l
join counts c using (edinet_code)
