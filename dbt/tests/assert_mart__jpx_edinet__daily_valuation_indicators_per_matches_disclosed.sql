-- 企業自己申告のPER(per)は決算期末日の終値を基準に計算されている（実データ検証、
-- docs/mart_indicators.md参照）。mart__jpx_edinet__daily_valuation_indicators自体は
-- 「取引日時点で参照可能な最新の開示」を使う設計（決算期末日ではなく開示日基準）の
-- ため、開示直後の行では約3ヶ月前の決算期末日ではなく現在の株価を使っており、
-- この突合には使えない。そのため決算期末日時点の終値を別途ASOF JOINで求め、
-- 自己申告PERとの整合性を検証する（結合キー・調整係数ロジックの健全性チェックを
-- 兼ねる）。相対誤差2%を許容しseverity=warnとする（buildは失敗させない）。

{{ config(severity='warn') }}

with edinet as (
    select
        doc_id, edinet_code, fiscal_year, period_type, period_end, eps, per,
        left(sec_code, 4) as jpx_code
    from {{ ref('mart__edinet__financial_indicators') }}
    where sec_code is not null and eps is not null and eps != 0 and per is not null
),

period_end_price as (
    select
        e.doc_id,
        p.close as period_end_close
    from edinet e
    asof left join (
        select code, file_date, coalesce(pm_close, am_close) as close
        from {{ ref('intermediate__jpx__daily_prices') }}
    ) p
        on e.jpx_code = p.code
        and p.file_date <= e.period_end
)

select
    e.doc_id, e.edinet_code, e.fiscal_year, e.period_type,
    e.per as per_disclosed,
    round(pep.period_end_close / e.eps, 4) as per_recomputed
from edinet e
join period_end_price pep on pep.doc_id = e.doc_id
where pep.period_end_close is not null
  and e.per != 0
  and abs(e.per - pep.period_end_close / e.eps) / abs(e.per) > 0.02
