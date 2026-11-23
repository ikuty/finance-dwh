-- 上場しているのに決算短信が1件もlandingに無い銘柄を検知する(finance-dwh #106)。
--
-- ir-disclosure-dl(finance-lake)は、対象seed(seed/edinet_common_stock_issuers.csv)にある会社だけ
-- 決算短信を取得する。seedは固定のため、seed作成後の新規上場や、作成時に漏れた会社は、日次の
-- 監視でもバックフィルでも取得されず、PBRなどが空のままになる(2026-11-22に、三菱商事・
-- 三菱重工業・三菱自動車工業・商船三井等と新規上場の約30社で発覚し、21社を追加した)。
-- このテストは、そうした漏れに気づくための目印。
--
-- 対象: J-Quants銘柄マスタの最新日で、市場区分がプライム・スタンダード・グロース、優先株式でなく、
-- EDINETの書類(提出者のsec_code)があり、決算短信(landing)に同じ銘柄コード(4桁)の行が1件も無い銘柄。
-- 行が返ればseedの更新が必要: finance-lake の services/ir-disclosure-dl/docs/seed_update_runbook.md
-- の手順で、seedに追加して決算短信を取得する。
--
-- severity=warn(buildは失敗させない)。新しい決算短信の取り込み・Slack通知の対象
-- (meta.notify_slack=true、assert_mart__edinet__financial_indicators_eps_bps_plausibleと同じ仕組み)。

{{ config(severity='warn', meta={'notify_slack': true}) }}

with latest_master as (
    select code, company_name_ja, market_name
    from {{ ref('cleansed__jquants__listed_master') }}
    where file_date = (select max(file_date) from {{ ref('cleansed__jquants__listed_master') }})
      and market_name in ('プライム', 'スタンダード', 'グロース')
      and company_name_ja not like '%優先株式%'
),

edinet_filers as (
    select
        sec_code,
        any_value(edinet_code) as edinet_code,
        min(cast(submit_date_time as date)) as first_edinet_doc
    from {{ ref('cleansed__edinet__documents') }}
    where sec_code is not null and sec_code <> ''
    group by sec_code
)

select
    m.code,
    m.company_name_ja,
    m.market_name,
    e.edinet_code,
    e.first_edinet_doc
from latest_master m
inner join edinet_filers e on e.sec_code = m.code
where left(m.code, 4) not in (
    select left(sec_code, 4)
    from {{ ref('cleansed__ir_disclosure__kessan_tanshin') }}
    where sec_code is not null
)
order by m.code
