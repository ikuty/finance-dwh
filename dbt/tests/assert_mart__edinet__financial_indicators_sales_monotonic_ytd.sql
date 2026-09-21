-- salesはYTD累計のため、同一(edinet_code, fiscal_year)内で
-- q1 <= q2/half <= q3 <= annual となるはず（売上高が期中に減少することは無い）。
-- 2026-09-21判明のSMFGバグ（複数候補項目名のうち個別のみの値を誤って採用し、
-- q1のsalesがannualを上回っていた）の再発防止テスト。外部データ不要、
-- 当システム内部の整合性のみで検証する。行が返れば失敗。
--
-- 既知の外れ値(2026-09-21調査、intermediate層導入後も残存): 以下7件は
-- element_idレベルまで確認した結果、当システム側の抽出ロジックの問題ではなく、
-- annual書類側のXBRL値自体が四半期報告書と整合しない特異点と判断し、
-- 調査を終了した（提出者側のデータ特性の可能性、docs/mart_validation.md参照）。
-- 将来的に別の原因が見つかった場合は除外リストの妥当性を再検討すること。

with known_outliers(sec_code, fiscal_year) as (
    values
        ('23450', 28), ('38530', 26), ('39780', 10), ('60300', 16),
        ('62690', 38), ('62690', 40), ('93180', 103)
),

ordered as (
    select
        edinet_code,
        sec_code,
        fiscal_year,
        period_type,
        sales,
        case period_type
            when 'q1' then 1
            when 'half' then 2
            when 'q2' then 2
            when 'q3' then 3
            when 'annual' then 4
        end as period_order
    from {{ ref('mart__edinet__financial_indicators') }}
    where sales is not null
),

pairs as (
    select
        a.edinet_code,
        a.sec_code,
        a.fiscal_year,
        a.period_type as earlier_period,
        a.sales as earlier_sales,
        b.period_type as later_period,
        b.sales as later_sales
    from ordered a
    join ordered b
        on a.edinet_code = b.edinet_code
        and a.fiscal_year = b.fiscal_year
        and a.period_order < b.period_order
)

select p.*
from pairs p
left join known_outliers ko
    on ko.sec_code = p.sec_code and ko.fiscal_year = p.fiscal_year
where p.later_sales < p.earlier_sales
  and ko.sec_code is null
