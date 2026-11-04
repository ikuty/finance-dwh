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
--
--   発行済株式数と1株配当は別の係数を使う(2026-10-04修正、2026-11-04再修正、重要):
--   上のperiod_end_cum_adj/file_date_cum_adjはex_rights_date（権利落ち日、株価が
--   調整後水準になる日）基準だが、決算期末日時点の「実際の」発行済株式数・「実際に
--   支払われた」1株配当は、分割・併合の効力発生日(effective_date、実際に株式数が
--   変わる日)基準で換算する必要がある。この2つの基準日は通常1〜3日ズレ、決算期末日が
--   ちょうどこの隙間（ex_rights_date ≦ period_end ＜ effective_date）に入る開示が
--   全期間で約780件ある（NTT 2023年6月期・KDDI 2025年3月期・京王電鉄2026年3月期ほか。
--   多くの企業が決算期末の翌月1日を分割の効力発生日に設定するため構造的に頻発する）。
--
--   - 決算期末日側: shares_period_end_cum_adj（効力発生日基準。期末時点の株数・配当は
--     期末後の分割をまだ織り込んでいない実額のため）。
--   - 取引日側: file_date_cum_adj（権利落ち日基準。close自体がこの係数で調整済みの
--     水準のため、同じ基準で揃える。効力発生日基準を使うと、権利落ち日〜効力発生日の
--     数日間は「分割後の株価 × 分割前の株数」になり時価総額が分割比分だけ過小になる）。
--     shares_adj_ratio = shares_period_end_cum_adj / file_date_cum_adj
--     shares_outstanding_adjusted = shares_outstanding / shares_adj_ratio
--     dividend_per_share_adjusted  = dividend_per_share × shares_adj_ratio
--   （market_cap/psrはshares_outstanding_adjusted、dividend_yieldは配当側を使う。
--   分割・併合が無ければshares_adj_ratio=1で無調整。変わるのは上記の隙間に期末が
--   入る開示と、権利落ち日〜効力発生日の取引日のみ。検証: 1:2分割(adj_factor=0.5)
--   で期末が効力発生日より前なら、shares_adj_ratio=0.5、株数は2倍・配当は半分と、
--   符号・方向とも整合する。）
--
--   EPS/BPSはadj_ratio（権利落ち日基準）のまま: 会社が期末後の分割をEPS/BPSには
--   決算短信・有報で遡及適用して開示するため、期末が隙間に入る場合も係数1で正しい。
--   一方1株配当は「実際に支払った額」が開示される（京王電鉄の決算短信注記: 「株式
--   分割前の実際の配当金の額を記載」）ため、EPS/BPSと同じadj_ratioで調整すると分割前の
--   額のまま残り、配当利回りが分割比分だけ過大になっていた（京王電鉄9008: 2.9%のところ
--   14.3%）。隙間に期末が入る年次開示279件のうち、前期の配当額と比べて遡及修正の疑い
--   があるのは7件のみ（4439・9368・9780・9223・9107・7071・4055、いずれも2021〜2025年。
--   272件は分割前の実額で開示）。この7件は二重に調整され配当が過小になるが、
--   個別判定の手段が無く既知の限界とする。
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
-- 配当額（経営指標等表由来、開示時点の株式数基準）を、shares_adj_ratio（上の
-- 「発行済株式数と1株配当は別の係数を使う」参照、2026-11-04変更）で対象取引日の
-- 株式数基準へ変換した上でdividend_per_share_adjustedとし、
-- dividend_yield = dividend_per_share_adjusted / closeとする。
--
-- per/psr/dividend_yield/earnings_yieldはTTM（直近12か月）ベース(2026-11-01変更、
-- 重要): 四半期・半期開示のeps/sales/dividend_per_shareは期首からの累積値のため、
-- これらをそのまま分母にすると通期開示の度に値が不連続に跳ねる（実データ検証:
-- ジャパニアス(9558)でPERが年2回切り替わることを確認、詳細はmart__jpx_edinet__
-- disclosed_fundamentalsのコメント参照）。disclosed_fundamentals側で事前計算した
-- eps_ttm/sales_ttm/dividend_per_share_ttmを使う。bps（期末時点のスナップショット、
-- 累積値ではない）はTTM化の対象外のためpbrは変更なし。
-- eps_ttm_adjustedはeps_adjusted等と同じadj_ratio、dividend_per_share_ttm_adjusted
-- はshares_adj_ratioで対象取引日の株式数基準へ変換した値（透明性のため列として公開する）。

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
        eps, bps, sales, shares_outstanding, dividend_per_share,
        eps_ttm, sales_ttm, dividend_per_share_ttm,
        period_end_cum_adj, shares_period_end_cum_adj
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
        f.eps_ttm, f.sales_ttm, f.dividend_per_share_ttm,
        f.period_end_cum_adj, f.shares_period_end_cum_adj
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
    case when shares_period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then shares_period_end_cum_adj / file_date_cum_adj end as shares_adj_ratio,
    case when period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then eps * period_end_cum_adj / file_date_cum_adj end as eps_adjusted,
    case when period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then bps * period_end_cum_adj / file_date_cum_adj end as bps_adjusted,
    case when shares_period_end_cum_adj is not null and shares_period_end_cum_adj != 0 and file_date_cum_adj is not null
        then shares_outstanding * file_date_cum_adj / shares_period_end_cum_adj end as shares_outstanding_adjusted,
    case when shares_period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then dividend_per_share * shares_period_end_cum_adj / file_date_cum_adj end as dividend_per_share_adjusted,
    case when eps_ttm is not null and period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then eps_ttm * period_end_cum_adj / file_date_cum_adj end as eps_ttm_adjusted,
    case when dividend_per_share_ttm is not null and shares_period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then dividend_per_share_ttm * shares_period_end_cum_adj / file_date_cum_adj end as dividend_per_share_ttm_adjusted,
    case when bps is not null and bps != 0 and period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then close / (bps * period_end_cum_adj / file_date_cum_adj) end as pbr,
    case when eps_ttm is not null and eps_ttm != 0 and period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then close / (eps_ttm * period_end_cum_adj / file_date_cum_adj) end as per,
    case when shares_outstanding is not null and shares_period_end_cum_adj is not null and shares_period_end_cum_adj != 0 and file_date_cum_adj is not null
        then close * (shares_outstanding * file_date_cum_adj / shares_period_end_cum_adj) end as market_cap,
    case when sales_ttm is not null and sales_ttm != 0 and shares_outstanding is not null and shares_period_end_cum_adj is not null and shares_period_end_cum_adj != 0 and file_date_cum_adj is not null
        then (close * (shares_outstanding * file_date_cum_adj / shares_period_end_cum_adj)) / sales_ttm end as psr,
    case when eps_ttm is not null and close is not null and close != 0 and period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then (eps_ttm * period_end_cum_adj / file_date_cum_adj) / close end as earnings_yield,
    case when dividend_per_share_ttm is not null and close is not null and close != 0 and shares_period_end_cum_adj is not null and file_date_cum_adj is not null and file_date_cum_adj != 0
        then (dividend_per_share_ttm * shares_period_end_cum_adj / file_date_cum_adj) / close end as dividend_yield
from daily
