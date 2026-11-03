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
    select
        unnest(data) as rec,
        filename
    from read_json_auto('{{ env_var("LAKE_ROOT", "/lake") }}/jquants-dl/raw/*/*/*/listed_master.json', filename=true)
),

flat as (
    select
        filename,
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
)

-- (code, file_date) ごとに最新の取得ファイル（パスの年月日が最大）の行だけを残す。
-- J-Quants側のDateは「直近営業日時点」を指し取得日ではないため、週末・祝日を挟むと
-- 同一Dateが複数の生ファイルにまたがる。多くは完全重複だが、後の取得で値が訂正される
-- ことがある（2026-11-02実例: 78780・652A0の信用区分Mrgnが取得ファイル間で異なる）。
-- 完全重複の除去（distinct）だけでは一意にならないため、最新取得分を採用する。
select * exclude (filename, rn)
from (
    select
        *,
        row_number() over (
            partition by code, file_date
            order by filename desc
        ) as rn
    from flat
)
where rn = 1
