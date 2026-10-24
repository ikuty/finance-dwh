-- J-Quants API (GET /v2/equities/master、jquants-dlが取得) の生レスポンスの
-- 型付け版（毎回フル rebuild、Parquet）。cleansed__jquants__daily_quotesと同じ理由
-- でlanding層を経由せず、レイクの生JSONファイルを直接読む。
--
-- 東証日報PDF由来のcleansed__jpx__stq_pricesとは、市場区分・33業種コードの
-- 分類方針が一部異なることを実データで確認済み（ファンド型銘柄の扱い等、
-- 詳細は[[jquants-price-vs-jpx-report-diff]]メモリ参照）。取捨選択・正規化は
-- mart層以降の責務とする。codeはここでは正規化せず、jQuants側の生の値（5桁）
-- のまま保持する。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/jquants_listed_master.parquet',
    format='parquet'
) }}

with raw as (
    select unnest(data) as rec
    from read_json_auto('{{ env_var("LAKE_ROOT", "/lake") }}/jquants-dl/raw/*/*/*/listed_master.json')
)

select
    try_cast(rec.Date as date) as file_date,
    rec.Code::varchar         as code,
    rec.CoName::varchar       as company_name_ja,
    rec.CoNameEn::varchar     as company_name_en,
    rec.S17::varchar          as industry_code_17,
    rec.S17Nm::varchar        as industry_name_17,
    rec.S33::varchar          as industry_code_33,
    rec.S33Nm::varchar        as industry_name_33,
    rec.ScaleCat::varchar     as scale_category,
    rec.Mkt::varchar          as market_code,
    rec.MktNm::varchar        as market_name,
    rec.Mrgn::varchar         as margin_code,
    rec.MrgnNm::varchar       as margin_name,
    rec.ProdCat::varchar      as product_category
from raw
