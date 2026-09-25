-- 銘柄(edinet_code)×期(fiscal_year, period_type)ごとの株価評価指標（毎回フル
-- rebuild、Parquet）。mart__edinet__financial_indicators（ファンダメンタルズ）と
-- intermediate__jpx__daily_prices（株価）を結合する、具体的な利用目的（PBR等の
-- 算出）を持つ薄いmart。grainはmart__edinet__financial_indicatorsと同じ。
--
-- 銘柄コードの結合(2026-09-25判明、重要):
--   EDINETのsec_codeは5桁（末尾1桁は証券の種類を表す予備コード、0=普通株式、
--   0以外=優先株等）、JPXのcodeは4桁（daily_prices構築時に普通株式のみへ正規化
--   済み）。sec_codeの末尾1桁を除去して4桁化し結合する。sec_code='00000'
--   （非上場・コード無し）やright(sec_code,1)!='0'（優先株等）は対象外とし、
--   LEFT JOINで自然にNULLになる設計（明示的な除外フィルタは入れない）。
--
-- 株価の参照ルール:
--   - 決算期末日ベース: file_date <= period_endの中で最大のfile_date（休業日なら
--     直前営業日）の終値。実データ検証(2026-09-25、SMFG5期分)で、この終値が
--     企業自己申告のper(株価収益率)×epsとほぼ完全に一致することを確認済み
--     （assert_mart__jpx_edinet__valuation_indicators_per_matches_disclosed参照）。
--   - 直近営業日ベース（列名に_latestサフィックス）: 銘柄ごとの最新file_dateの
--     終値。定義上その日より後の分割・併合は無いため、常にraw（無調整）終値で
--     問題ない。
--   - いずれもintermediate__jpx__daily_prices（raw、無調整）を参照する。決算期末日
--     ベースの指標は、各期のEPS/BPS自体がその期のオリジナルな株式数ベースで開示
--     されているため、split調整後(daily_prices_adjusted)ではなくrawを使うのが
--     整合的（PER検証で確認済み）。
--   - 終値は前場・後場のうちpm_close（後場引け、無ければam_closeにフォールバック）
--     を「その日の終値」として採用する（日本株の慣行上、後場引けが日々の終値）。

{{ config(
    materialized='external',
    location=env_var('MART_ROOT', '/data/mart') ~ '/jpx_edinet_valuation_indicators.parquet',
    format='parquet'
) }}

with edinet as (
    select
        doc_id,
        edinet_code,
        sec_code,
        filer_name,
        fiscal_year,
        period_type,
        period_start,
        period_end,
        bps,
        eps,
        sales,
        shares_outstanding,
        per as per_disclosed,
        left(sec_code, 4) as jpx_code
    from {{ ref('mart__edinet__financial_indicators') }}
),

prices as (
    select
        code,
        file_date,
        coalesce(pm_close, am_close) as close
    from {{ ref('intermediate__jpx__daily_prices') }}
),

latest_price as (
    select code, file_date as latest_close_date, close as latest_close
    from prices
    qualify row_number() over (partition by code order by file_date desc) = 1
),

period_end_price as (
    select
        e.doc_id,
        p.file_date as period_end_close_date,
        p.close as period_end_close
    from edinet e
    asof left join prices p
        on e.jpx_code = p.code
        and p.file_date <= e.period_end
)

select
    e.doc_id,
    e.edinet_code,
    e.sec_code,
    e.filer_name,
    e.fiscal_year,
    e.period_type,
    e.period_start,
    e.period_end,
    pe.period_end_close_date,
    pe.period_end_close,
    lp.latest_close_date,
    lp.latest_close,
    e.per_disclosed,
    case when e.bps is not null and e.bps != 0
        then pe.period_end_close / e.bps end as pbr,
    case when e.bps is not null and e.bps != 0
        then lp.latest_close / e.bps end as pbr_latest,
    case when e.eps is not null and e.eps != 0
        then pe.period_end_close / e.eps end as per_computed,
    case when e.eps is not null and e.eps != 0
        then lp.latest_close / e.eps end as per_computed_latest,
    pe.period_end_close * e.shares_outstanding as market_cap,
    lp.latest_close * e.shares_outstanding as market_cap_latest,
    case when e.sales is not null and e.sales != 0
        then (pe.period_end_close * e.shares_outstanding) / e.sales end as psr,
    case when e.sales is not null and e.sales != 0
        then (lp.latest_close * e.shares_outstanding) / e.sales end as psr_latest,
    case when pe.period_end_close is not null and pe.period_end_close != 0
        then e.eps / pe.period_end_close end as earnings_yield
from edinet e
left join period_end_price pe on pe.doc_id = e.doc_id
left join latest_price lp on lp.code = e.jpx_code
