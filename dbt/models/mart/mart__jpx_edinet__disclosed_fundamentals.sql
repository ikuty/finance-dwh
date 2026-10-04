-- 銘柄(jpx_code)×開示(doc_id)ごとの財務指標（EDINET開示ベース、株価に依存しない
-- 値のみ、毎回フル rebuild、Parquet）。mart__edinet__financial_indicatorsに
-- 銘柄コード変換(jpx_code)と、決算期末日時点の累積調整係数(period_end_cum_adj)を
-- 付与した薄い層。
--
-- mart__jpx_edinet__daily_valuation_indicators（日次grain）が「株価と連動する
-- 指標」のみを持つよう責務分離するために新設した（2026-09-26、ユーザー判断）。
-- eps/bps/sales/shares_outstanding/dividend_per_share等の開示値は決算期ごとに
-- 決まり株価とは無関係のため、日次grainに冗長に持たせず、この開示grainの
-- モデルに一元化してdoc_idで参照させる。
--
-- mart__edinet__financial_indicatorsをそのまま拡張せずこのモデルを分離した理由:
-- mart__edinet__financial_indicatorsはEDINET開示のみに依存する純粋なmartとして
-- 維持し、JPX価格調整（period_end_cum_adj）の概念を持ち込まない。
--
-- period_end_cum_adj: 決算期末日(period_end)時点（以前で直近の取引日）の累積
-- 調整係数（intermediate__jpx__daily_prices_adjusted.cum_adjustment_factor）を
-- ASOF JOINで取得したもの。period_endだけに依存しfile_dateには依存しないため、
-- この開示grainで一意に決まる（日次grain側のfile_date時点の累積調整係数との比が
-- adj_ratioになる。詳細はmart__jpx_edinet__daily_valuation_indicatorsのコメント
-- 参照）。EPS/BPS/1株配当の調整に使う（会社が期末後の分割を遡及適用する基準と
-- ex_rights_date基準が実データ上整合するため）。
--
-- shares_period_end_cum_adj（2026-10-04追加、重要）: 発行済株式数の調整専用の
-- 累積係数。上のperiod_end_cum_adjはex_rights_date（権利落ち日）基準だが、
-- 発行済株式数は分割・併合の効力発生日(effective_date、実際に株式数が変わる日)
-- 基準で調整する必要がある。この2つの基準日は通常1〜3日ズレ、決算期末日が
-- ちょうどこの隙間（ex_rights_date ≦ period_end ＜ effective_date）に入ると、
-- period_end_cum_adjを発行済株式数に流用した場合に調整漏れが起きる（実データで
-- 確認: NTT 2023年6月期・KDDI 2025年3月期ほか、全期間で781件の開示が該当。
-- 多くの企業が決算期末の翌月1日を分割の効力発生日に設定するため構造的に頻発
-- する）。intermediate__jpx__daily_prices_adjusted.shares_cum_adjustment_factor
-- （effective_date基準）をASOF JOINで取得する。

{{ config(
    materialized='external',
    location=env_var('MART_ROOT', '/data/mart') ~ '/jpx_edinet_disclosed_fundamentals.parquet',
    format='parquet'
) }}

with edinet as (
    select
        doc_id, edinet_code, sec_code, filer_name, fiscal_year, period_type,
        period_end, submit_date_time, bps, eps, sales, shares_outstanding, dividend_per_share,
        left(sec_code, 4) as jpx_code
    from {{ ref('mart__edinet__financial_indicators') }}
    where sec_code is not null
)

select
    e.doc_id,
    e.edinet_code,
    e.sec_code,
    e.jpx_code,
    e.filer_name,
    e.fiscal_year,
    e.period_type,
    e.period_end,
    e.submit_date_time,
    e.eps,
    e.bps,
    e.sales,
    e.shares_outstanding,
    e.dividend_per_share,
    p.cum_adjustment_factor as period_end_cum_adj,
    p.shares_cum_adjustment_factor as shares_period_end_cum_adj
from edinet e
asof left join {{ ref('intermediate__jpx__daily_prices_adjusted') }} p
    on e.jpx_code = p.code
    and p.file_date <= e.period_end
