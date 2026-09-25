-- 書類(doc_id)単位のJ-GAAP経営指標等（19指標）。J-GAAP名のitem_nameのみを対象とし、
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
--
-- 通貨単位の判定(2026-09-22判明、重要):
--   IFRS採用企業の一部（三井海洋開発(6269)等）は、経営指標等のIFRSタグをUSD建てで
--   開示し、同一書類内にJPY建てのJ-GAAP名タグも別途存在するケースがある(個別/単体は
--   J-GAAPのまま作成される慣行と同様、海外事業中心の企業が連結をUSD建てで開示する
--   ため)。生の数値をそのまま比較すると通貨単位の違いにより無関係な値に見える
--   （USD建ての値とJPY建ての値を桁で比較すると全く整合しない）。金額系の指標は
--   unit_id='JPY'（1株当たり指標はunit_id='JPYPerShares'）を必須条件に加え、
--   外貨建ての値は個別/連結と同様にフォールバック対象外とする(無ければNULL、
--   他の会計基準側でJPY建ての値が見つかればそちらが採用される設計、mart側参照)。
--   比率系(equity_ratio/roe/per/payout_ratio)は無単位(pure)のためこの条件は不要。
--
-- 2026-09-25追加(4指標): shares_outstanding(発行済株式総数)/diluted_eps(潜在株式調整後
-- EPS)/comprehensive_income(包括利益)/cash_and_equivalents(現金及び現金同等物残高)。
-- shares_outstandingは会計基準に依存しない項目(unit_id='shares')のため、item_name自体は
-- J-GAAP/IFRS/US GAAPの3モデル共通で同一だが、連結/個別の扱いは他指標と同じフォールバック
-- 構造を適用する(経営指標等表の中で連結会社・提出会社単体の両方に同じitem_nameが現れる
-- ため)。comprehensive_incomeはnet_incomeとの間に単純な近似関係が立てられない(その他の
-- 包括利益の増減が不明なため)ため、mart側に専用の整合性テストは設けない。
--
-- 2026-09-25追加(dividend_per_share、1株当たり配当額): shares_outstandingと同様、
-- IFRS/US GAAP専用のitem_nameが存在せず3モデル共通で同一のため、コメントの意味では
-- 会計基準に依存しない。ただし他の指標とは異なりhas_consolidatedによる個別値
-- フォールバック制限を適用しない(2026-09-25実データ検証で判明): 配当額は連結決算
-- 作成企業であっても経営指標等表で連結コンテキストのタグが付くことは稀（実データ
-- 全体で連結コンテキスト142件 vs 個別コンテキスト160,355件、99.9%が個別）。
-- total_assets/salesのような「個別値が連結値の代替として比較不可能」という問題が
-- 配当額には当てはまらない（1株当たり配当は連結・個別で本質的に同一の、企業単位の
-- 意思決定であり、規模の異なる指標ではない）ため、常に個別値へフォールバックする。

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
    select f.doc_id, f.item_name, f.context_id, f.value_num, f.unit_id, td.doc_type_code
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
        '配当性向、経営指標等',
        '発行済株式総数（普通株式）、経営指標等',
        '潜在株式調整後１株当たり当期純利益、経営指標等',
        '包括利益、経営指標等',
        '現金及び現金同等物の残高、経営指標等',
        '１株当たり配当額、経営指標等'
    )
),

current_period_facts as (
    select
        doc_id, item_name, value_num, unit_id,
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
        max(case when item_name = '総資産額、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '総資産額、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
        end
    ) as total_assets,
    coalesce(
        max(case when item_name = '純資産額、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '純資産額、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
        end
    ) as net_assets,
    coalesce(
        max(case when item_name = '自己資本比率、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '自己資本比率、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as equity_ratio,
    coalesce(
        max(case when item_name = '経常利益又は経常損失（△）、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '経常利益又は経常損失（△）、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
        end
    ) as ordinary_income,
    coalesce(
        max(case when item_name = '親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失（△）、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        max(case when item_name = '当期純利益又は当期純損失（△）、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            coalesce(
                max(case when item_name = '親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失（△）、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end),
                max(case when item_name = '当期純利益又は当期純損失（△）、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
            )
        end
    ) as net_income,
    coalesce(
        max(case when item_name = '売上高、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        max(case when item_name = '営業収益、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        max(case when item_name = '経常収益、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        max(case when item_name = '営業収入、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        max(case when item_name = '営業総収入、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            coalesce(
                max(case when item_name = '売上高、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end),
                max(case when item_name = '営業収益、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end),
                max(case when item_name = '経常収益、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end),
                max(case when item_name = '営業収入、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end),
                max(case when item_name = '営業総収入、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
            )
        end
    ) as sales,
    coalesce(
        max(case when item_name = '１株当たり当期純利益又は当期純損失（△）、経営指標等' and not is_non_consolidated and unit_id = 'JPYPerShares' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '１株当たり当期純利益又は当期純損失（△）、経営指標等' and is_non_consolidated and unit_id = 'JPYPerShares' then value_num end)
        end
    ) as eps,
    coalesce(
        max(case when item_name = '１株当たり純資産額、経営指標等' and not is_non_consolidated and unit_id = 'JPYPerShares' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '１株当たり純資産額、経営指標等' and is_non_consolidated and unit_id = 'JPYPerShares' then value_num end)
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
        max(case when item_name = '営業活動によるキャッシュ・フロー、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '営業活動によるキャッシュ・フロー、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
        end
    ) as operating_cf,
    coalesce(
        max(case when item_name = '投資活動によるキャッシュ・フロー、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '投資活動によるキャッシュ・フロー、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
        end
    ) as investing_cf,
    coalesce(
        max(case when item_name = '財務活動によるキャッシュ・フロー、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '財務活動によるキャッシュ・フロー、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
        end
    ) as financing_cf,
    coalesce(
        max(case when item_name = '資本金、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '資本金、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
        end
    ) as capital,
    coalesce(
        max(case when item_name = '配当性向、経営指標等' and not is_non_consolidated then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '配当性向、経営指標等' and is_non_consolidated then value_num end)
        end
    ) as payout_ratio,
    coalesce(
        max(case when item_name = '発行済株式総数（普通株式）、経営指標等' and not is_non_consolidated and unit_id = 'shares' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '発行済株式総数（普通株式）、経営指標等' and is_non_consolidated and unit_id = 'shares' then value_num end)
        end
    ) as shares_outstanding,
    coalesce(
        max(case when item_name = '潜在株式調整後１株当たり当期純利益、経営指標等' and not is_non_consolidated and unit_id = 'JPYPerShares' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '潜在株式調整後１株当たり当期純利益、経営指標等' and is_non_consolidated and unit_id = 'JPYPerShares' then value_num end)
        end
    ) as diluted_eps,
    coalesce(
        max(case when item_name = '包括利益、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '包括利益、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
        end
    ) as comprehensive_income,
    coalesce(
        max(case when item_name = '現金及び現金同等物の残高、経営指標等' and not is_non_consolidated and unit_id = 'JPY' then value_num end),
        case when not bool_or(has_consolidated) then
            max(case when item_name = '現金及び現金同等物の残高、経営指標等' and is_non_consolidated and unit_id = 'JPY' then value_num end)
        end
    ) as cash_and_equivalents,
    coalesce(
        max(case when item_name = '１株当たり配当額、経営指標等' and not is_non_consolidated and unit_id = 'JPYPerShares' then value_num end),
        max(case when item_name = '１株当たり配当額、経営指標等' and is_non_consolidated and unit_id = 'JPYPerShares' then value_num end)
    ) as dividend_per_share
from with_dei
group by doc_id
