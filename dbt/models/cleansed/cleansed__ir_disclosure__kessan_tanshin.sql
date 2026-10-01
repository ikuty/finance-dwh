-- ir-disclosure-dlが取得する決算短信PDFのサマリー情報の型付け版。
-- landing.ir_disclosure_kessan_facts(Parquet、日付ごとにload_ir_disclosure_kessan_
-- recent/backlogタスクが更新)を型付けするだけのexternal materialization
-- (jpx_stq_prices等と同じ理由で常に全量rebuildする)。
--
-- 数値の正規化(△による負数表記・カンマ・「－」による未開示表記)はlanding層
-- (ir_disclosure_kessan_facts.py)側で既に完了しているため、ここは単純な
-- try_castのみで良い(jpx_stqのカンマ除去とは異なる役割分担、理由は
-- ir_disclosure_kessan_facts.pyのモジュールdocstring参照)。
--
-- extraction_status != 'ok' の行(訂正・補足説明資料・期中レビュー完了notice・
-- 開示予定日に関するお知らせ)は財務数値列が全てNULLのまま残る。本体以外を
-- 除外せず残しているのは、後段で「このdocidは訂正として処理済み」等の存在
-- 確認に使えるようにするため(レイク層・landing層と同じ「解釈しすぎない」方針)。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/ir_disclosure_kessan_tanshin.parquet',
    format='parquet'
) }}

select
    cast(file_date as date)                                   as file_date,
    docid,
    extraction_status,
    period_type,
    consolidation,
    accounting_standard,
    fiscal_period_label,

    try_cast(sales as decimal(20, 4))                         as sales,
    try_cast(sales_prior as decimal(20, 4))                   as sales_prior,
    try_cast(sales_yoy_pct as decimal(10, 4))                 as sales_yoy_pct,
    try_cast(operating_income as decimal(20, 4))               as operating_income,
    try_cast(operating_income_prior as decimal(20, 4))         as operating_income_prior,
    try_cast(operating_income_yoy_pct as decimal(10, 4))       as operating_income_yoy_pct,
    try_cast(ordinary_income as decimal(20, 4))                 as ordinary_income,
    try_cast(ordinary_income_prior as decimal(20, 4))           as ordinary_income_prior,
    try_cast(ordinary_income_yoy_pct as decimal(10, 4))         as ordinary_income_yoy_pct,
    try_cast(income_before_tax as decimal(20, 4))                as income_before_tax,
    try_cast(income_before_tax_prior as decimal(20, 4))          as income_before_tax_prior,
    try_cast(income_before_tax_yoy_pct as decimal(10, 4))        as income_before_tax_yoy_pct,
    try_cast(net_income as decimal(20, 4))                      as net_income,
    try_cast(net_income_prior as decimal(20, 4))                as net_income_prior,
    try_cast(net_income_yoy_pct as decimal(10, 4))              as net_income_yoy_pct,

    try_cast(eps_actual as decimal(18, 4))                     as eps_actual,
    try_cast(eps_actual_prior as decimal(18, 4))               as eps_actual_prior,
    try_cast(eps_diluted_actual as decimal(18, 4))             as eps_diluted_actual,
    try_cast(eps_diluted_actual_prior as decimal(18, 4))       as eps_diluted_actual_prior,

    try_cast(total_assets as decimal(20, 4))                   as total_assets,
    try_cast(total_assets_prior as decimal(20, 4))             as total_assets_prior,
    try_cast(net_assets as decimal(20, 4))                     as net_assets,
    try_cast(net_assets_prior as decimal(20, 4))               as net_assets_prior,
    try_cast(equity_ratio as decimal(10, 4))                   as equity_ratio,
    try_cast(equity_ratio_prior as decimal(10, 4))             as equity_ratio_prior,

    try_cast(cf_operating as decimal(20, 4))                   as cf_operating,
    try_cast(cf_investing as decimal(20, 4))                   as cf_investing,
    try_cast(cf_financing as decimal(20, 4))                   as cf_financing,
    try_cast(cf_cash_end as decimal(20, 4))                    as cf_cash_end,

    try_cast(forecast_sales as decimal(20, 4))                  as forecast_sales,
    try_cast(forecast_sales_yoy_pct as decimal(10, 4))          as forecast_sales_yoy_pct,
    try_cast(forecast_operating_income as decimal(20, 4))        as forecast_operating_income,
    try_cast(forecast_operating_income_yoy_pct as decimal(10, 4)) as forecast_operating_income_yoy_pct,
    try_cast(forecast_ordinary_income as decimal(20, 4))          as forecast_ordinary_income,
    try_cast(forecast_ordinary_income_yoy_pct as decimal(10, 4))  as forecast_ordinary_income_yoy_pct,
    try_cast(forecast_income_before_tax as decimal(20, 4))         as forecast_income_before_tax,
    try_cast(forecast_income_before_tax_yoy_pct as decimal(10, 4)) as forecast_income_before_tax_yoy_pct,
    try_cast(forecast_net_income as decimal(20, 4))              as forecast_net_income,
    try_cast(forecast_net_income_yoy_pct as decimal(10, 4))      as forecast_net_income_yoy_pct,
    try_cast(forecast_eps as decimal(18, 4))                    as forecast_eps,

    try_cast(_loaded_at as timestamptz)                         as _loaded_at
from {{ source('landing', 'ir_disclosure_kessan_facts') }}
