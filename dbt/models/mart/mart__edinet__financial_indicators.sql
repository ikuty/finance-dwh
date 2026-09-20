-- 銘柄(edinet_code)×期(fiscal_year, period_type)ごとの主要財務指標（案B）。
-- cleansed__edinet__report_periods（期間ディメンション）と cleansed__edinet__facts
-- （型付け済み明細、doc_id粒度で全履歴を保持）を doc_id で結合して構築する。
--
-- 会計基準の混在(J-GAAP/IFRS/US GAAP)への対応:
--   別レイヤーに分けず、指標ごとに coalesce() で1列に統合する（「まずはシンプルに
--   始める」という方針）。accounting_standard・has_consolidated列はDEIファクト
--   （会計基準、DEI / 連結決算の有無、DEI）から直接取得する（推測しない）。
--   ordinary_income/capital/payout_ratioはIFRS/US GAAPに対応概念が無いため、
--   それらの会計基準を採用する企業ではNULLになる（意図した挙動）。
--
-- 連結/個別の判定:
--   経営指標等(5期比較)項目はEDINET CSVの「連結・個別」列が使えない(常に'other'、
--   cleansed__edinet__facts冒頭コメント参照)。context_idの`_NonConsolidatedMember`
--   サフィックス有無で判定する(無し=連結、有り=個別、個別のみ提出企業向けに
--   個別へフォールバックする)。
--
-- 「当期」を表すcontext_idの接頭辞は書類種別で異なる(実機確認済み):
--   有価証券報告書(annual): CurrentYear(Instant/Duration)
--   四半期報告書(quarter系): Current(Quarter|YTD)(Instant/Duration)
--   半期報告書(half): Interim(Instant/Duration) または Current(Quarter|YTD)(Instant/Duration)
--     の両方が実在する(2026-09-21実機判明。2024年度制度改正で新設された半期報告書は
--     旧四半期報告書のcontext_idラベルをそのまま流用しているケースがあり、
--     Interim系だけを見ると値が0件になる書類が実在した。両パターンを許容する)。
--
-- 訂正報告書対応: report_periodsは書類(doc_id)粒度で訂正報告書も別行として保持する
-- ため、同一(edinet_code, fiscal_year, period_type)にdoc_idが複数あり得る。
-- submit_date_time最新の1件に絞ってから指標を結合する。

{{ config(
    materialized='external',
    location=env_var('MART_ROOT', '/data/mart') ~ '/edinet_financial_indicators.parquet',
    format='parquet'
) }}

with periods as (
    select
        *,
        row_number() over (
            partition by edinet_code, fiscal_year, period_type
            order by submit_date_time desc
        ) as _rn
    from {{ ref('cleansed__edinet__report_periods') }}
    where filer_category = 'company' and fiscal_year is not null
),

target_periods as (
    select
        doc_id, edinet_code, sec_code, filer_name, fiscal_year, period_type,
        period_start, period_end, regime, doc_type_code
    from periods
    where _rn = 1
),

