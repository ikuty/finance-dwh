-- 現金及び現金同等物の残高(cash_and_equivalents)は資産の一部であり、総資産額
-- (total_assets)を超えることは会計上あり得ない。異なるconsolidation scope
-- （連結/個別）から誤って組み合わせた場合にこの関係が崩れうる。外部データ不要。
-- 行が返れば失敗（total_assets_gte_net_assetsと同じ絶対不変条件、severity既定=error）。
--
-- 既知の外れ値: sec_code=43120（37期q3）は、cash_and_equivalentsが隣接する期
-- （36期annual: 103.4億円、37期q2: 114.0億円、38期q3: 148.0億円）に対して約1000倍
-- （11.88兆円）という桁違いの値だった。element_idは正しいタグ
-- （jpcrp_cor:CashAndCashEquivalentsSummaryOfBusinessResults）であり抽出ロジックの
-- 問題ではなく、提出者側のXBRL入力ミス（単位の付け間違い等）と判断した
-- （2026-09-25調査、docs/mart_validation.md参照）。

with known_outliers(sec_code, fiscal_year) as (
    values
        ('43120', 37)
)

select m.doc_id, m.edinet_code, m.fiscal_year, m.period_type, m.cash_and_equivalents, m.total_assets
from {{ ref('mart__edinet__financial_indicators') }} m
left join known_outliers ko
    on ko.sec_code = m.sec_code and ko.fiscal_year = m.fiscal_year
where m.cash_and_equivalents is not null
  and m.total_assets is not null
  and m.cash_and_equivalents > m.total_assets
  and ko.sec_code is null
