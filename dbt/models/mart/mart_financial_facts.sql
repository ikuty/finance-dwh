-- 財務ファクト（縦持ち）。cleansed の明細を、採用要素のホワイトリスト
-- （seed: element_whitelist）で絞り、企業名を付与したもの。
-- ワイド（財務諸表形式）へのピボットは後続作業（採用要素の選定 = ウェアハウス設計）。

with facts as (
    select * from {{ ref('cleansed__edinet__facts') }}
),

whitelist as (
    select element_id, metric_name from {{ ref('element_whitelist') }}
)

select
    f.edinet_code,
    f.sec_code,
    c.filer_name,
    w.metric_name,
    f.element_id,
    f.item_name,
    f.context_id,
    f.relative_year,
    f.consolidation,
    f.period_instant,
    f.unit,
    f.value_num,
    f.value_text,
    f.doc_id,
    f.file_date,
    f.submit_date_time
from facts f
join whitelist w on w.element_id = f.element_id
left join {{ ref('mart_company') }} c on c.edinet_code = f.edinet_code
