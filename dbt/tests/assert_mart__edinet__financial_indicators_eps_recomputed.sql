-- EPS(eps)の報告値と、net_income/shares_outstandingによる簡易再計算値を突合する。
-- 報告値のEPSは期中加重平均株式数を分母とするのに対し、shares_outstanding
-- (発行済株式総数)は期末時点の値のため、期中に増資・自己株式取得・株式分割等が
-- あった期は構造的に乖離する。相対誤差20%を許容しseverity=warnとする（buildは
-- 失敗させない、目視確認用）。外部データ不要、当システム内部の整合性のみで検証する。

{{ config(severity='warn') }}

select
    doc_id, edinet_code, fiscal_year, period_type,
    eps, net_income, shares_outstanding,
    round(net_income / shares_outstanding, 4) as recomputed_eps
from {{ ref('mart__edinet__financial_indicators') }}
where eps is not null
  and net_income is not null
  and shares_outstanding is not null
  and shares_outstanding != 0
  and eps != 0
  and abs(eps - net_income / shares_outstanding) / abs(eps) > 0.20
