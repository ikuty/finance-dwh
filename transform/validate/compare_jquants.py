"""当DWHが独自ロジックで算出した財務指標を、第三者データ(J-Quants API)と照合する。

不定期・手動実行専用（Prefectの日次フローには含めない）。cleansed__jquants__
fin_summary(検証時のみ手動実行で作成)とmart__jpx_edinet__quarterly_fundamentals
(DWH確定値)の2つのParquetを読み、企業×期間ごとに主要項目を突き合わせる。

実行例:
    docker compose run --rm --entrypoint python transform validate/compare_jquants.py

対象の絞り込み(いずれも実機データで判明、詳細は各判定根拠を参照):
  - doc_typeに'FinancialStatements'を含む行のみを対象にする(許可リスト方式)。
    'EarnForecastRevision'(業績予想の修正)・'DividendForecastRevision'(配当予想の
    修正)等の「お知らせ系」開示は実績値列が全てNULLで、除外リスト方式だと新しい
    「お知らせ系」doc_typeが見つかるたびにモグラ叩きになる(2026-10-06実機確認、
    EarnForecastRevisionのみ除外していた初期実装でDividendForecastRevisionを
    見逃していた)。
  - REIT(doc_typeに'REIT'を含む行)は対象外として件数のみ報告する。EDINETの
    書類一覧APIがREIT(投資法人)にsecCodeを付与しないため、edinet-dl(レイク層)
    がそもそもREITの書類を取得しておらず、DWH側に該当データが存在しない
    (2026-10-07判明、別タスク化。詳細はedinet-dl/services/edinet-dl/CLAUDE.md
    「REIT（投資法人）対応の検討」参照)。XBRL本体のDEI項目には証券コードが
    存在するため将来対応は可能だが、財務数値側は投資法人専用のXBRL名前空間
    (jpsps_cor:)に対応するDWH側の抽出ロジックが別途必要。
  - J-QuantsのCodeは5桁(末尾1桁は株式種別)。DWHのjpx_codeは4桁のため、
    Codeの先頭4桁で結合する。

数値の一致判定:
  - sales(売上高)は、J-Quants側が決算短信・有報のサマリー表に記載された
    「百万円単位・切り捨て」表記のため、DWHの正確な円単位の値とは末尾6桁分が
    異なりうる(2026-10-06実機確認、全18件の不一致がこのパターンで説明できた)。
    「DWH値を100万円単位に切り捨てた値 = J-Quants値」であれば一致とみなす。
  - eps/bps/dividend_per_shareは厳密な完全一致で判定する
    (実機データでこれらに丸め誤差は見られなかった)。
  - shares_outstandingは、決算期末が株式分割・併合の権利確定日〜効力発生日の
    間に位置する開示で、DWHが分割前の株数を保持する一方、J-Quantsは分割後の
    株数を遡及表示する構造的な差異がある(2026-10-07実機確認、全量データで
    399件中339件がこのパターン。詳細はmart__jpx_edinet__quarterly_fundamentals.
    shares_period_end_cum_adjのコメント参照)。「DWH値 ÷ shares_period_end_cum_adj
    = J-Quants値」であれば一致とみなす。残り60件(係数がNULL・1.0でも不一致・
    複数回の分割が重なり係数適用でも合わない等)は未解明で、不一致として報告する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal

import duckdb

CLEANSED_ROOT = os.environ.get("CLEANSED_ROOT", "/data/cleansed")
MART_ROOT = os.environ.get("MART_ROOT", "/data/mart")

EXACT_FIELDS = ("eps", "bps", "dividend_per_share")

_QUERY = """
with jq_all as (
    select
        left(code, 4) as jpx_code,
        doc_type,
        cur_per_en as period_end,
        sales as jq_sales,
        eps as jq_eps,
        bps as jq_bps,
        shares_outstanding as jq_shares_outstanding,
        dividend_per_share_annual as jq_dividend_per_share
    from read_parquet(?)
    where doc_type like '%FinancialStatements%'
),
jq as (
    select * from jq_all where doc_type not like '%REIT%'
),
dwh as (
    select
        jpx_code, period_end,
        sales as dwh_sales,
        eps as dwh_eps,
        bps as dwh_bps,
        shares_outstanding as dwh_shares_outstanding,
        shares_period_end_cum_adj,
        dividend_per_share as dwh_dividend_per_share
    from read_parquet(?)
    where is_preferred_actuals
)
select
    jq.jpx_code, jq.period_end, jq.doc_type,
    dwh.dwh_sales, jq.jq_sales,
    dwh.dwh_eps, jq.jq_eps,
    dwh.dwh_bps, jq.jq_bps,
    dwh.dwh_shares_outstanding, jq.jq_shares_outstanding, dwh.shares_period_end_cum_adj,
    dwh.dwh_dividend_per_share, jq.jq_dividend_per_share
