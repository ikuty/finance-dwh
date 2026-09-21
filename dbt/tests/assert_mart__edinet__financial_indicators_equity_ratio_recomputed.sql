-- 自己資本比率(equity_ratio)の報告値と、net_assets/total_assetsによる再計算値を
-- 突合する。両者は完全一致しない場合がある（equity_ratioの分子は非支配株主持分を
-- 除いた自己資本、net_assetsは非支配株主持分を含む純資産のため）ため、許容誤差を
-- 広め（15ポイント）に取り、severity=warnとする（buildは失敗させない、目視確認用）。
-- 外部データ不要、当システム内部の整合性のみで検証する。

{{ config(severity='warn') }}

select
    doc_id, edinet_code, fiscal_year, period_type,
    equity_ratio, net_assets, total_assets,
    round(net_assets / total_assets, 4) as recomputed_equity_ratio
from {{ ref('mart__edinet__financial_indicators') }}
where equity_ratio is not null
  and net_assets is not null
  and total_assets is not null
  and total_assets != 0
  and abs(equity_ratio - net_assets / total_assets) > 0.15
