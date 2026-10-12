-- 書類(doc_id)単位のDEI(Document Entity Information)。会計基準・連結決算の有無を
-- cleansed__edinet__factsから直接取得する（推測しない）。J-GAAP/IFRS/US GAAP
-- 各intermediateモデル・martの両方から参照される共通部品。
--
-- shares_outstanding(発行済株式総数、2026-09-25追加): 当初は経営指標等の
-- `発行済株式総数（普通株式）、経営指標等`から各会計基準別モデルで個別に抽出していたが
-- （intermediate__edinet__{jgaap,ifrs,usgaap}_financial_facts参照、フォールバック用に
-- 現在も残している）、カバレッジが35,045書類にとどまっていた。「株式の総数等」
-- 開示セクションの`事業年度末現在発行数（株）、発行済株式、株式の総数等`
-- （context_id='FilingDateInstant_OrdinaryShareMember'）の方がカバレッジが77,666書類と
-- 2.2倍高く、こちらを正とする。この項目は会計基準・連結決算スコープに依存しない
-- 企業単位の法的事実（発行済株式数）のため、DEIと同様にここで一元的に抽出する。
--
-- contextの選定(重要): 無サフィックスの`FilingDateInstant`は全種類株式(優先株等含む)の
-- 合算であり、`_OrdinaryShareMember`（普通株式限定）とは概念が異なる(複数種類株式を
-- 持つ企業で実際に値が乖離することを実データで確認済み、約1,686書類)。EPS/BPSの分母は
-- 通常「普通株式数」のため、`_OrdinaryShareMember`を使う（旧ソースの「（普通株式）」
-- 限定と概念を揃える）。
--
-- sec_code(証券コード、DEI、2026-10-13追加): REIT(投資法人)のsec_codeは書類一覧APIの
-- secCodeが常にnull(実機確認、services/ir-disclosure-dl/docs/reit_support_design.md
-- @finance-lake参照)のため、cleansed__edinet__documents→cleansed__edinet__report_periods
-- 経由のsec_code列がREITでは常にNULLになり、mart__edinet__reit_financial_indicators.
-- sec_codeが全件NULLになっていた(2026-10-12発見)。DEIの証券コードタグはREIT書類の
-- 96%(725件中696件)に存在し、1書類内で複数の異なる値を持つケースも無い(実機確認済み)。
-- 通常企業は書類一覧APIのsecCodeで既に足りているため、この列は主にREIT向けだが、
-- 会計基準・連結個別スコープに依存しない企業単位の法的事実という点でshares_outstanding
-- と同じ性質のため、ここに一元的に追加する。

{{ config(
    materialized='external',
    location=env_var('INTERMEDIATE_ROOT', '/data/intermediate') ~ '/edinet_dei_facts.parquet',
    format='parquet'
) }}

select
    doc_id,
    max(case when item_name = '会計基準、DEI' then nullif(value_text, '－') end) as accounting_standard,
    max(case when item_name = '連結決算の有無、DEI' then
        case value_text when 'true' then true when 'false' then false end
    end) as has_consolidated,
    max(case when item_name = '事業年度末現在発行数（株）、発行済株式、株式の総数等'
        and context_id = 'FilingDateInstant_OrdinaryShareMember'
        then value_num end) as shares_outstanding,
    max(case when item_name = '証券コード、DEI' then nullif(value_text, '－') end) as sec_code
from {{ ref('cleansed__edinet__facts') }}
where item_name in (
    '会計基準、DEI', '連結決算の有無、DEI', '事業年度末現在発行数（株）、発行済株式、株式の総数等',
    '証券コード、DEI'
)
group by doc_id
