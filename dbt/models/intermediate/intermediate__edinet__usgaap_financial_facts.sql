-- 書類(doc_id)単位のUS GAAP経営指標等（14指標）。US GAAP名のitem_nameのみを対象とし、
-- J-GAAP/IFRSの項目とは一切混在させない。設計・連結/個別の扱いは
-- intermediate__edinet__jgaap_financial_factsと同じ（詳細はそちらのコメント参照）。
-- 対象企業数は極めて少ない（実機確認: 10〜14社程度）。
--
-- ordinary_income/capital/payout_ratioはUS GAAPに対応概念が無いため、列自体は
-- 他の中間モデルとの結合(mart側)のためスキーマ互換目的でNULLとして持つ。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/edinet_usgaap_financial_facts.parquet',
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
        '総資産額（US GAAP）、経営指標等',
        '純資産額（US GAAP）、経営指標等',
        '自己資本比率（US GAAP）、経営指標等',
        '当社株主に帰属する純利益又は純損失（△）（US GAAP）、経営指標等',
        '売上高（US GAAP）、経営指標等',
        '基本的１株当たり当社株主に帰属する利益又は損失（△）（US GAAP）、経営指標等',
        '１株当たり株主資本（US GAAP）、経営指標等',
        '株主資本利益率（US GAAP）、経営指標等',
        '株価収益率（US GAAP）、経営指標等',
        '営業活動によるキャッシュ・フロー（US GAAP）、経営指標等',
        '投資活動によるキャッシュ・フロー（US GAAP）、経営指標等',
        '財務活動によるキャッシュ・フロー（US GAAP）、経営指標等'
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
        max(case when item_name = '総資産額（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '総資産額（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as total_assets,
    coalesce(
        max(case when item_name = '純資産額（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '純資産額（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as net_assets,
    coalesce(
        max(case when item_name = '自己資本比率（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '自己資本比率（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as equity_ratio,
    cast(null as decimal(38, 4)) as ordinary_income,
    coalesce(
        max(case when item_name = '当社株主に帰属する純利益又は純損失（△）（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '当社株主に帰属する純利益又は純損失（△）（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as net_income,
    coalesce(
        max(case when item_name = '売上高（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '売上高（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as sales,
    coalesce(
        max(case when item_name = '基本的１株当たり当社株主に帰属する利益又は損失（△）（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '基本的１株当たり当社株主に帰属する利益又は損失（△）（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as eps,
    coalesce(
        max(case when item_name = '１株当たり株主資本（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '１株当たり株主資本（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as bps,
    coalesce(
        max(case when item_name = '株主資本利益率（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '株主資本利益率（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as roe,
    coalesce(
        max(case when item_name = '株価収益率（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '株価収益率（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as per,
    coalesce(
        max(case when item_name = '営業活動によるキャッシュ・フロー（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '営業活動によるキャッシュ・フロー（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as operating_cf,
    coalesce(
        max(case when item_name = '投資活動によるキャッシュ・フロー（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '投資活動によるキャッシュ・フロー（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as investing_cf,
    coalesce(
        max(case when item_name = '財務活動によるキャッシュ・フロー（US GAAP）、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '財務活動によるキャッシュ・フロー（US GAAP）、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as financing_cf,
    cast(null as decimal(38, 4)) as capital,
    cast(null as decimal(38, 4)) as payout_ratio
from with_dei
group by doc_id
