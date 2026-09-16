-- 三菱UFJ eスマート証券(kabu.com)の株式分割ページの型付け版。
-- landing.mufg_stock_splits(Parquet、週次スナップショットごとにfile_date=YYYY-MM-DD
-- で分割)を型付けするだけのexternal materialization。各週、その時点での全履歴
-- (将来の予定含む)を再掲載する形式のため、landingは全週分を蓄積する一方、
-- cleansedは最新file_date(直近の取得)のみを読む(過去の週のスナップショットは
-- 新しい週に完全に包含されるため、cleansedで重複させる意味が無い)。
--
-- 割当日・効力発生日が確定していない銘柄は"－"(全角ダッシュ)で埋まっていることを
-- 実機確認済み(2026-09-16)。try_castにより自然にNULLになる。
-- 割当比率は"1：2"のような全角コロン区切りの文字列(小数を含む例あり: "1：1.05")。
-- ratio_before/ratio_afterに分割してdecimalへ型付けする(生の文字列もratio_raw
-- として残す)。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/mufg_stock_splits.parquet',
    format='parquet'
) }}

select
    cast(file_date as date)                                   as file_date,
    try_cast(allotment_date as date)                          as allotment_date,
    code,
    name,
    ratio                                                     as ratio_raw,
    try_cast(split_part(ratio, '：', 1) as decimal(18, 4))     as ratio_before,
    try_cast(split_part(ratio, '：', 2) as decimal(18, 4))     as ratio_after,
    try_cast(last_cum_rights_date as date)                     as last_cum_rights_date,
    try_cast(effective_date as date)                           as effective_date,
    try_cast(sellable_date as date)                            as sellable_date,
    try_cast(_loaded_at as timestamptz)                        as _loaded_at
from {{ source('landing', 'mufg_stock_splits') }}
where file_date = (select max(file_date) from {{ source('landing', 'mufg_stock_splits') }})
