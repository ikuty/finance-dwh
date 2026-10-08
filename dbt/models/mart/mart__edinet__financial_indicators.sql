-- 銘柄(edinet_code)×期(fiscal_year, period_type)ごとの主要財務指標（案B）。
-- cleansed__edinet__report_periods（期間ディメンション）と、会計基準(J-GAAP/IFRS/
-- US GAAP)ごとに指標を抽出したintermediateモデル3種を doc_id で結合して構築する。
--
-- 会計基準の混在(J-GAAP/IFRS/US GAAP)への対応(2026-09-21、intermediate層導入):
--   当初は1つのmartモデル内で全会計基準の候補item_nameを1つのcoalesce chainに
--   混在させていたが、これが原因のバグが複数見つかった（日本ハム(2282)等:
--   IFRS採用企業でも個別財務諸表は通常J-GAAPのまま作成されるため、1つの書類に
--   J-GAAP名タグとIFRS名タグが混在しうる。優先順位だけで選ぶと会計基準と無関係な
--   値を誤って採用してしまう）。
--
--   会計基準ごとに独立したintermediateモデル
--   （intermediate__edinet__{jgaap,ifrs,usgaap}_financial_facts）で指標を抽出し、
--   このmartは「企業自身のaccounting_standard(DEI由来)を優先し、無ければ他基準へ
--   フォールバックする」だけの薄い結合層にする。連結優先・個別フォールバックの
--   判定（has_consolidated考慮）は各intermediateモデル側で完結しており、この
--   martはそれを意識する必要が無い。
--
-- ordinary_income/capital/payout_ratioはIFRS/US GAAPに対応概念が無いため、
-- それらの会計基準を採用する企業ではNULLになる（意図した挙動、各intermediate
-- モデル側でNULL固定）。
--
-- 訂正報告書対応(2026-10-08、列単位のlatest-non-null方式に変更、重要):
--   report_periodsは書類(doc_id)粒度で訂正報告書も別行として保持するため、同一
--   (edinet_code, fiscal_year, period_type)にdoc_idが複数あり得る。当初
--   submit_date_time最新の1件に絞る(行単位)方式だったが、訂正書類のXBRLは訂正
--   した項目だけを再タグ付けする実務があり(実機確認: イシン株式会社の訂正有報は
--   EPS/BPSタグを持たず、半期比較情報のテキストブロック内にのみ訂正後の値が
--   記載されていた)、行単位で最新を選ぶとその書類に無い項目がNULLになり、訂正前
--   の値まで失ってしまっていた。そのため、書類ごとに計算した指標値を一度combined
--   (per_doc)として保持し、最終selectで各指標列ごとに
--   「list(列 order by submit_date_time desc) filter (where 列 is not null)」の
--   先頭要素を取る(=新しい書類から見て最初に見つかった非NULL値)方式に変更した。
--   doc_id/submit_date_time等のメタデータ列は「全体として最新の書類」を代表値として
--   残すが、個々の指標はその書類に無ければ古い書類から遡る点に注意(1行の値が複数の
--   doc_idに由来しうる、来歴の厳密な単一性は持たない)。
--
-- submit_date_time列(2026-09-25追加): 株価と組み合わせる際、決算期末日ではなく
-- この開示日を基準にする必要がある(決算期末日を基準にすると、実際にはまだ
-- 開示されていない数値を先読みして使う「先読みバイアス」が発生する。実データで
-- 通期は約87〜90日、四半期でも約42〜43日のディスクロージャーラグを確認済み。
-- 詳細はdocs/mart_indicators.md参照)。mart__jpx_edinet__daily_valuation_indicators
-- が「その取引日時点で参照可能な最新の開示」を判定するために使用する。
--
-- headquarters_address/prefecture列(2026-10-04追加): cleansed__edinet__headquarters
-- （本店所在地、都道府県）をdoc_idで結合する。外部からcleansedを直接参照させず
-- martだけで完結させるため(ユーザー判断)、このmartの薄い結合層としての役割に
-- 1つ追加する形で持たせる。

{{ config(
    materialized='external',
    location=env_var('MART_ROOT', '/data/mart') ~ '/edinet_financial_indicators.parquet',
    format='parquet'
) }}

with periods as (
    select
        doc_id, edinet_code, sec_code, filer_name, fiscal_year, period_type,
        period_start, period_end, submit_date_time, regime
    from {{ ref('cleansed__edinet__report_periods') }}
    where filer_category = 'company' and fiscal_year is not null
),

