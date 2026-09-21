-- 書類(doc_id)単位のJ-GAAP経営指標等（14指標）。J-GAAP名のitem_nameのみを対象とし、
-- IFRS/US GAAPの項目とは一切混在させない（2026-09-21、会計基準をまたいだcoalesceが
-- 引き起こしたバグ（企業自身の会計基準と無関係な値を誤って採用）を受けて、
-- mart__edinet__financial_indicatorsから分離）。
--
-- 連結優先・個別フォールバック:
--   連結値を優先し、個別値へのフォールバックは「連結決算を作成しない企業
--   (has_consolidated=false、DEIの連結決算の有無より)」の場合のみ許可する。
--   連結決算を作成する企業の個別値は、連結の代替として比較可能でないケースがある
--   （例: 持株会社の個別営業収入は連結事業収益と全くスケールが異なる。日本ハム
--   (2282)77期annualで実機確認: 連結IFRS売上収益のタグが無く個別J-GAAP売上高
--   （775億円）のみ存在、これは連結ベースの経常収益(9,532億円、q3実績から推定)とは
--   別物）。has_consolidated is null（DEI取得不可）の場合は許容側(false相当)とする。
--
-- 「当期」を表すcontext_idの接頭辞は書類種別で異なる。詳細はmart側コメント参照。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/edinet_jgaap_financial_facts.parquet',
    format='parquet'
) }}

with target_docs as (
    select doc_id, doc_type_code
    from {{ ref('cleansed__edinet__report_periods') }}
    where filer_category = 'company'
),

relevant_facts as (
    select f.doc_id, f.item_name, f.context_id, f.value_num, td.doc_type_code
    from {{ ref('cleansed__edinet__facts') }} f
    inner join target_docs td on td.doc_id = f.doc_id
    where f.item_name in (
        '総資産額、経営指標等',
        '純資産額、経営指標等',
        '自己資本比率、経営指標等',
        '経常利益又は経常損失（△）、経営指標等',
        '親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失（△）、経営指標等',
        '当期純利益又は当期純損失（△）、経営指標等',
        '売上高、経営指標等', '営業収益、経営指標等', '経常収益、経営指標等',
        '営業収入、経営指標等', '営業総収入、経営指標等',
        '１株当たり当期純利益又は当期純損失（△）、経営指標等',
        '１株当たり純資産額、経営指標等',
        '自己資本利益率、経営指標等',
        '株価収益率、経営指標等',
        '営業活動によるキャッシュ・フロー、経営指標等',
        '投資活動によるキャッシュ・フロー、経営指標等',
        '財務活動によるキャッシュ・フロー、経営指標等',
        '資本金、経営指標等',
        '配当性向、経営指標等'
    )
),

current_period_facts as (
    select
        doc_id, item_name, value_num,
        context_id like '%_NonConsolidatedMember' as is_non_consolidated
    from relevant_facts
    where case
            when doc_type_code = '120' then regexp_matches(context_id, '^CurrentYear(Instant|Duration)(_NonConsolidatedMember)?$')
            when doc_type_code = '140' then regexp_matches(context_id, '^Current(Quarter|YTD)(Instant|Duration)(_NonConsolidatedMember)?$')
            when doc_type_code = '160' then regexp_matches(context_id, '^(Interim|Current(Quarter|YTD))(Instant|Duration)(_NonConsolidatedMember)?$')
            else false
          end
),

with_dei as (
    select cpf.*, coalesce(d.has_consolidated, false) as has_consolidated
    from current_period_facts cpf
    left join {{ ref('intermediate__edinet__dei_facts') }} d on d.doc_id = cpf.doc_id
)

select
    doc_id,
    coalesce(
        max(case when item_name = '総資産額、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '総資産額、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as total_assets,
    coalesce(
        max(case when item_name = '純資産額、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '純資産額、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as net_assets,
    coalesce(
        max(case when item_name = '自己資本比率、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '自己資本比率、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as equity_ratio,
    coalesce(
        max(case when item_name = '経常利益又は経常損失（△）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '経常利益又は経常損失（△）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as ordinary_income,
    coalesce(
        max(case when item_name = '親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失（△）、経営指標等' and not is_non_consolidated then value_num end),
        max(case when item_name = '当期純利益又は当期純損失（△）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            coalesce(
                max(case when item_name = '親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失（△）、経営指標等' and is_non_consolidated then value_num end),
                max(case when item_name = '当期純利益又は当期純損失（△）、経営指標等' and is_non_consolidated then value_num end)
            )
        end
    ) as net_income,
    coalesce(
        max(case when item_name = '売上高、経営指標等' and not is_non_consolidated then value_num end),
        max(case when item_name = '営業収益、経営指標等' and not is_non_consolidated then value_num end),
        max(case when item_name = '経常収益、経営指標等' and not is_non_consolidated then value_num end),
        max(case when item_name = '営業収入、経営指標等' and not is_non_consolidated then value_num end),
        max(case when item_name = '営業総収入、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            coalesce(
                max(case when item_name = '売上高、経営指標等' and is_non_consolidated then value_num end),
                max(case when item_name = '営業収益、経営指標等' and is_non_consolidated then value_num end),
                max(case when item_name = '経常収益、経営指標等' and is_non_consolidated then value_num end),
                max(case when item_name = '営業収入、経営指標等' and is_non_consolidated then value_num end),
                max(case when item_name = '営業総収入、経営指標等' and is_non_consolidated then value_num end)
            )
        end
    ) as sales,
    coalesce(
        max(case when item_name = '１株当たり当期純利益又は当期純損失（△）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '１株当たり当期純利益又は当期純損失（△）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as eps,
    coalesce(
        max(case when item_name = '１株当たり純資産額、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '１株当たり純資産額、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as bps,
    coalesce(
        max(case when item_name = '自己資本利益率、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '自己資本利益率、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as roe,
    coalesce(
        max(case when item_name = '株価収益率、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '株価収益率、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as per,
    coalesce(
        max(case when item_name = '営業活動によるキャッシュ・フロー、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '営業活動によるキャッシュ・フロー、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as operating_cf,
    coalesce(
        max(case when item_name = '投資活動によるキャッシュ・フロー、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '投資活動によるキャッシュ・フロー、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as investing_cf,
    coalesce(
        max(case when item_name = '財務活動によるキャッシュ・フロー、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '財務活動によるキャッシュ・フロー、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as financing_cf,
    coalesce(
        max(case when item_name = '資本金、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '資本金、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as capital,
    coalesce(
        max(case when item_name = '配当性向、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '配当性向、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as payout_ratio
from with_dei
group by doc_id
