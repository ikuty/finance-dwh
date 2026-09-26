-- 銘柄(jpx_code)×取引日(file_date)ごとの株価評価指標（毎回フル rebuild、Parquet）。
-- mart__jpx_edinet__valuation_indicators（銘柄×期grain、決算期末日/直近営業日の
-- 2基準が1行に同居し解釈が難しいと判明）を置き換える（2026-09-25、ユーザー判断）。
-- 日次grainにすることで「最新」は単に最終行になり、決算期末日時点のスナップショット
-- も日次系列から該当日を1行引くだけで得られる。任意日の推移も取得できる。
--
-- 責務分離(2026-09-26、ユーザー判断): 株価(close)と連動して日次で変化する指標
-- （pbr/per/market_cap/psr/earnings_yield/dividend_yield、および分割・併合調整
-- 済みの*_adjusted列）だけをこのモデルに残す。eps/bps/sales/shares_outstanding/
-- dividend_per_share等の開示値そのものや、fiscal_year/period_type/period_end/
-- submit_date_time等の開示メタデータは、決算期・分割イベントが起きない限り値が
-- 変わらず日次grainでは冗長なため、開示grainのmart__jpx_edinet__disclosed_
-- fundamentalsへ切り出した。それらが必要な場合はdoc_idで結合する。
--
-- 「その取引日時点で参照可能な最新の開示」の判定(重要、docs/mart_indicators.md
-- 「今後の拡張候補」で事前検討済み):
--   決算期末日ではなく submit_date_time（開示日）を基準にする。決算期末日を基準に
--   すると、実際にはまだ開示されていない数値を先読みしてしまう（実データで通期は
--   約87〜90日、四半期でも約42〜43日のディスクロージャーラグを確認済み）。
--   ASOF JOIN（submit_date_time <= file_date、直近1件）で判定する。
--
-- 分割・併合をまたぐ期間のEPS/BPS/発行済株式数の調整(重要):
--   株価はintermediate__jpx__daily_prices_adjustedの累積調整係数
--   （cum_adjustment_factor、対象日より後の分割・併合の影響を織り込んだ係数）で
--   既に調整済みだが、EPS/BPS/shares_outstandingは開示時点の株式数のまま。
--   ある開示の後（period_end後）に分割・併合が起きると、次の開示までの間は
--   「調整後株価 ÷ 未調整EPS」で計算が歪む（分割比率の分だけPERが不自然に
--   高く/低く出る）。
--
--   開示の権利落ち日基準の累積調整係数(period_end_cum_adj、mart__jpx_edinet__
--   disclosed_fundamentalsで開示grainとして事前計算済み)と、対象取引日自身の
--   累積調整係数(file_date_cum_adj)の比を使い、EPS/BPSを対象取引日の株式数基準へ
--   変換する:
--     adj_ratio = period_end_cum_adj / file_date_cum_adj
--     eps_adjusted = eps × adj_ratio
--     bps_adjusted = bps × adj_ratio
--   発行済株式数は逆方向（分割で株数が増える）のため、adj_ratioの逆数を掛ける:
--     shares_outstanding_adjusted = shares_outstanding / adj_ratio
--   （分割・併合が対象取引日までに無ければ両cum_adjは等しくadj_ratio=1、無調整。
--   検証: 1:2分割(adj_factor=0.5)の場合、period_end基準cum_adj=0.5・file_date基準
--   cum_adj=1.0(分割後)ならadj_ratio=0.5、eps_adjustedは半分（1株あたりの分母である
--   株数が倍になるため妥当）、shares_outstanding_adjustedは2倍（実際に株数が倍に
--   なるため妥当）と、符号・方向とも整合することを確認済み）。
--
-- sales（売上高）・trading_value等、企業単位の総額指標は株式数に依存しないため
-- 調整不要（disclosed値をそのまま使う）。
--
-- per・earnings_yieldは「実績EPS」ベース(重要、2026-09-25実データ検証):
--   ここでのeps（延いてはper・earnings_yield）は、EDINET開示（有価証券報告書・
--   四半期報告書）に基づく確定済み実績値であり、証券会社サイト等で一般的に
--   デフォルト表示される「予想PER」（会社が発表した進行期の業績予想EPSを使う）
--   とは異なる。実機で3087（ドトール・日レスホールディングス）を例に、株価3,210円
--   時点の各サイトの表示を突合した:
--     - かぶたん(17.9倍)・Yahoo!ファイナンス(17.85倍、UI上に「会社予想」と明記):
--       進行期（2027.02期）の会社予想EPS(179.6円)を使用。3210/179.6≒17.88で一致。
--     - みんかぶ(18.8倍): 直近確定期（2026.02期）の実績EPS(170.7円)を使用。
--       3210/170.7≒18.81で一致。このmartの計算方式と同じ。
--   会社予想EPSは決算短信・適時開示の「業績予想」欄に記載される情報で、EDINETの
--   有価証券報告書・四半期報告書には通常含まれないため、現状のデータソースからは
--   予想PERを算出できない（別データソースが必要、未対応）。
--
-- dividend_per_share_adjusted・dividend_yield(2026-09-25追加): EDINETの１株当たり
-- 配当額（経営指標等表由来、開示時点の株式数基準）を、eps_adjustedと同じadj_ratio
-- で対象取引日の株式数基準へ変換した上でdividend_per_share_adjustedとし、
-- dividend_yield = dividend_per_share_adjusted / closeとする。