dei as (
    select * from {{ ref('intermediate__edinet__dei_facts') }}
),

hq as (
    select doc_id, address_clean as headquarters_address, prefecture
    from {{ ref('cleansed__edinet__headquarters') }}
),

combined as (
    select
        tp.*,
        d.accounting_standard,
        d.has_consolidated,
        d.shares_outstanding as dei_shares_outstanding,
        hq.headquarters_address,
        hq.prefecture,
        jg.total_assets as jg_total_assets, ifrs.total_assets as ifrs_total_assets, us.total_assets as us_total_assets,
        jg.net_assets as jg_net_assets, ifrs.net_assets as ifrs_net_assets, us.net_assets as us_net_assets,
        jg.equity_ratio as jg_equity_ratio, ifrs.equity_ratio as ifrs_equity_ratio, us.equity_ratio as us_equity_ratio,
        jg.ordinary_income as jg_ordinary_income, ifrs.ordinary_income as ifrs_ordinary_income, us.ordinary_income as us_ordinary_income,
        jg.net_income as jg_net_income, ifrs.net_income as ifrs_net_income, us.net_income as us_net_income,
        jg.sales as jg_sales, ifrs.sales as ifrs_sales, us.sales as us_sales,
        jg.eps as jg_eps, ifrs.eps as ifrs_eps, us.eps as us_eps,
        jg.bps as jg_bps, ifrs.bps as ifrs_bps, us.bps as us_bps,
        jg.roe as jg_roe, ifrs.roe as ifrs_roe, us.roe as us_roe,
        jg.per as jg_per, ifrs.per as ifrs_per, us.per as us_per,
        jg.operating_cf as jg_operating_cf, ifrs.operating_cf as ifrs_operating_cf, us.operating_cf as us_operating_cf,
        jg.investing_cf as jg_investing_cf, ifrs.investing_cf as ifrs_investing_cf, us.investing_cf as us_investing_cf,
        jg.financing_cf as jg_financing_cf, ifrs.financing_cf as ifrs_financing_cf, us.financing_cf as us_financing_cf,
        jg.capital as jg_capital, ifrs.capital as ifrs_capital, us.capital as us_capital,
        jg.payout_ratio as jg_payout_ratio, ifrs.payout_ratio as ifrs_payout_ratio, us.payout_ratio as us_payout_ratio,
        jg.shares_outstanding as jg_shares_outstanding, ifrs.shares_outstanding as ifrs_shares_outstanding, us.shares_outstanding as us_shares_outstanding,
        jg.diluted_eps as jg_diluted_eps, ifrs.diluted_eps as ifrs_diluted_eps, us.diluted_eps as us_diluted_eps,
        jg.comprehensive_income as jg_comprehensive_income, ifrs.comprehensive_income as ifrs_comprehensive_income, us.comprehensive_income as us_comprehensive_income,
        jg.cash_and_equivalents as jg_cash_and_equivalents, ifrs.cash_and_equivalents as ifrs_cash_and_equivalents, us.cash_and_equivalents as us_cash_and_equivalents,
        jg.dividend_per_share as jg_dividend_per_share, ifrs.dividend_per_share as ifrs_dividend_per_share, us.dividend_per_share as us_dividend_per_share
    from periods tp
    left join dei d on d.doc_id = tp.doc_id
    left join hq on hq.doc_id = tp.doc_id
    left join {{ ref('intermediate__edinet__jgaap_financial_facts') }} jg on jg.doc_id = tp.doc_id
    left join {{ ref('intermediate__edinet__ifrs_financial_facts') }} ifrs on ifrs.doc_id = tp.doc_id
    left join {{ ref('intermediate__edinet__usgaap_financial_facts') }} us on us.doc_id = tp.doc_id
),

