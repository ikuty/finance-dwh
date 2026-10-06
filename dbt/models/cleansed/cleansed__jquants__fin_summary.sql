-- J-Quants API (GET /v2/fins/summary) の生レスポンスの型付け版。
--
-- 当DWHが独自ロジックで算出した財務指標（mart__edinet__financial_indicators等）を
-- 第三者データで検証する目的専用のモデル（2026-10-06、ユーザー判断）。日次の
-- Prefectフローには含めない不定期手動実行のため、他のcleansedモデルと異なり
-- landing層を経由せず、レイクの生JSONファイルをDuckDBのread_json_autoで直接読む
-- （Prefectタスクモジュールを新規に作ることの方が、この用途には過剰と判断した）。
--
-- tags=['jquants_validation']: 日次のdbt build（transform/flows/daily_transform.py）
-- から常に除外される（--exclude tag:jquants_validation）。ソースの生JSONが検証実行
-- 時にしか存在しないため、日次ビルドに含めると毎日エラーになるため。検証時は
-- `dbt build --select tag:jquants_validation`のように明示的に指定して実行する。
--
-- 粒度: 1行=1開示（レスポンスのdata配列の1要素）。同一(code, cur_per_end)に複数行
-- (訂正報告・連結/非連結の重複等)が存在しうるが、ここでは選択ロジックを持たず生のまま
-- 残す（cleansed__edinet__factsと同じ「解釈しすぎない」方針）。どの行を正とするかは
-- 照合スクリプト側の責務とする。
--
-- 全レスポンスフィールドはJSON文字列型で返るため、未開示項目は空文字列になる
-- (try_castで自然にNULLになる)。DWH側の対応項目との検証に使う列のみを型付けし、
-- 残りのフィールド（予想配当・非連結値等、今回の検証対象外）は含めない。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/jquants_fin_summary.parquet',
    format='parquet',
    tags=['jquants_validation']
) }}

with raw as (
    select unnest(data) as rec
    from read_json_auto('{{ env_var("LAKE_ROOT", "/lake") }}/jquants-validate/fins_summary/*.json')
)

select
    rec.Code::varchar as code,
    try_cast(rec.DiscDate as date) as disc_date,
    rec.DiscTime::varchar as disc_time,
    rec.DocType::varchar as doc_type,
    rec.CurPerType::varchar as cur_per_type,
    try_cast(rec.CurPerSt as date) as cur_per_st,
    try_cast(rec.CurPerEn as date) as cur_per_en,
    try_cast(rec.CurFYSt as date) as cur_fy_st,
    try_cast(rec.CurFYEn as date) as cur_fy_en,
    try_cast(rec.Sales as decimal(20, 2)) as sales,
    try_cast(rec.OdP as decimal(20, 2)) as ordinary_profit,
    try_cast(rec.NP as decimal(20, 2)) as net_profit,
    try_cast(rec.EPS as decimal(20, 4)) as eps,
    try_cast(rec.DEPS as decimal(20, 4)) as diluted_eps,
    try_cast(rec.TA as decimal(20, 2)) as total_assets,
    try_cast(rec.Eq as decimal(20, 2)) as equity,
    try_cast(rec.EqAR as decimal(10, 6)) as equity_to_asset_ratio,
    try_cast(rec.BPS as decimal(20, 4)) as bps,
    try_cast(rec.CFO as decimal(20, 2)) as cash_flow_operating,
    try_cast(rec.CFI as decimal(20, 2)) as cash_flow_investing,
    try_cast(rec.CFF as decimal(20, 2)) as cash_flow_financing,
    try_cast(rec.CashEq as decimal(20, 2)) as cash_and_equivalents,
    try_cast(rec.DivAnn as decimal(20, 4)) as dividend_per_share_annual,
    try_cast(rec.ShOutFY as bigint) as shares_outstanding,
    try_cast(rec.ROE as decimal(10, 6)) as roe,
    try_cast(rec.FEPS as decimal(20, 4)) as forecast_eps
from raw
