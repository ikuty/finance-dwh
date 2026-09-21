-- ROE(roe)の報告値と、net_income/net_assetsによる簡易再計算値を突合する。報告値は
-- 通常期首・期末平均の自己資本を分母とするが、ここでは期末値のみで簡易比較するため
-- 完全一致は期待しない。許容誤差を広め（5ポイント）に取り、severity=warnとする
-- （buildは失敗させない、目視確認用）。外部データ不要、当システム内部の整合性の
-- みで検証する。

{{ config(severity='warn') }}

select
    doc_id, edinet_code, fiscal_year, period_type,
    roe, net_income, net_assets,
    round(net_income / net_assets, 4) as recomputed_roe
from {{ ref('mart__edinet__financial_indicators') }}
where roe is not null
  and net_income is not null
  and net_assets is not null
  and net_assets != 0
  and abs(roe - net_income / net_assets) > 0.05
