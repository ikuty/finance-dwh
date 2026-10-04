-- cleansed__mufg__stock_splits/stock_consolidationsを統合した、銘柄(code)×権利落ち日
-- 単位のコーポレートアクション・イベント。J-Quantsの調整係数(AdjFactor)算出方式に
-- 倣う（https://jpx-jquants.com/ja/spec/eq-bars-daily/adj）。
--
-- 権利落ち日の求め方(2026-09-25、実機のkabu.comページのレンダリング結果で検証済み):
--   権利落ち日＝権利付最終日(last_cum_rights_date)の翌営業日。祝日カレンダーを
--   別途持たず、intermediate__jpx__daily_pricesの実際の取引日一覧(全銘柄共通の
--   市場カレンダー)から「last_cum_rights_dateより後の最小file_date」を検索して
--   求める。実例(8766 東京海上、割当日2026/09/30・権利付最終日2026/09/28)で、
--   この翌営業日(2026/09/29)がkabu.comの「売却可能予定日」列と完全一致することを
--   確認済み（独立した2つの手がかりが同じ結論を指す）。
--
-- 調整係数(adj_factor)の算出: ratio_before/ratio_afterで分割・併合とも統一的に
-- 計算できる（分割"1：2"→0.5、併合"10株→1株"→10、いずれもJ-Quantsの定義と整合）。
--
-- 既知の制約（2026-09-25、ユーザーに提示済み）:
--   - kabu.com（一証券会社のページ）が出典であり、取引所公式データではないため
--     網羅性を検証する手段が無い。
--   - ライツイシュー（新株予約権無償割当）による調整は対象外（分割・併合の
--     データしか無いため）。
--   - 銘柄コード変更（商号変更等、cleansed__mufg__company_name_changes）との
--     連携は今回のスコープ外。
--
-- effective_date（株式数の効力発生日）の追加(2026-10-04発見・修正、重要):
--   ex_rights_date（権利落ち日、株価が調整後水準になる最初の取引日）と
--   effective_date（分割・併合の効力発生日、株式数が実際に変わる日）は別物で、
--   通常1〜3日のズレがある（例: NTT 2023年分割は権利落ち日2023-06-29・
--   効力発生日2023-07-01）。株価の調整にはex_rights_dateが正しいが、発行済
--   株式数の調整には本来effective_dateを使うべきところ、両方にex_rights_date
--   由来の係数を流用していたため、決算期末日(period_end)がちょうどこの隙間
--   （ex_rights_date ≦ period_end ＜ effective_date）に入る開示で発行済株式数の
--   調整が漏れる不具合があった（実データで確認: NTT 2023年6月期・KDDI 2025年
--   3月期など、全期間で781件の開示が該当）。多くの企業が決算期末の翌月1日を
--   分割の効力発生日に設定するため、稀なケースではなく構造的に頻発する。
--   effective_dateをここで持たせ、intermediate__jpx__daily_prices_adjustedで
--   株価用(ex_rights_date基準)とは別に株式数用(effective_date基準)の累積調整
--   係数を算出する。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/mufg_corporate_actions.parquet',
    format='parquet'
) }}

with splits as (
    select
        code,
        last_cum_rights_date,
        effective_date,
        ratio_before / ratio_after as adj_factor,
        'stock_split' as action_type
    from {{ ref('cleansed__mufg__stock_splits') }}
    where last_cum_rights_date is not null
      and ratio_before is not null
      and ratio_after is not null
      and ratio_after != 0
),

consolidations as (
    select
        code,
        last_cum_rights_date,
        effective_date,
        ratio_before / ratio_after as adj_factor,
        'stock_consolidation' as action_type
    from {{ ref('cleansed__mufg__stock_consolidations') }}
    where last_cum_rights_date is not null
      and ratio_before is not null
      and ratio_after is not null
      and ratio_after != 0
),

actions as (
    select * from splits
    union all
    select * from consolidations
),

-- 全銘柄共通の取引日カレンダー（市場全体で1つ、銘柄ごとに持つ必要は無い）。
trading_calendar as (
    select distinct file_date
    from {{ ref('intermediate__jpx__daily_prices') }}
),

with_ex_rights_date as (
    select
        a.code,
        a.action_type,
        a.last_cum_rights_date,
        a.effective_date,
        a.adj_factor,
        (
            select min(tc.file_date)
            from trading_calendar tc
            where tc.file_date > a.last_cum_rights_date
        ) as ex_rights_date
    from actions a
)

select *
from with_ex_rights_date
where ex_rights_date is not null
