-- 決算短信（四半期・半期・通期）の配当表から、1件ごとに「直近12か月の実績配当」と
-- 「当期（通期）の予想配当」を算出する（毎回フル rebuild、Parquet。2026-11-12新設、#108）。
--
-- 背景: EDINETの半期報告書には1株当たり配当額が載らず、半期開示が最新の期間は
-- mart__jpx_edinet__disclosed_fundamentalsのdividend_per_share_ttmがNULLになり、配当利回りが
-- 全銘柄で空になっていた。決算短信の「配当の状況」には、前期実績・当期実績（経過分のみ）・
-- 予想の各行が（Q1末・Q2末・Q3末・期末・合計）の5スロットで載るため、どの決算短信からも
-- 直近12か月ぶんの配当が組み立てられる。抽出と型付けはir_disclosure_kessan_facts.py
-- (parse_dividend_table)とcleansed__ir_disclosure__kessan_tanshinが担う。
--
-- 実績ベースの直近12か月配当（dividend_ttm）:
--   Q1       : 前期Q2 + 前期Q3 + 前期期末 + 当期Q1
--   中間     : 前期Q3 + 前期期末 + 当期Q1 + 当期Q2
--   Q3       : 前期期末 + 当期Q1 + 当期Q2 + 当期Q3
--   通期     : 当期の合計
--   スロットの「－」は抽出段階で0（無配）になっている。必要なスロットが1つでもNULL
--   （空欄・解釈できない行）なら算出しない。
--
-- 予想ベースの当期年間配当（dividend_forecast_annual）:
--   予想行の合計。四半期・中間の決算短信は、予想行に経過済みのスロットが載らないため、
--   経過済み(当期実績の行)と未経過(予想の行)を合わせた4スロットの合計が「合計」と
--   一致することを確認する。対象期間はforecast_period_end(cleansed参照)で、四半期は
--   今期通期、通期決算は次期通期。
--
--   会社が年間の合計だけを予想し、内訳（Q2末・期末）を「－」とする場合（任天堂等）は、
--   スロットの合計が0・合計が正のとき合計列をそのまま採る（status='ok_total_only'）。
--   合計が「－」（未定、抽出段階で0）でスロットに値がある行は、未定との区別がつかない
--   ため値を出さない（dividend_inconsistent）。
--
-- 整合性の確認（実データ、2026-11-12）: 前期行はQ1〜期末の合計が「合計」列と一致する
-- 行が98.1%。一致しない行は空欄が詰められてスロットがずれている等のため、値を出さず
-- 理由をestimate_statusに残す（dividend_inconsistent）。通期の当期実績・予想行と、
-- 四半期の予想（経過済みスロットと合算）も同じ確認を行う。
--
-- 既知の限界:
--   - 「－」（無配）と「未定」を区別できない（抽出段階で両方0）。予想が未定の会社は
--     予想配当0と出る可能性がある。
--   - 記念配当・特別配当はそのまま直近12か月に入る（EDINETベースのTTMと同じ挙動）。
--   - 分割・併合を期中にまたぐ会社は、スロット間で株式数の基準が異なる可能性がある
--     （注記で「分割前の実際の配当金の額を記載」とする会社と、遡及修正する会社が
--     混在し、機械的な判定はできない）。
--
-- 株式数の基準（日次マートが取引日の基準へ換算する。効力発生日基準の累積調整係数）:
--   - 実績: shares_period_end_cum_adj。決算短信の期末日（period_end）時点。実績は「実際に
--     支払った額」で載る（mart__jpx_edinet__disclosed_fundamentalsのdividend_per_shareと同じ）。
--   - 予想: shares_disclosed_cum_adj。開示日（disclosed_at）時点。予想は開示時点の株式数で
--     載るため、期末後・開示前に分割があると期末日基準では二重に調整される
--     （京王電鉄9008の2026年3月期は、期末後の分割後の水準で予想が載る）。
--
-- 同じ(edinet_code, period_end)の決算短信が複数ある場合は、連結を優先し、開示日が新しい
-- ものを採用する（訂正等、bps_estimatesと同じ）。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/ir_disclosure_dividend_ttm.parquet',
    format='parquet'
) }}

