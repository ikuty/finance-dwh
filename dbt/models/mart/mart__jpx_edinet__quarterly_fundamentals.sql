-- EDINET由来(有報/半期報告書、period_type: annual/half)とir-disclosure-dl由来
-- (決算短信、period_type: q1/q2_half/q3/annual)の実績値を1本にまとめたmart
-- （毎回フルrebuild、Parquet）。
--
-- 背景（2026-10-03、ユーザー判断）: 2024年4月の制度改正で四半期報告書のEDINET
-- 提出が廃止され、EDINETには有報(annual)・半期報告書(half)しか無い
-- (mart__jpx_edinet__disclosed_fundamentals参照)。ir-disclosure-dlの決算短信は
-- Q1/Q3を含む全期を対象とするため、EDINETに恒常的に存在しないQ1/Q3の空白と、
-- 実測したEDINET提出までのラグ（有報は期末後中央値85日・p90=89日、半期報告書は
-- 中央値44日。決算短信は期末後約25〜30日で開示）による一時的な空白の両方を
-- 埋められる。
--
-- **行は削除・統合しない(UNION ALLのみ)**: 当初、企業(edinet_code)単位で
-- forecast_epsを「その企業の最新の決算短信予想」として全行に付与する設計を
-- 試みたが、実データで2021年・2023年の古いEDINET実績行に2026年開示の予想
-- （forecast_period_end=2027年）が付いてしまう先読みバイアスを実機で確認し撤回した
-- （mart__edinet__financial_indicators.submit_date_timeのコメントで既に明記
-- されている「先読みバイアス回避」の原則に反するため）。forecast_eps/
-- forecast_period_endは、それを実際に記載したir_disclosure由来の行自身にのみ
-- 残し、他の行・他の期間へブロードキャストしない。
--
-- 同じ(edinet_code, period_end)にEDINET・ir_disclosure両方の行が存在する場合も
-- 両方とも残す(forecast情報を持つir_disclosure側の行を失わないため)。「この期間の
-- 確定値として何を使うべきか」を一意に選びたい場合は、is_preferred_actuals列
-- （EDINET優先、無ければ開示が最も新しいもの）で絞ること。
--
-- disclosed_at（開示日時）は株価等と組み合わせる際の先読みバイアス回避のため
-- period_endではなくこちらを基準にする（mart__edinet__financial_indicators.
-- submit_date_timeと同じ方針）。ir-disclosure側はTDnet検知日(file_date)を
-- 開示日時の近似として用いる。
--
-- 金額列(sales)の単位は常に円（2026-10-04修正）: ir-disclosure側のcleansed層
-- （決算短信PDFの「百万円未満切捨て」表記のまま数値化、百万円単位）をここで
-- 1,000,000倍して円に揃える。EDINET側は元々円のため変換不要。
--
-- shares_period_end_cum_adj（2026-10-07追加）: disclosed_fundamentals由来の
-- 発行済株式数調整係数をそのまま素通し（ir_disclosure由来はNULL、元々
-- shares_outstanding自体を持たないため）。J-Quants等、第三者データとの検証で
-- 「決算期末がちょうど株式分割の権利確定日〜効力発生日の間に入る開示」を
-- 識別するために必要（詳細はdisclosed_fundamentals側のコメント参照）。

{{ config(
    materialized='external',
    location=env_var('MART_ROOT', '/data/mart') ~ '/jpx_edinet_quarterly_fundamentals.parquet',
    format='parquet'
) }}

with edinet_actuals as (
    select
        edinet_code,
        sec_code,
        jpx_code,
        period_end,
        period_type,
        cast(submit_date_time as timestamp)        as disclosed_at,
        eps,
        bps,
        sales,
        shares_outstanding,
        dividend_per_share,
        shares_period_end_cum_adj,
        cast(null as decimal(18, 4))               as forecast_eps,
        cast(null as date)                         as forecast_period_end,
        'edinet'                                   as source
    from {{ ref('mart__jpx_edinet__disclosed_fundamentals') }}
),

ir_disclosure_actuals as (
    select
        edinet_code,
        sec_code,
        left(sec_code, 4)                          as jpx_code,
        period_end,
        period_type,
        cast(file_date as timestamp)               as disclosed_at,
        eps_actual                                 as eps,
        cast(null as decimal(38, 4))               as bps,
        -- 決算短信の金額欄は「百万円未満切捨て」表記のため、cleansed層は印字
        -- された数値のまま保持している(円への換算はしていない、PDFの生表示を
        -- そのまま保持するcleansed層の責務方針)。EDINET側(sales等)は円そのまま
        -- のため、このmartで比較可能にするにはここで百万円→円に換算する必要が
        -- ある(2026-10-04発見・修正: 積水ハウスの実データで、同一期間・同一EPS
        -- にも関わらずsalesがEDINET側1,965,644,000,000・ir_disclosure側
        -- 1,965,644と100万倍食い違っていた)。
        sales * 1000000                            as sales,
        cast(null as decimal(38, 4))               as shares_outstanding,
        cast(null as decimal(38, 4))               as dividend_per_share,
        cast(null as double)                       as shares_period_end_cum_adj,
        forecast_eps,
        forecast_period_end,
        'ir_disclosure'                            as source
    from {{ ref('cleansed__ir_disclosure__kessan_tanshin') }}
    where extraction_status = 'ok'
      and edinet_code is not null
      and period_end is not null
),

combined as (
    select * from edinet_actuals
    union all
    select * from ir_disclosure_actuals
)

select
    *,
    row_number() over (
        partition by edinet_code, period_end
        order by case when source = 'edinet' then 0 else 1 end, disclosed_at desc
    ) = 1 as is_preferred_actuals
from combined
