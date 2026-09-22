-- salesはYTD累計のため、同一(edinet_code, fiscal_year)内で
-- q1 <= q2/half <= q3 <= annual となるはず（売上高が期中に減少することは無い）。
-- 2026-09-21判明のSMFGバグ（複数候補項目名のうち個別のみの値を誤って採用し、
-- q1のsalesがannualを上回っていた）の再発防止テスト。外部データ不要、
-- 当システム内部の整合性のみで検証する。行が返れば失敗。
--
-- 既知の外れ値: 以下は当システム側の抽出ロジックの問題ではないと判断し調査を
-- 終了したケース。将来的に別の原因が見つかった場合は除外リストの妥当性を
-- 再検討すること（docs/mart_validation.md参照）。
--   - 三井海洋開発(6269)分は2026-09-22、通貨単位(unit_id)を無視していたことが
--     根本原因と判明し、intermediateモデル側にunit_id='JPY'条件を追加して
--     解消済みのため、本リストからは除外した。
--   - 以下6件はelement_idレベルまで確認し、annual書類側のXBRL値自体が
--     四半期報告書と整合しない提出者側のデータ特性と判断（2026-09-21調査）。
--   - メタップス(6172)14期は同一XBRL要素・同一通貨で約4%の小幅な差異のみ
--     （事業区分変更等の可能性、抽出ロジックの問題ではない、2026-09-22調査）。

with known_outliers(sec_code, fiscal_year) as (
    values
        ('23450', 28), ('38530', 26), ('39780', 10), ('60300', 16),
        ('93180', 103), ('61720', 14)
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
