-- EDINET提出書類のうち「期」の概念を持つ定期開示（有価証券報告書・四半期報告書・
-- 半期報告書）を、銘柄×期の単位に正規化した期間ディメンション。cleansed__edinet__facts
-- の集計（銘柄ごと・期ごと）の土台として使う（確認書・内部統制報告書・臨時報告書等の
-- 随時開示はここでは扱わない）。
--
-- 粒度は cleansed__edinet__documents と同じ(1行=1書類)。cleansed__edinet__facts
-- （1行=1書類内の1項目）の集計はしない。
--
-- filer_category(company/fund)の分離(2026-09-19決定):
--   EDINET提出者には、通常の事業会社に加えて投資信託受益証券等のファンド型が
--   混在する(実機確認: ordinance_code='030'、全体の0.04%程度)。JPX側の
--   security_category_ja分離と同じ考え方で、ordinance_code='030'をfundとして
--   独立した属性にする。
--
-- regime(旧制度/移行期/新制度)の判定方式(2026-09-19決定、重要):
--   2024年度から、四半期報告書(第1・第3四半期)の提出義務が廃止され、半期報告書に
--   置き換わった(金融商品取引法改正)。日付の単純な閾値比較では判定できないケースが
--   実データで見つかっている(例: 事業年度が2024-04-01より前に開始するのに
--   四半期報告書が1件も存在しない移行期のケース)。そのため、同一(edinet_code,
--   fiscal_year)内に実際に四半期報告書(q1/q2/q3/quarter)が存在すれば旧制度、
--   半期報告書(half)が存在すれば新制度、どちらも存在しなければ移行期、という
--   **実データの存在有無から導出する**方式にする(regulatory calendarの推測に
--   依存しないため頑健)。ファンド型、またはfiscal_yearが特定できない行は
--   regimeもNULLとする。
--
-- doc_descriptionのテキスト欠損について:
--   ごく少数(doc_type_code別に0.1〜0.2%程度)、doc_descriptionが定型文
--   (「四半期報告書」等)のみで期数・四半期号数を含まない書類が実在する
--   (撤回書類ではなく、period_start/period_endは正常。実機確認済み)。
--   正規表現で抽出できない場合はエラーにせず、fiscal_yearはNULL、
--   四半期のperiod_typeは号数を持たない'quarter'にフォールバックする。

{{ config(
    materialized='external',
    location=env_var('CLEANSED_ROOT', '/data/cleansed') ~ '/edinet_report_periods.parquet',
    format='parquet'
) }}

with base as (
    select
        d.doc_id,
        d.edinet_code,
        d.sec_code,
        d.filer_name,
        case when d.ordinance_code = '030' then 'fund' else 'company' end as filer_category,
        nullif(regexp_extract(d.doc_description, '第([0-9]+)期', 1), '')::integer as fiscal_year,
        case
            when d.doc_type_code = '120' then 'annual'
            when d.doc_type_code = '160' then 'half'
            when d.doc_type_code = '140' then
                case
                    when regexp_extract(d.doc_description, '第([0-9])四半期', 1) <> ''
                        then 'q' || regexp_extract(d.doc_description, '第([0-9])四半期', 1)
                    else 'quarter'
                end
        end as period_type,
        d.period_start,
        d.period_end,
        d.submit_date_time,
        d.doc_type_code,
        d.form_code
    from {{ ref('cleansed__edinet__documents') }} d
    where d.doc_type_code in ('120', '140', '160')
),

regime_flags as (
    select
        edinet_code,
        fiscal_year,
        max(case when period_type in ('q1', 'q2', 'q3', 'quarter') then 1 else 0 end) as has_quarterly,
        max(case when period_type = 'half' then 1 else 0 end) as has_half
    from base
    where filer_category = 'company' and fiscal_year is not null
    group by 1, 2
)

select
    b.doc_id,
    b.edinet_code,
    b.sec_code,
    b.filer_name,
    b.filer_category,
    b.fiscal_year,
    b.period_type,
    b.period_start,
    b.period_end,
    case
        when b.filer_category <> 'company' or b.fiscal_year is null then null
        when rf.has_quarterly = 1 then '旧制度'
        when rf.has_half = 1 then '新制度'
        else '移行期'
    end as regime,
    b.submit_date_time,
    b.doc_type_code,
    b.form_code
from base b
left join regime_flags rf
    on rf.edinet_code = b.edinet_code and rf.fiscal_year = b.fiscal_year