relevant_facts as (
    select f.doc_id, f.item_name, f.context_id, f.value_num, f.value_text
    from {{ ref('cleansed__edinet__facts') }} f
    inner join target_periods tp on tp.doc_id = f.doc_id
    where f.item_name in (
        -- DEI
        '会計基準、DEI', '連結決算の有無、DEI',
        -- total_assets
        '総資産額、経営指標等', '総資産額（IFRS）、経営指標等', '総資産額（US GAAP）、経営指標等',
        -- net_assets
        '純資産額、経営指標等', '親会社の所有者に帰属する持分（IFRS）、経営指標等', '純資産額（US GAAP）、経営指標等',
        -- equity_ratio
        '自己資本比率、経営指標等', '親会社所有者帰属持分比率（IFRS）、経営指標等', '自己資本比率（US GAAP）、経営指標等',
        -- ordinary_income
        '経常利益又は経常損失（△）、経営指標等',
        -- net_income
        '親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失（△）、経営指標等',
        '当期純利益又は当期純損失（△）、経営指標等',
        '当期利益又は当期損失（△）：親会社の所有者に帰属（IFRS）、経営指標等',
        '当社株主に帰属する純利益又は純損失（△）（US GAAP）、経営指標等',
        -- sales
        '売上高、経営指標等', '営業収益、経営指標等', '経常収益、経営指標等', '営業収入、経営指標等', '営業総収入、経営指標等',
        '売上収益（IFRS）、経営指標等', '売上収益、経営指標等', '売上高（US GAAP）、経営指標等',
        -- eps
        '１株当たり当期純利益又は当期純損失（△）、経営指標等',
        '基本的１株当たり利益又は損失（△）（IFRS）、経営指標等',
        '基本的１株当たり当社株主に帰属する利益又は損失（△）（US GAAP）、経営指標等',
        -- bps
        '１株当たり純資産額、経営指標等',
        '１株当たり親会社所有者帰属持分（IFRS）、経営指標等',
        '１株当たり株主資本（US GAAP）、経営指標等',
        -- roe
        '自己資本利益率、経営指標等',
        '親会社所有者帰属持分利益率（IFRS）、経営指標等',
        '株主資本利益率（US GAAP）、経営指標等',
        -- per
        '株価収益率、経営指標等', '株価収益率（IFRS）、経営指標等', '株価収益率（US GAAP）、経営指標等',
        -- operating_cf
        '営業活動によるキャッシュ・フロー、経営指標等',
        '営業活動によるキャッシュ・フロー（IFRS）、経営指標等',
        '営業活動によるキャッシュ・フロー（US GAAP）、経営指標等',
        -- investing_cf
        '投資活動によるキャッシュ・フロー、経営指標等',
        '投資活動によるキャッシュ・フロー（IFRS）、経営指標等',
        '投資活動によるキャッシュ・フロー（US GAAP）、経営指標等',
        -- financing_cf
        '財務活動によるキャッシュ・フロー、経営指標等',
        '財務活動によるキャッシュ・フロー（IFRS）、経営指標等',
        '財務活動によるキャッシュ・フロー（US GAAP）、経営指標等',
        -- capital
        '資本金、経営指標等',
        -- payout_ratio
        '配当性向、経営指標等'
    )
),

joined as (
    select tp.*, rf.item_name, rf.context_id, rf.value_num, rf.value_text
    from target_periods tp
    inner join relevant_facts rf on rf.doc_id = tp.doc_id
),

current_period_facts as (
    select
        doc_id, doc_type_code, item_name, value_num,
        context_id like '%_NonConsolidatedMember' as is_non_consolidated
    from joined
    where item_name not in ('会計基準、DEI', '連結決算の有無、DEI')
      and case
            when doc_type_code = '120' then regexp_matches(context_id, '^CurrentYear(Instant|Duration)(_NonConsolidatedMember)?$')
            when doc_type_code = '140' then regexp_matches(context_id, '^Current(Quarter|YTD)(Instant|Duration)(_NonConsolidatedMember)?$')
            when doc_type_code = '160' then regexp_matches(context_id, '^(Interim|Current(Quarter|YTD))(Instant|Duration)(_NonConsolidatedMember)?$')
            else false
          end
),

-- 連結優先・個別フォールバックをitem_name単位でまず解決する
per_item as (
    select
        doc_id,
        item_name,
        coalesce(
            max(value_num) filter (where not is_non_consolidated),
            max(value_num) filter (where is_non_consolidated)
        ) as best_value
    from current_period_facts
    group by doc_id, item_name
),