with tanshin as (
    select
        edinet_code,
        left(sec_code, 4)  as jpx_code,
        docid,
        period_type,
        period_end,
        forecast_period_end,
        file_date          as disclosed_at,
        consolidation,
        dividend_prior_q1, dividend_prior_q2, dividend_prior_q3, dividend_prior_q4, dividend_prior_total,
        dividend_cur_q1, dividend_cur_q2, dividend_cur_q3, dividend_cur_q4, dividend_cur_total,
        dividend_fcst_q1, dividend_fcst_q2, dividend_fcst_q3, dividend_fcst_q4, dividend_fcst_total,
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

with_period_factor as (
    select
        t.*,
        p.shares_cum_adjustment_factor as shares_period_end_cum_adj
    from (select * from tanshin where rn = 1) t
    asof left join {{ ref('intermediate__jpx__daily_prices_adjusted') }} p
        on p.code = t.jpx_code
        and p.file_date <= t.period_end
),

with_factor as (
    select
        w.*,
        p.shares_cum_adjustment_factor as shares_disclosed_cum_adj
    from with_period_factor w
    asof left join {{ ref('intermediate__jpx__daily_prices_adjusted') }} p
        on p.code = w.jpx_code
        and p.file_date <= w.disclosed_at
),

-- 予想の4スロット。四半期・中間は経過済みスロットが当期実績の行にあるため、予想の行を
-- 優先しつつ当期実績で補う。通期の予想行は5スロットとも予想の行から取る。
slots as (
    select
        *,
        case when period_type = 'annual' then dividend_fcst_q1 else coalesce(dividend_fcst_q1, dividend_cur_q1) end as f_q1,
        case when period_type = 'annual' then dividend_fcst_q2 else coalesce(dividend_fcst_q2, dividend_cur_q2) end as f_q2,
        case when period_type = 'annual' then dividend_fcst_q3 else coalesce(dividend_fcst_q3, dividend_cur_q3) end as f_q3,
        case when period_type = 'annual' then dividend_fcst_q4 else coalesce(dividend_fcst_q4, dividend_cur_q4) end as f_q4
    from with_factor
),

calc as (
    select
        *,
        -- 実績ベース: 必要なスロットがそろっているか、整合性（前期行/通期の当期行の合計）が取れているか
        case period_type
            when 'annual' then
                dividend_cur_q1 is not null and dividend_cur_q2 is not null
                and dividend_cur_q3 is not null and dividend_cur_q4 is not null and dividend_cur_total is not null
            when 'q1' then
                dividend_prior_q1 is not null and dividend_prior_q2 is not null and dividend_prior_q3 is not null
                and dividend_prior_q4 is not null and dividend_prior_total is not null
                and dividend_cur_q1 is not null
            when 'q2_half' then
                dividend_prior_q1 is not null and dividend_prior_q2 is not null and dividend_prior_q3 is not null
                and dividend_prior_q4 is not null and dividend_prior_total is not null
                and dividend_cur_q1 is not null and dividend_cur_q2 is not null
            when 'q3' then
                dividend_prior_q1 is not null and dividend_prior_q2 is not null and dividend_prior_q3 is not null
                and dividend_prior_q4 is not null and dividend_prior_total is not null
                and dividend_cur_q1 is not null and dividend_cur_q2 is not null and dividend_cur_q3 is not null
            else false
        end as ttm_slots_present,
        case period_type
            when 'annual' then
                abs(dividend_cur_q1 + dividend_cur_q2 + dividend_cur_q3 + dividend_cur_q4 - dividend_cur_total) < 0.011
            else
                abs(dividend_prior_q1 + dividend_prior_q2 + dividend_prior_q3 + dividend_prior_q4 - dividend_prior_total) < 0.011
        end as ttm_consistent,
        case period_type
            when 'annual' then dividend_cur_total
            when 'q1' then dividend_prior_q2 + dividend_prior_q3 + dividend_prior_q4 + dividend_cur_q1
            when 'q2_half' then dividend_prior_q3 + dividend_prior_q4 + dividend_cur_q1 + dividend_cur_q2
            when 'q3' then dividend_prior_q4 + dividend_cur_q1 + dividend_cur_q2 + dividend_cur_q3
        end as ttm_value,
        -- 予想ベース
        f_q1 is not null and f_q2 is not null and f_q3 is not null and f_q4 is not null
            and dividend_fcst_total is not null as fcst_slots_present,
        abs(f_q1 + f_q2 + f_q3 + f_q4 - dividend_fcst_total) < 0.011 as fcst_consistent,
        f_q1 + f_q2 + f_q3 + f_q4 = 0 and dividend_fcst_total > 0 as fcst_total_only,
        coalesce(dividend_fcst_q1, dividend_fcst_q2, dividend_fcst_q3, dividend_fcst_q4, dividend_fcst_total) is null as fcst_absent,
        coalesce(
            dividend_prior_q1, dividend_prior_q2, dividend_prior_q3, dividend_prior_q4, dividend_prior_total,
            dividend_cur_q1, dividend_cur_q2, dividend_cur_q3, dividend_cur_q4, dividend_cur_total,
            dividend_fcst_q1, dividend_fcst_q2, dividend_fcst_q3, dividend_fcst_q4, dividend_fcst_total
        ) is null as table_absent,
        shares_period_end_cum_adj is null or shares_period_end_cum_adj = 0 as factor_missing,
        shares_disclosed_cum_adj is null or shares_disclosed_cum_adj = 0 as disclosed_factor_missing
    from slots
)

select
    edinet_code,
    jpx_code,
    docid,
    period_type,
    period_end,
    forecast_period_end,
    disclosed_at,
    consolidation,
    dividend_prior_total,
    dividend_cur_total,
    dividend_fcst_total,
    shares_period_end_cum_adj,
    shares_disclosed_cum_adj,
    case
        when table_absent or factor_missing or not ttm_slots_present or not ttm_consistent then null
        else ttm_value
    end as dividend_ttm,
    case
        when table_absent then 'no_dividend_table'
        when not ttm_slots_present then 'slots_missing'
        when not ttm_consistent then 'dividend_inconsistent'
        when factor_missing then 'no_adjustment_factor'
        else 'ok'
    end as dividend_ttm_status,
    case
        when table_absent or fcst_absent or disclosed_factor_missing or not fcst_slots_present
            or not (fcst_consistent or fcst_total_only) then null
        else dividend_fcst_total
    end as dividend_forecast_annual,
    case
        when table_absent or fcst_absent then 'no_forecast'
        when not fcst_slots_present then 'slots_missing'
        when not (fcst_consistent or fcst_total_only) then 'dividend_inconsistent'
        when disclosed_factor_missing then 'no_adjustment_factor'
        when fcst_consistent then 'ok'
        else 'ok_total_only'
    end as dividend_forecast_status
from calc
