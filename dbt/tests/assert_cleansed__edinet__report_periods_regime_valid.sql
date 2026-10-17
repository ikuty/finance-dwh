-- regimeは filer_category='company' かつ fiscal_year が非NULLな行でのみ値
-- ('旧制度'/'移行期'/'新制度')を持ち、それ以外(fund・reit・infra_fund・etf、または
-- fiscal_year不明)の行では常にNULLであること。設計通りの動作を保証する。
-- 行が返れば失敗。
-- reit・infra_fund・etfはfiscal_yearが非NULLになる(docDescriptionに「第X期」を含む
-- ため、company同様に抽出される)ため、fiscal_year is nullの判定だけでは検知
-- できず明示的なチェックが必要(reitは2026-10-12、infra_fundは2026-10-13、
-- etfは2026-10-17追加)。

select
    doc_id,
    filer_category,
    fiscal_year,
    regime
from {{ ref('cleansed__edinet__report_periods') }}
where
    (filer_category = 'company' and fiscal_year is not null and regime not in ('旧制度', '移行期', '新制度'))
    or (filer_category = 'fund' and regime is not null)
    or (filer_category = 'reit' and regime is not null)
    or (filer_category = 'infra_fund' and regime is not null)
    or (filer_category = 'etf' and regime is not null)
    or (fiscal_year is null and regime is not null)
