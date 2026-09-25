-- JPX形式B(株式相場表・月次簡易OHLC)の型付け版。
--
-- モデル名について(2026-09-25、cleansed__jpx__monthly_ohlcから改名):
--   「月次」はソースPDFの配信単位(1ヶ月1ファイル)を指すだけで、データの粒度は
--   銘柄×営業日(1行=1日)であり月次集計ではない。旧名がこの粒度を「月次」と
--   誤解させる名前だったため、実態に合わせてdaily_ohlcに改名した(landing層の
--   テーブル名・Prefectフロー名(load_jpx_monthly_ohlc等)は「月次配信PDFの取り込み」
--   という意味で妥当なため変更していない。cleansed層のみの改名)。
--
-- landing.jpx_monthly_ohlc_facts(Parquet、月単位でPrefectのload_jpx_monthly_ohlc_facts
-- タスクが日付ごとのパーティションへ分けて書く)を型付けするだけの external
-- materialization。形式C・EDINETと同じ理由で常に全量rebuildする（対象規模が
-- 小さく、incremental化の理由が無い）。詳細はdocs/raw_landing_design.md参照。
--
-- 形式Cと異なり、この形式のOHLC値にカンマ区切りは含まれない（実機データで確認、
-- 高額なREIT銘柄でも例: "376500"）ため、replace(',','')は不要。VWAP・売買高・
-- 売買代金・最終気配・前日比は形式Bに存在しないため列自体が無い。
-- 銘柄名称欄は「名称＋株式種別」を機械的に分離できないため(landing側で判断済み、
-- jpx_monthly_ohlc_facts.py参照)、name_jaは結合済みの文字列のまま。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/jpx_daily_ohlc.parquet',
    format='parquet'
) }}

select
    cast(file_date as date)                as file_date,
    code,
    name_ja,
    try_cast(am_open as decimal(18, 4))    as am_open,
    try_cast(am_high as decimal(18, 4))    as am_high,
    try_cast(am_low as decimal(18, 4))     as am_low,
    try_cast(am_close as decimal(18, 4))   as am_close,
    try_cast(pm_open as decimal(18, 4))    as pm_open,
    try_cast(pm_high as decimal(18, 4))    as pm_high,
    try_cast(pm_low as decimal(18, 4))     as pm_low,
    try_cast(pm_close as decimal(18, 4))   as pm_close,
    try_cast(_loaded_at as timestamptz)    as _loaded_at
from {{ source('landing', 'jpx_monthly_ohlc_facts') }}