per_doc as (
select
    doc_id,
    edinet_code,
    sec_code,
    filer_name,
    fiscal_year,
    period_type,
    period_start,
    period_end,
    submit_date_time,
    regime,
    accounting_standard,
    has_consolidated,
    headquarters_address,
    prefecture,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_total_assets end,
        case when accounting_standard = 'Japan GAAP' then jg_total_assets end,
        case when accounting_standard = 'US GAAP' then us_total_assets end,
        ifrs_total_assets, jg_total_assets, us_total_assets
    ) as total_assets,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_net_assets end,
        case when accounting_standard = 'Japan GAAP' then jg_net_assets end,
        case when accounting_standard = 'US GAAP' then us_net_assets end,
        ifrs_net_assets, jg_net_assets, us_net_assets
    ) as net_assets,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_equity_ratio end,
        case when accounting_standard = 'Japan GAAP' then jg_equity_ratio end,
        case when accounting_standard = 'US GAAP' then us_equity_ratio end,
        ifrs_equity_ratio, jg_equity_ratio, us_equity_ratio
    ) as equity_ratio,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_ordinary_income end,
        case when accounting_standard = 'Japan GAAP' then jg_ordinary_income end,
        case when accounting_standard = 'US GAAP' then us_ordinary_income end,
        ifrs_ordinary_income, jg_ordinary_income, us_ordinary_income
    ) as ordinary_income,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_net_income end,
        case when accounting_standard = 'Japan GAAP' then jg_net_income end,
        case when accounting_standard = 'US GAAP' then us_net_income end,
        ifrs_net_income, jg_net_income, us_net_income
    ) as net_income,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_sales end,
        case when accounting_standard = 'Japan GAAP' then jg_sales end,
        case when accounting_standard = 'US GAAP' then us_sales end,
        ifrs_sales, jg_sales, us_sales
    ) as sales,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_eps end,
        case when accounting_standard = 'Japan GAAP' then jg_eps end,
        case when accounting_standard = 'US GAAP' then us_eps end,
        ifrs_eps, jg_eps, us_eps
    ) as eps,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_bps end,
        case when accounting_standard = 'Japan GAAP' then jg_bps end,
        case when accounting_standard = 'US GAAP' then us_bps end,
        ifrs_bps, jg_bps, us_bps
    ) as bps,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_roe end,
        case when accounting_standard = 'Japan GAAP' then jg_roe end,
        case when accounting_standard = 'US GAAP' then us_roe end,
        ifrs_roe, jg_roe, us_roe
    ) as roe,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_per end,
        case when accounting_standard = 'Japan GAAP' then jg_per end,
        case when accounting_standard = 'US GAAP' then us_per end,
        ifrs_per, jg_per, us_per
    ) as per,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_operating_cf end,
        case when accounting_standard = 'Japan GAAP' then jg_operating_cf end,
        case when accounting_standard = 'US GAAP' then us_operating_cf end,
        ifrs_operating_cf, jg_operating_cf, us_operating_cf
    ) as operating_cf,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_investing_cf end,
        case when accounting_standard = 'Japan GAAP' then jg_investing_cf end,
        case when accounting_standard = 'US GAAP' then us_investing_cf end,
        ifrs_investing_cf, jg_investing_cf, us_investing_cf
    ) as investing_cf,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_financing_cf end,
        case when accounting_standard = 'Japan GAAP' then jg_financing_cf end,
        case when accounting_standard = 'US GAAP' then us_financing_cf end,
        ifrs_financing_cf, jg_financing_cf, us_financing_cf
    ) as financing_cf,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_capital end,
        case when accounting_standard = 'Japan GAAP' then jg_capital end,
        case when accounting_standard = 'US GAAP' then us_capital end,
        ifrs_capital, jg_capital, us_capital
    ) as capital,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_payout_ratio end,
        case when accounting_standard = 'Japan GAAP' then jg_payout_ratio end,
        case when accounting_standard = 'US GAAP' then us_payout_ratio end,
        ifrs_payout_ratio, jg_payout_ratio, us_payout_ratio
    ) as payout_ratio,
    -- 「株式の総数等」開示由来のdei_shares_outstandingを優先する（2026-09-25変更、
    -- カバレッジ77,666書類・経営指標等ベースの2.2倍。詳細はintermediate__edinet__
    -- dei_factsのコメント参照）。無ければ経営指標等ベースの会計基準別coalesceに
    -- フォールバックする（残り約1.9%の書類をカバー）。
    coalesce(
        dei_shares_outstanding,
        case when accounting_standard = 'IFRS' then ifrs_shares_outstanding end,
        case when accounting_standard = 'Japan GAAP' then jg_shares_outstanding end,
        case when accounting_standard = 'US GAAP' then us_shares_outstanding end,
        ifrs_shares_outstanding, jg_shares_outstanding, us_shares_outstanding
    ) as shares_outstanding,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_diluted_eps end,
        case when accounting_standard = 'Japan GAAP' then jg_diluted_eps end,
        case when accounting_standard = 'US GAAP' then us_diluted_eps end,
        ifrs_diluted_eps, jg_diluted_eps, us_diluted_eps
    ) as diluted_eps,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_comprehensive_income end,
        case when accounting_standard = 'Japan GAAP' then jg_comprehensive_income end,
        case when accounting_standard = 'US GAAP' then us_comprehensive_income end,
        ifrs_comprehensive_income, jg_comprehensive_income, us_comprehensive_income
    ) as comprehensive_income,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_cash_and_equivalents end,
        case when accounting_standard = 'Japan GAAP' then jg_cash_and_equivalents end,
        case when accounting_standard = 'US GAAP' then us_cash_and_equivalents end,
        ifrs_cash_and_equivalents, jg_cash_and_equivalents, us_cash_and_equivalents
    ) as cash_and_equivalents,
    coalesce(
        case when accounting_standard = 'IFRS' then ifrs_dividend_per_share end,
        case when accounting_standard = 'Japan GAAP' then jg_dividend_per_share end,
        case when accounting_standard = 'US GAAP' then us_dividend_per_share end,
        ifrs_dividend_per_share, jg_dividend_per_share, us_dividend_per_share
    ) as dividend_per_share