-- item_name単位の値をJ-GAAP/IFRS/US GAAPの優先順でcoalesceし、指標列へpivotする
pivoted as (
    select
        doc_id,
        coalesce(
            max(case when item_name = '総資産額、経営指標等' then best_value end),
            max(case when item_name = '総資産額（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '総資産額（US GAAP）、経営指標等' then best_value end)
        ) as total_assets,
        coalesce(
            max(case when item_name = '純資産額、経営指標等' then best_value end),
            max(case when item_name = '親会社の所有者に帰属する持分（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '純資産額（US GAAP）、経営指標等' then best_value end)
        ) as net_assets,
        coalesce(
            max(case when item_name = '自己資本比率、経営指標等' then best_value end),
            max(case when item_name = '親会社所有者帰属持分比率（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '自己資本比率（US GAAP）、経営指標等' then best_value end)
        ) as equity_ratio,
        max(case when item_name = '経常利益又は経常損失（△）、経営指標等' then best_value end) as ordinary_income,
        coalesce(
            max(case when item_name = '親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失（△）、経営指標等' then best_value end),
            max(case when item_name = '当期純利益又は当期純損失（△）、経営指標等' then best_value end),
            max(case when item_name = '当期利益又は当期損失（△）：親会社の所有者に帰属（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '当社株主に帰属する純利益又は純損失（△）（US GAAP）、経営指標等' then best_value end)
        ) as net_income,
        coalesce(
            max(case when item_name = '売上高、経営指標等' then best_value end),
            max(case when item_name = '営業収益、経営指標等' then best_value end),
            max(case when item_name = '経常収益、経営指標等' then best_value end),
            max(case when item_name = '営業収入、経営指標等' then best_value end),
            max(case when item_name = '営業総収入、経営指標等' then best_value end),
            max(case when item_name = '売上収益（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '売上収益、経営指標等' then best_value end),
            max(case when item_name = '売上高（US GAAP）、経営指標等' then best_value end)
        ) as sales,
        coalesce(
            max(case when item_name = '１株当たり当期純利益又は当期純損失（△）、経営指標等' then best_value end),
            max(case when item_name = '基本的１株当たり利益又は損失（△）（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '基本的１株当たり当社株主に帰属する利益又は損失（△）（US GAAP）、経営指標等' then best_value end)
        ) as eps,
        coalesce(
            max(case when item_name = '１株当たり純資産額、経営指標等' then best_value end),
            max(case when item_name = '１株当たり親会社所有者帰属持分（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '１株当たり株主資本（US GAAP）、経営指標等' then best_value end)
        ) as bps,
        coalesce(
            max(case when item_name = '自己資本利益率、経営指標等' then best_value end),
            max(case when item_name = '親会社所有者帰属持分利益率（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '株主資本利益率（US GAAP）、経営指標等' then best_value end)
        ) as roe,
        coalesce(
            max(case when item_name = '株価収益率、経営指標等' then best_value end),
            max(case when item_name = '株価収益率（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '株価収益率（US GAAP）、経営指標等' then best_value end)
        ) as per,
        coalesce(
            max(case when item_name = '営業活動によるキャッシュ・フロー、経営指標等' then best_value end),
            max(case when item_name = '営業活動によるキャッシュ・フロー（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '営業活動によるキャッシュ・フロー（US GAAP）、経営指標等' then best_value end)
        ) as operating_cf,
        coalesce(
            max(case when item_name = '投資活動によるキャッシュ・フロー、経営指標等' then best_value end),
            max(case when item_name = '投資活動によるキャッシュ・フロー（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '投資活動によるキャッシュ・フロー（US GAAP）、経営指標等' then best_value end)
        ) as investing_cf,
        coalesce(
            max(case when item_name = '財務活動によるキャッシュ・フロー、経営指標等' then best_value end),
            max(case when item_name = '財務活動によるキャッシュ・フロー（IFRS）、経営指標等' then best_value end),
            max(case when item_name = '財務活動によるキャッシュ・フロー（US GAAP）、経営指標等' then best_value end)
        ) as financing_cf,
        max(case when item_name = '資本金、経営指標等' then best_value end) as capital,
        max(case when item_name = '配当性向、経営指標等' then best_value end) as payout_ratio
    from per_item
    group by doc_id
),

dei as (
    select
        doc_id,
        max(case when item_name = '会計基準、DEI' then nullif(value_text, '－') end) as accounting_standard,
        max(case when item_name = '連結決算の有無、DEI' then
            case value_text when 'true' then true when 'false' then false end
        end) as has_consolidated
    from joined
    where item_name in ('会計基準、DEI', '連結決算の有無、DEI')
    group by doc_id
)

select
    tp.doc_id,
    tp.edinet_code,
    tp.sec_code,
    tp.filer_name,
    tp.fiscal_year,
    tp.period_type,
    tp.period_start,
    tp.period_end,
    tp.regime,
    d.accounting_standard,
    d.has_consolidated,
    p.total_assets,
    p.net_assets,
    p.equity_ratio,
    p.ordinary_income,
    p.net_income,
    p.sales,
    p.eps,
    p.bps,
    p.roe,
    p.per,
    p.operating_cf,
    p.investing_cf,
    p.financing_cf,
    p.capital,
    p.payout_ratio
from target_periods tp
left join pivoted p on p.doc_id = tp.doc_id
left join dei d on d.doc_id = tp.doc_id
