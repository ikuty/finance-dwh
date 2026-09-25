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
        then value_num end) as shares_outstanding
from {{ ref('cleansed__edinet__facts') }}
where item_name in ('会計基準、DEI', '連結決算の有無、DEI', '事業年度末現在発行数（株）、発行済株式、株式の総数等')
group by doc_id
