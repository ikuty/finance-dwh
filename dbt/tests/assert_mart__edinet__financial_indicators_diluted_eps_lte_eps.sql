-- 潜在株式調整後EPS(diluted_eps)は、潜在株式（新株予約権等）の希薄化効果により
-- 基本EPS(eps)以下になるはず。ただし当期純利益が赤字(eps<=0)の場合は、希薄化が
-- 損失を縮小させてしまう(反希薄化)ため会計基準上diluted_epsをepsと同値とする扱いが
-- 一般的で、この不等式は成立しない。よってeps>0の場合のみ対象とする。severity=warn
-- （四捨五入等による僅かな逆転もあり得るため、buildは失敗させない）。外部データ不要。

{{ config(severity='warn') }}

select doc_id, edinet_code, fiscal_year, period_type, eps, diluted_eps
from {{ ref('mart__edinet__financial_indicators') }}
where eps is not null
  and diluted_eps is not null
  and eps > 0
  and diluted_eps > eps
