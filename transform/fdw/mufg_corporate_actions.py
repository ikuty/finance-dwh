"""三菱UFJ eスマート証券(kabu.com)の株式分割・株式併合・商号変更ページ(生HTML)から
構造化された行データを取り出す。

`jpx_stq_pdf.py`/`jpx_stq_facts.py`のStage1/Stage2分離とは異なり、この3ページは
座標情報を持たないシンプルな<table>のため、HTML解析(BeautifulSoup)から構造化行
までを1段で行う。列構成が3ページで異なるため(株式分割7列・株式併合5列・商号変更
4列)、それぞれ独立したdataclass・パース関数を持つ。

日付・比率とも生文字列のまま保持し(landing層は生データに忠実、型付け・比率の
分割はcleansed層のdbtモデルの責務)、ここでは一切加工しない。

実機確認(2026-09-16)した実際のページ構造:
    - 株式分割(bunkatu.html): 割当日/銘柄コード/銘柄名/割当比率/権利付最終日/
      効力発生日/売却可能予定日。末尾に割当日が"－"(欠損)の行が1件、時系列を
      外れて出現することを確認済み。欠損値は空文字列("－")のまま保持し、
      cleansed側でtry_castにより自然にNULL化させる(EDINET/JPXの数値カラムと
      同じ設計)。
    - 株式併合(gensi.html): 効力発生日/銘柄コード/銘柄名/併合比率/権利付最終日。
    - 商号変更(syougou_henkou.html): 変更日/銘柄コード/旧商号/新商号。
"""
from __future__ import annotations

from dataclasses import dataclass, fields

from bs4 import BeautifulSoup


def _table_rows(html: str) -> list[list[str]]:
    """<main>内の<table>から、ヘッダー行(先頭の<tr>)を除く各行のセルテキストの
    リストを返す。<table>が見つからなければ空リストを返す。"""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    table = main.find("table") if main is not None else soup.find("table")
    if table is None:
        return []

    trs = table.find_all("tr")
    rows: list[list[str]] = []
    for tr in trs[1:]:  # 先頭行はヘッダー
        cells = [cell.get_text(strip=True) for cell in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)
    return rows


@dataclass
class StockSplitRow:
    allotment_date: str
    code: str
    name: str
    ratio: str
    last_cum_rights_date: str
    effective_date: str
    sellable_date: str


STOCK_SPLIT_COLUMNS = [f.name for f in fields(StockSplitRow)]


def parse_stock_splits(html: str) -> list[StockSplitRow]:
    """株式分割ページ(bunkatu.html)をパースする。7列: 割当日/銘柄コード/銘柄名/
    割当比率/権利付最終日/効力発生日/売却可能予定日。"""
    return [StockSplitRow(*row[:7]) for row in _table_rows(html) if len(row) >= 7]


@dataclass
class StockConsolidationRow:
    effective_date: str
    code: str
    name: str
    ratio: str
    last_cum_rights_date: str


STOCK_CONSOLIDATION_COLUMNS = [f.name for f in fields(StockConsolidationRow)]


def parse_stock_consolidations(html: str) -> list[StockConsolidationRow]:
    """株式併合ページ(gensi.html)をパースする。5列: 効力発生日/銘柄コード/銘柄名/
    併合比率/権利付最終日。"""
    return [StockConsolidationRow(*row[:5]) for row in _table_rows(html) if len(row) >= 5]


@dataclass
class CompanyNameChangeRow:
    change_date: str
    code: str
    old_name: str
    new_name: str


COMPANY_NAME_CHANGE_COLUMNS = [f.name for f in fields(CompanyNameChangeRow)]


def parse_company_name_changes(html: str) -> list[CompanyNameChangeRow]:
    """商号変更ページ(syougou_henkou.html)をパースする。4列: 変更日/銘柄コード/
    旧商号/新商号。"""
    return [CompanyNameChangeRow(*row[:4]) for row in _table_rows(html) if len(row) >= 4]
