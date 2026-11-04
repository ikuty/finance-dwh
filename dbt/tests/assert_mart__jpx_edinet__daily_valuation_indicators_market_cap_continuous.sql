-- 同じ開示(doc_id)を参照している連続2取引日で、時価総額が2倍超または半分未満に変わる行を
-- 検出する再発検知テスト(issue #83)。同じ開示なら株数は同じなので、変化は株価の変動
-- だけのはずで、分割比分の跳ねは株数・株価の調整基準のズレを示す(例: 権利落ち日〜効力
-- 発生日に「分割後の株価 × 分割前の株数」になっていた京王電鉄9008の時価総額)。
--
-- 実データ(2026-11-04)での検証: 修正前は1,556件(830銘柄)、修正後は23件(17銘柄)。
-- 残りは時価総額が数億円規模の小型株の急騰落で、100億円未満(前日値)は対象外にして
-- ノイズを除く。dividend_yieldの同一開示内の急変は修正前後とも1件のみで検知力が
-- 無いため、このテストの対象にしない(配当の調整ズレは開示切替日に現れるため)。
-- 相場急変で正当に起こりうるためseverity=warn(buildは失敗させない)。

{{ config(severity='warn') }}

with t as (
    select
        jpx_code,
        file_date,
        doc_id,
        market_cap,
        lag(market_cap) over (partition by jpx_code order by file_date) as prev_market_cap,
        lag(doc_id) over (partition by jpx_code order by file_date) as prev_doc_id
    from {{ ref('mart__jpx_edinet__daily_valuation_indicators') }}
)

select jpx_code, file_date, doc_id, prev_market_cap, market_cap
from t
where doc_id = prev_doc_id
  and prev_market_cap >= 10000000000
  and market_cap > 0
  and (market_cap / prev_market_cap > 2 or market_cap / prev_market_cap < 0.5)