from combined
)

select
    edinet_code,
    fiscal_year,
    period_type,
    (list(doc_id order by submit_date_time desc))[1] as doc_id,
    (list(sec_code order by submit_date_time desc) filter (where sec_code is not null))[1] as sec_code,
    (list(filer_name order by submit_date_time desc) filter (where filer_name is not null))[1] as filer_name,
    (list(period_start order by submit_date_time desc) filter (where period_start is not null))[1] as period_start,
    (list(period_end order by submit_date_time desc) filter (where period_end is not null))[1] as period_end,
    (list(submit_date_time order by submit_date_time desc))[1] as submit_date_time,
    (list(regime order by submit_date_time desc) filter (where regime is not null))[1] as regime,
    (list(accounting_standard order by submit_date_time desc) filter (where accounting_standard is not null))[1] as accounting_standard,
    (list(has_consolidated order by submit_date_time desc) filter (where has_consolidated is not null))[1] as has_consolidated,
    (list(headquarters_address order by submit_date_time desc) filter (where headquarters_address is not null))[1] as headquarters_address,
    (list(prefecture order by submit_date_time desc) filter (where prefecture is not null))[1] as prefecture,
    (list(total_assets order by submit_date_time desc) filter (where total_assets is not null))[1] as total_assets,
    (list(net_assets order by submit_date_time desc) filter (where net_assets is not null))[1] as net_assets,
    (list(equity_ratio order by submit_date_time desc) filter (where equity_ratio is not null))[1] as equity_ratio,
    (list(ordinary_income order by submit_date_time desc) filter (where ordinary_income is not null))[1] as ordinary_income,
    (list(net_income order by submit_date_time desc) filter (where net_income is not null))[1] as net_income,
    (list(sales order by submit_date_time desc) filter (where sales is not null))[1] as sales,
    (list(eps order by submit_date_time desc) filter (where eps is not null))[1] as eps,
    (list(bps order by submit_date_time desc) filter (where bps is not null))[1] as bps,
    (list(roe order by submit_date_time desc) filter (where roe is not null))[1] as roe,
    (list(per order by submit_date_time desc) filter (where per is not null))[1] as per,
    (list(operating_cf order by submit_date_time desc) filter (where operating_cf is not null))[1] as operating_cf,
    (list(investing_cf order by submit_date_time desc) filter (where investing_cf is not null))[1] as investing_cf,
    (list(financing_cf order by submit_date_time desc) filter (where financing_cf is not null))[1] as financing_cf,
    (list(capital order by submit_date_time desc) filter (where capital is not null))[1] as capital,
    (list(payout_ratio order by submit_date_time desc) filter (where payout_ratio is not null))[1] as payout_ratio,
    (list(shares_outstanding order by submit_date_time desc) filter (where shares_outstanding is not null))[1] as shares_outstanding,
    (list(diluted_eps order by submit_date_time desc) filter (where diluted_eps is not null))[1] as diluted_eps,
    (list(comprehensive_income order by submit_date_time desc) filter (where comprehensive_income is not null))[1] as comprehensive_income,
    (list(cash_and_equivalents order by submit_date_time desc) filter (where cash_and_equivalents is not null))[1] as cash_and_equivalents,
    (list(dividend_per_share order by submit_date_time desc) filter (where dividend_per_share is not null))[1] as dividend_per_share
from per_doc
group by edinet_code, fiscal_year, period_type
