"""mufg_corporate_actions.py の単体テスト。

kabu.comの利用規約により実際に取得したHTMLはコミットできないため、実機確認した
列構成・タグ構造に基づく手打ちの合成HTMLでテストする（JPX PDFと同じ方針）。
"""

from __future__ import annotations

import mufg_corporate_actions as m


def _wrap_table(header_cells: list[str], rows: list[list[str]]) -> str:
    header_html = "".join(f"<th>{c}</th>" for c in header_cells)
    rows_html = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
    )
    return f"""<!doctype html>
<html><body>
<main>
<table>
<tr>{header_html}</tr>
{rows_html}
</table>
</main>
</body></html>"""


# --- parse_stock_splits ---------------------------------------------------------


def test_parse_stock_splits_reads_all_seven_columns() -> None:
    html = _wrap_table(
        ["割当日", "銘柄コード", "銘柄名", "割当比率", "権利付最終日", "効力発生日", "売却可能予定日"],
        [["2026/09/30", "6465", "ホシザキ", "1：2", "2026/09/28", "2026/10/01", "2026/09/29"]],
    )
    rows = m.parse_stock_splits(html)
    assert rows == [
        m.StockSplitRow(
            allotment_date="2026/09/30",
            code="6465",
            name="ホシザキ",
            ratio="1：2",
            last_cum_rights_date="2026/09/28",
            effective_date="2026/10/01",
            sellable_date="2026/09/29",
        )
    ]


def test_parse_stock_splits_keeps_missing_allotment_date_as_raw_dash() -> None:
    # 実機確認: 末尾に割当日が"－"(欠損)の行が時系列を外れて出現することがある。
    # 生データに忠実な方針により、ここでは変換せずそのまま保持する。
    html = _wrap_table(
        ["割当日", "銘柄コード", "銘柄名", "割当比率", "権利付最終日", "効力発生日", "売却可能予定日"],
        [["－", "8686", "Ａ　Ｆ　Ｌ", "1：2", "2018/03/15", "－", "2018/03/16"]],
    )
    rows = m.parse_stock_splits(html)
    assert rows[0].allotment_date == "－"
    assert rows[0].effective_date == "－"


def test_parse_stock_splits_skips_short_rows() -> None:
    html = _wrap_table(
        ["割当日", "銘柄コード", "銘柄名", "割当比率", "権利付最終日", "効力発生日", "売却可能予定日"],
        [["2026/09/30", "6465"]],  # 列不足の行は無視する
    )
    assert m.parse_stock_splits(html) == []


def test_parse_stock_splits_empty_when_no_table() -> None:
    assert m.parse_stock_splits("<html><body><main>no table here</main></body></html>") == []


# --- parse_stock_consolidations --------------------------------------------------


def test_parse_stock_consolidations_reads_all_five_columns() -> None:
    html = _wrap_table(
        ["効力発生日", "銘柄コード", "銘柄名", "併合比率", "権利付最終日"],
        [["2026/11/01", "6574", "コンヴァノ", "10株→1株", "2026/10/28"]],
    )
    rows = m.parse_stock_consolidations(html)
    assert rows == [
        m.StockConsolidationRow(
            effective_date="2026/11/01",
            code="6574",
            name="コンヴァノ",
            ratio="10株→1株",
            last_cum_rights_date="2026/10/28",
        )
    ]


def test_parse_stock_consolidations_multiple_rows() -> None:
    html = _wrap_table(
        ["効力発生日", "銘柄コード", "銘柄名", "併合比率", "権利付最終日"],
        [
            ["2026/11/01", "6574", "コンヴァノ", "10株→1株", "2026/10/28"],
            ["2026/10/22", "1366", "ｉＦ２２５Ｗベ", "100株→1株", "2026/10/19"],
        ],
    )
    assert len(m.parse_stock_consolidations(html)) == 2


# --- parse_company_name_changes --------------------------------------------------


def test_parse_company_name_changes_reads_all_four_columns() -> None:
    html = _wrap_table(
        ["変更日", "銘柄コード", "旧商号", "新商号"],
        [["2027/07/01", "2904", "一正蒲鉾", "一正ホールディングス"]],
    )
    rows = m.parse_company_name_changes(html)
    assert rows == [
        m.CompanyNameChangeRow(
            change_date="2027/07/01",
            code="2904",
            old_name="一正蒲鉾",
            new_name="一正ホールディングス",
        )
    ]


def test_parse_company_name_changes_empty_when_no_table() -> None:
    assert m.parse_company_name_changes("<html><body></body></html>") == []
