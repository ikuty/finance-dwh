-- 決算短信(四半期・半期・通期)の純資産から、その期末時点の1株当たり純資産(BPS)を推定する
-- (毎回フル rebuild、Parquet。2026-11-05新設、PBRが空になる問題への対応)。
--
-- 背景: BPSは有価証券報告書(年次)にしか載らず(EDINETの四半期報告書は2024年に廃止、
-- 半期報告書にもほぼ無い)、直近の開示が四半期・半期だと日次のPBRがNULLになっていた
-- (全銘柄の約4割)。決算短信には当期末と前期末の純資産が載るため、前期末(年次)の
-- BPSに純資産の比を掛けて、最新期末のBPSを推定する:
--
--   bps_estimated = annual_bps × (net_assets ÷ net_assets_prior) [× 分割調整]
--
-- - 純資産の比は同じ決算短信の中の2値(当期末・前期末)で、純資産の定義(自己資本か
--   純資産合計か、非支配株主持分を含むか)が分子・分母で揃うため相殺される。株数は
--   不要(前期末と同じと仮定する)。
-- - 前期末のBPSは、EDINETの直近の年次(period_endが決算短信の期末より前)のもの。
-- - 株数の変化(自己株式の取得・消却・増資)は反映できない。そのため純資産が前期比
--   ±10%を超えて動いた行は推定せずNULL(estimate_status='ratio_out_of_range')にする。
--
-- 精度の検証(2026-11-05、Mac Miniの実データ): J-Quantsの四半期BPS(正解として照合のみに
-- 使用、推定には使っていない)229件との比較で、±10%内の209件は絶対誤差の中央値0.20%、
-- 5%以内95%、10%以内99%、最大12%。期間別の中央値: 第1四半期0.03%・中間0.18%・
-- 第3四半期0.81%。JT(2914)の2026年9月期第3四半期は推定2,321.4円・J-Quants 2,321.1円・
-- 株探の逆算約2,323円で一致。通期の決算短信(55件)での検証は中央値0.6%(1年分の株数変化が
-- 全て誤差になる厳しい条件)。
--
-- bps_estimatedは決算短信の期末(period_end)時点の株式数基準(period_end_cum_adj
-- は同日の累積調整係数)。mart__jpx_edinet__disclosed_fundamentalsのbps/period_end_cum_adj
-- と同じ扱いで、日次モデルが取引日の基準へ換算する。年次BPSの基準日と決算短信の期末の間に
-- 分割があれば、annual_cum_adj / period_end_cum_adj で調整済み。
--
-- 同じ(edinet_code, period_end)の決算短信が複数ある場合は、連結を優先し、開示日が新しい
-- ものを採用する(訂正等)。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/ir_disclosure_bps_estimates.parquet',
    format='parquet'
) }}

with tanshin as (
    select
        edinet_code,
        left(sec_code, 4)  as jpx_code,
        docid,
        period_type,
        period_end,
        file_date          as disclosed_at,
        consolidation,
        net_assets,
        net_assets_prior,
        row_number() over (
            partition by edinet_code, period_end
            order by case when consolidation = 'consolidated' then 0 else 1 end,
                     file_date desc, docid desc
        ) as rn
    from {{ ref('cleansed__ir_disclosure__kessan_tanshin') }}
    where extraction_status = 'ok'
      and edinet_code is not null
      and sec_code is not null
      and period_end is not null
),

annual as (
    select
        edinet_code,
        period_end          as annual_period_end,
        doc_id              as annual_doc_id,
        bps                 as annual_bps,
        period_end_cum_adj  as annual_cum_adj
    from {{ ref('mart__jpx_edinet__disclosed_fundamentals') }}
    where period_type = 'annual' and bps is not null and bps > 0
),

with_annual as (
    select
        t.*,
        a.annual_period_end,
        a.annual_doc_id,
        a.annual_bps,
        a.annual_cum_adj
    from (select * from tanshin where rn = 1) t
    asof left join annual a
        on a.edinet_code = t.edinet_code
        and t.period_end > a.annual_period_end
),

with_price as (
    select
        w.*,
        p.cum_adjustment_factor as period_end_cum_adj
    from with_annual w
    asof left join {{ ref('intermediate__jpx__daily_prices_adjusted') }} p
        on p.code = w.jpx_code
        and p.file_date <= w.period_end
)

select
    edinet_code,
    jpx_code,
    docid,
    period_type,
    period_end,
    disclosed_at,
    consolidation,
    net_assets,
    net_assets_prior,
    net_assets / nullif(net_assets_prior, 0) as net_assets_ratio,
    annual_doc_id,
    annual_period_end,
    annual_bps,
    annual_cum_adj,
    period_end_cum_adj,
    case
        when net_assets is null or net_assets_prior is null or net_assets <= 0 or net_assets_prior <= 0
            then null
        when annual_bps is null or datediff('day', annual_period_end, period_end) not between 80 and 400
            then null
        when abs(net_assets / net_assets_prior - 1) > 0.10
            then null
        when annual_cum_adj is null or period_end_cum_adj is null or period_end_cum_adj = 0
            then null
        else annual_bps * (annual_cum_adj / period_end_cum_adj) * (net_assets / net_assets_prior)
    end as bps_estimated,
    case
        when net_assets is null or net_assets_prior is null or net_assets <= 0 or net_assets_prior <= 0
            then 'net_assets_missing'
        when annual_bps is null or datediff('day', annual_period_end, period_end) not between 80 and 400
            then 'no_prior_annual'
        when abs(net_assets / net_assets_prior - 1) > 0.10
            then 'ratio_out_of_range'
        when annual_cum_adj is null or period_end_cum_adj is null or period_end_cum_adj = 0
            then 'no_adjustment_factor'
        else 'ok'
    end as estimate_status
from with_price
