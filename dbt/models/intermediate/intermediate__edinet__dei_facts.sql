-- 書類(doc_id)単位のDEI(Document Entity Information)。会計基準・連結決算の有無を
-- cleansed__edinet__factsから直接取得する（推測しない）。J-GAAP/IFRS/US GAAP
-- 各intermediateモデル・martの両方から参照される共通部品。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/edinet_dei_facts.parquet',
    format='parquet'
) }}

select
    doc_id,
    max(case when item_name = '会計基準、DEI' then nullif(value_text, '－') end) as accounting_standard,
    max(case when item_name = '連結決算の有無、DEI' then
        case value_text when 'true' then true when 'false' then false end
    end) as has_consolidated
from {{ ref('cleansed__edinet__facts') }}
where item_name in ('会計基準、DEI', '連結決算の有無、DEI')
group by doc_id