from jq
left join dwh on jq.jpx_code = dwh.jpx_code and jq.period_end = dwh.period_end
order by jq.jpx_code, jq.period_end
"""

_REIT_COUNT_QUERY = """
select count(*) from read_parquet(?)
where doc_type like '%FinancialStatements%' and doc_type like '%REIT%'
"""


@dataclass
class FieldSummary:
    compared: int = 0
    match: int = 0
    mismatch: int = 0


@dataclass
class Mismatch:
    jpx_code: str
    period_end: object
    field: str
    dwh_value: object
    jq_value: object


@dataclass
class ComparisonResult:
    reit_skipped: int = 0
    field_summaries: dict[str, FieldSummary] = field(default_factory=dict)
    mismatches: list[Mismatch] = field(default_factory=list)


Numeric = int | float | Decimal


def sales_matches(dwh_v: Numeric | None, jq_v: Numeric | None) -> bool | None:
    """売上高は100万円単位切り捨ての差異を許容する(モジュールdocstring参照)。"""
    if dwh_v is None or jq_v is None:
        return None
    return float(dwh_v) // 1_000_000 * 1_000_000 == float(jq_v)


def exact_matches(dwh_v: Numeric | None, jq_v: Numeric | None) -> bool | None:
    if dwh_v is None or jq_v is None:
        return None
    return float(dwh_v) == float(jq_v)


def shares_matches(dwh_v: Numeric | None, jq_v: Numeric | None, adj: float | None) -> bool | None:
    """株式分割の権利確定日〜効力発生日の間に決算期末がある開示では、DWHは
    分割前の株数を保持する一方、J-Quantsは分割後の株数を遡及表示する
    (モジュールdocstring参照)。shares_period_end_cum_adjで除した値が一致すれば
    同一の構造的差異として一致とみなす(係数がNULL・1.0、または除しても
    一致しない場合は不一致のまま報告する)。
    """
    if dwh_v is None or jq_v is None:
        return None
    if float(dwh_v) == float(jq_v):
        return True
    if adj is None or adj == 0:
        return False
    return abs(float(dwh_v) / adj - float(jq_v)) < 1.0


_FIELD_CHECKS = {"sales": sales_matches} | {f: exact_matches for f in EXACT_FIELDS}


_ALL_FIELDS = (*_FIELD_CHECKS, "shares_outstanding")


def run_comparison(con: duckdb.DuckDBPyConnection) -> ComparisonResult:
    cleansed_path = f"{CLEANSED_ROOT}/jquants_fin_summary.parquet"
    mart_path = f"{MART_ROOT}/jpx_edinet_quarterly_fundamentals.parquet"

    result = ComparisonResult(field_summaries={f: FieldSummary() for f in _ALL_FIELDS})
    result.reit_skipped = con.execute(_REIT_COUNT_QUERY, [cleansed_path]).fetchone()[0]  # type: ignore[index]

    query_result = con.execute(_QUERY, [cleansed_path, mart_path])
    cols = [c[0] for c in query_result.description]
    rows = [dict(zip(cols, r, strict=True)) for r in query_result.fetchall()]

    for row in rows:
        for field_name in _ALL_FIELDS:
            dwh_v = row[f"dwh_{field_name}"]
            jq_v = row[f"jq_{field_name}"]
            if field_name == "shares_outstanding":
                is_match = shares_matches(dwh_v, jq_v, row["shares_period_end_cum_adj"])
            else:
                is_match = _FIELD_CHECKS[field_name](dwh_v, jq_v)
            if is_match is None:
                continue
            summary = result.field_summaries[field_name]
            summary.compared += 1
            if is_match:
                summary.match += 1
            else:
                summary.mismatch += 1
                result.mismatches.append(
                    Mismatch(
                        jpx_code=row["jpx_code"],
                        period_end=row["period_end"],
                        field=field_name,
                        dwh_value=dwh_v,
                        jq_value=jq_v,
                    )
                )
    return result


def print_report(result: ComparisonResult) -> None:
    print(f"REIT(対象外、件数のみ): {result.reit_skipped}件")
    print()
    print("項目\t比較件数\t一致\t不一致")
    for field_name, summary in result.field_summaries.items():
        print(f"{field_name}\t{summary.compared}\t{summary.match}\t{summary.mismatch}")

    if result.mismatches:
        print()
        print("不一致の詳細:")
        for m in result.mismatches:
            print(f"  {m.jpx_code} {m.period_end} {m.field}: DWH={m.dwh_value} J-Quants={m.jq_value}")


def main() -> None:
    con = duckdb.connect()
    result = run_comparison(con)
    print_report(result)


if __name__ == "__main__":
    main()
