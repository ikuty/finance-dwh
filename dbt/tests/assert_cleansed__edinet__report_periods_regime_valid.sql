-- regimeは filer_category='company' かつ fiscal_year が非NULLな行でのみ値
-- ('旧制度'/'移行期'/'新制度')を持ち、それ以外(fund、またはfiscal_year不明)の行では
-- 常にNULLであること。設計通りの動作を保証する。行が返れば失敗。

select
    doc_id,
    filer_category,
    fiscal_year,
    regime
from {{ ref('cleansed__edinet__report_periods') }}
where
    (filer_category = 'company' and fiscal_year is not null and regime not in ('旧制度', '移行期', '新制度'))
    or (filer_category = 'fund' and regime is not null)
    or (fiscal_year is null and regime is not null)