{{ config(
    materialized='external',
    location=env_var('MART_ROOT', '/data/mart') ~ '/jpx_edinet_daily_valuation_indicators.parquet',
    format='parquet'
) }}

with prices as (
    select
        code,
        file_date,
        coalesce(pm_close, am_close) as close,
        cum_adjustment_factor as file_date_cum_adj
    from {{ ref('intermediate__jpx__daily_prices_adjusted') }}
),

fundamentals as (
    select
        jpx_code, doc_id, submit_date_time,
        eps, bps, sales, shares_outstanding, dividend_per_share, period_end_cum_adj
    from {{ ref('mart__jpx_edinet__disclosed_fundamentals') }}
),

-- 各取引日について、その日「時点で開示済み」の直近の開示をASOF JOINで判定する。
daily as (
    select
        p.code as jpx_code,
        p.file_date,
        p.close,
        p.file_date_cum_adj,
        f.doc_id,
        f.eps, f.bps, f.sales, f.shares_outstanding, f.dividend_per_share,
        f.period_end_cum_adj
    from prices p
    asof left join fundamentals f
        on p.code = f.jpx_code
        and f.submit_date_time <= p.file_date
)

select
    jpx_code,
    file_date,
    close,
    doc_id,
    case when period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then period_end_cum_adj / file_date_cum_adj end as adj_ratio,
    case when period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then eps * period_end_cum_adj / file_date_cum_adj end as eps_adjusted,
    case when period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then bps * period_end_cum_adj / file_date_cum_adj end as bps_adjusted,
    case when period_end_cum_adj is not null and file_date_cum_adj is not null and period_end_cum_adj != 0
        then shares_outstanding * file_date_cum_adj / period_end_cum_adj end as shares_outstanding_adjusted,
    case when period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then dividend_per_share * period_end_cum_adj / file_date_cum_adj end as dividend_per_share_adjusted,
    case when bps is not null and bps != 0 and period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then close / (bps * period_end_cum_adj / file_date_cum_adj) end as pbr,
    case when eps is not null and eps != 0 and period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then close / (eps * period_end_cum_adj / file_date_cum_adj) end as per,
    case when shares_outstanding is not null and period_end_cum_adj is not null and file_date_cum_adj is not null and period_end_cum_adj != 0
        then close * (shares_outstanding * file_date_cum_adj / period_end_cum_adj) end as market_cap,
    case when sales is not null and sales != 0 and shares_outstanding is not null and period_end_cum_adj is not null and file_date_cum_adj is not null and period_end_cum_adj != 0
        then (close * (shares_outstanding * file_date_cum_adj / period_end_cum_adj)) / sales end as psr,
    case when eps is not null and close is not null and close != 0 and period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then (eps * period_end_cum_adj / file_date_cum_adj) / close end as earnings_yield,
    case when dividend_per_share is not null and close is not null and close != 0 and period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then (dividend_per_share * period_end_cum_adj / file_date_cum_adj) / close end as dividend_yield
from daily
