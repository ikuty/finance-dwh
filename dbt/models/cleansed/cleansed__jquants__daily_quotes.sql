-- J-Quants API (GET /v2/equities/bars/daily、jquants-dlが取得) の生レスポンスの
-- 型付け版（毎回フル rebuild、Parquet）。cleansed__jquants__fin_summaryと同じ理由で
-- landing層を経由せず、レイクの生JSONファイルをDuckDBのread_json_autoで直接読む
-- （JSONはDuckDBのネイティブリーダーで問題なく読めるため、Python製landingパーサー
-- を新設する理由が無い。edinet-dlのCSVとは事情が異なる）。日次のdbt buildに含める
-- （fin_summaryと異なり不定期手動実行ではなく、jquants-dlが毎日取得するため）。
--
-- 東証日報PDF由来のcleansed__jpx__stq_pricesとは完全に独立したテーブルとして持つ
-- （2026-10-24決定）。銘柄コードの桁数が異なる（jQuantsは5桁末尾0、日報は4桁）等の
-- 差異があり、取捨選択・正規化はmart層以降の責務とする
-- （詳細は[[jquants-price-vs-jpx-report-diff]]メモリ参照）。codeはここでは
-- 正規化せず、jQuants側の生の値（5桁）のまま保持する。
--
-- UL/LL（値幅制限フラグ）・ExRT（権利関連の値と思われる）は意味が確定していない
-- ため、解釈せず生の文字列のまま保持する（cleansed__edinet__factsのvalue_text/
-- value_numと同じ「解釈しすぎない」方針）。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/jquants_daily_quotes.parquet',
    format='parquet'
) }}

with raw as (
    select unnest(data) as rec
    from read_json_auto('{{ env_var("LAKE_ROOT", "/lake") }}/jquants-dl/raw/*/*/*/daily_quotes.json')
)

select
    try_cast(rec.Date as date)                 as file_date,
    rec.Code::varchar                          as code,
    try_cast(rec.O as decimal(18, 4))          as open_price,
    try_cast(rec.H as decimal(18, 4))          as high_price,
    try_cast(rec.L as decimal(18, 4))          as low_price,
    try_cast(rec.C as decimal(18, 4))          as close_price,
    rec.UL::varchar                            as upper_limit_flag_raw,
    rec.LL::varchar                            as lower_limit_flag_raw,
    try_cast(rec.Vo as decimal(20, 4))         as volume,
    try_cast(rec.Va as decimal(20, 4))         as trading_value,
    try_cast(rec.AdjFactor as decimal(18, 8))  as adj_factor,
    try_cast(rec.AdjO as decimal(18, 4))       as adj_open_price,
    try_cast(rec.AdjH as decimal(18, 4))       as adj_high_price,
    try_cast(rec.AdjL as decimal(18, 4))       as adj_low_price,
    try_cast(rec.AdjC as decimal(18, 4))       as adj_close_price,
    try_cast(rec.AdjVo as decimal(20, 4))      as adj_volume,
    try_cast(rec.MktCap as decimal(20, 4))     as market_cap,
    rec.ExRT::varchar                          as ex_rights_flag_raw
from raw
