-- BPS(bps)の報告値と、net_assets/shares_outstandingによる簡易再計算値を突合する。
-- 両者とも期末時点の値のため理論上近い値になるはずだが、報告値のBPSは通常自己株式を
-- 除いた株式数を分母とするのに対し、shares_outstanding(発行済株式総数)は自己株式を
-- 含む総数のため、自己株式保有比率が高い企業ほど乖離しうる。相対誤差20%を許容し
-- severity=warnとする（buildは失敗させない、目視確認用）。外部データ不要、当システム
-- 内部の整合性のみで検証する。

{{ config(severity='warn') }}

select
    doc_id, edinet_code, fiscal_year, period_type,
    bps, net_assets, shares_outstanding,
    round(net_assets / shares_outstanding, 4) as recomputed_bps
from {{ ref('mart__edinet__financial_indicators') }}
where bps is not null
  and net_assets is not null
  and shares_outstanding is not null
  and shares_outstanding != 0
  and bps != 0
  and abs(bps - net_assets / shares_outstanding) / abs(bps) > 0.20
