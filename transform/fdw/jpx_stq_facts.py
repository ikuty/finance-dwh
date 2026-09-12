"""JPX形式C(株式相場表・詳細日次)の座標付き単語データ(`jpx_stq_pdf.iter_words`の
出力)から、セクション1(立会市場普通取引)の銘柄別明細行を構造化する。

Stage1(`jpx_stq_pdf.py`)の後段。ビジネスロジック(セクション境界の判定・列の
意味づけ・見出し行の継承等)はすべてここに閉じ込める。今後何度も変わる想定の
層であり、Stage1(PDF→座標付き単語データ、実測 約36秒/日)を経由し直さずに
このロジックだけを直せるように分離している。

列の並び順は単純なテキスト抽出では保持されない(PDF生成時の描画順序が視覚上の
列順と一致しない)ことを実機データで確認済みのため、単語の座標(x0)を用いた
列への割り当てを行う。列境界はセクション1のヘッダー行の実測座標から算出した
定数(`_COLUMN_BOUNDS`)を用いる。

見出し行(市場区分・業種)はフォントサイズ(10pt)で本文(6pt)と判別する。判定の
過程で「繰り返し列ヘッダー行」「日付行」「表題」「取引種別見出し」等、見出し
"のように見えるが違う行"を除外する必要があった(実機データで発見・修正した
既知の落とし穴。詳細はdocs/raw_landing_design.md参照)。

詳細設計・実機での複数日検証結果はdocs/raw_landing_design.md参照。
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Sequence
from dataclasses import dataclass, fields

from jpx_stq_pdf import Word

_HEADING_SIZE_THRESHOLD = 8.0

# 列境界(x0の下限)。セクション1データ行の実測x0の中点から算出した定数。
# [(閾値, 列名), ...] 昇順。bisect.bisect_right で「その閾値未満の列」を選ぶ。
_COLUMN_BOUNDS: list[tuple[float, str]] = [
    (107.35, "code"),
    (228.45, "unit_name"),
    (346.2, "am_open"),
    (406.2, "am_high"),
    (466.2, "am_low"),
    (526.2, "am_close"),
    (586.2, "pm_open"),
    (646.2, "pm_high"),
    (706.2, "pm_low"),
    (778.6, "pm_close"),
    (849.0, "final_special_quote"),
    (900.3, "net_change"),
    (971.8, "vwap"),
    (1050.0, "trading_volume"),
    (float("inf"), "trading_value"),
]
_BOUND_VALUES = [b[0] for b in _COLUMN_BOUNDS]

_UNIT_NAME_RE = re.compile(r"^(\d+)(.*)$")
# 銘柄コード: 4〜5桁数字、または末尾に英字1文字(REIT・投資証券の投資口コード。例:"256A")。
_CODE_RE = re.compile(r"^[0-9]{3,5}[A-Za-z0-9]?$")
_ASCII_RE = re.compile(r"[A-Za-z,.&\s]+")
_DATE_RE = re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日")
_SECTION_ID_RE = re.compile(r"^(\d+)-")

# 構造上の見出し(市場区分・業種ではない)。市場区分/業種の判定から除外する。
_STRUCTURAL_HEADINGS = {
    "内国株式", "単一銘柄取引", "外国株式", "外国投信等", "内国株式優先株等",
    "外国投信受益証券", "Domestic Stock", "Single Issue Trading",
}

# 各ページに繰り返し出現する列見出しラベル(単語単位)。行の全単語がこれに
# 含まれる場合は見出し(市場区分/業種)ではなく列ヘッダーとして無視する。
_COLUMN_LABEL_WORDS = {
    "コード", "銘柄名", "始値", "高値", "安値", "終値", "最終気配", "前日比",
    "売買高加重", "平均価格", "売買高", "売買代金", "単位", "売買",
    "午前", "午後", "(Themorningtradingsession)", "(Theafternoontradingsession)",
}

# 市場区分は既知の値のみを許可する(「立会市場普通取引」等の取引種別見出しにも
# "市場"が部分文字列として含まれ誤爆するため、部分一致ではなく列挙で判定する)。
_MARKET_SEGMENTS = {"プライム市場", "スタンダード市場", "グロース市場", "TOKYOPROMarket銘柄"}


@dataclass
class FactRow:
    """landing.jpx_stq_facts の1行(1銘柄×1日)。全列 str|None(型付けはcleansed層)。"""

    file_date: str
    code: str
    trading_unit: str | None
    name_ja: str | None
    name_en: str | None
    market_segment: str | None
    industry_sector_ja: str | None
    industry_sector_en: str | None
    am_open: str | None
    am_high: str | None
    am_low: str | None
    am_close: str | None
    pm_open: str | None
    pm_high: str | None
    pm_low: str | None
    pm_close: str | None
    final_special_quote: str | None
    net_change: str | None
    vwap: str | None
    trading_volume: str | None
    trading_value: str | None


FACT_COLUMNS = [f.name for f in fields(FactRow)]


def _column_for_x0(x0: float) -> str:
    idx = bisect.bisect_right(_BOUND_VALUES, x0)
    return _COLUMN_BOUNDS[idx][1]


def find_section1_pages(words: Sequence[Word]) -> set[int]:
    """各ページ右上の"N-M..."番号からセクション1(立会市場普通取引)のページを特定する。"""
    pages: set[int] = set()
    for w in words:
        if w.x0 > 1000 and w.top < 90:
            m = _SECTION_ID_RE.match(w.text)
            if m and m.group(1) == "1":
                pages.add(w.page)
    return pages


def _classify_heading(
    row_words: list[Word],
    market_segment: str | None,
    industry_ja: str | None,
    industry_en: str | None,
) -> tuple[str | None, str | None, str | None]:
    if all(w.text in _COLUMN_LABEL_WORDS for w in row_words):
        return market_segment, industry_ja, industry_en  # 繰り返し列ヘッダー行

    full_text = "".join(w.text for w in row_words if w.x0 < 700)
    if (
        not full_text
        or _DATE_RE.match(full_text)
        or full_text == "株式相場表"
        or full_text.endswith("取引")
        or full_text in _STRUCTURAL_HEADINGS
    ):
        return market_segment, industry_ja, industry_en  # 日付行・表題・取引種別見出し等

    if full_text in _MARKET_SEGMENTS:
        return full_text, industry_ja, industry_en

    ja = "".join(w.text for w in row_words if not _ASCII_RE.fullmatch(w.text)) or None
    en = "".join(w.text for w in row_words if _ASCII_RE.fullmatch(w.text)) or None
    if ja is None:
        # 日本語が無い(英語見出し単独の行、または未知のパターン)。
        # 業種の日英は同一行で揃って出現するため、日本語が無ければ状態を変えない。
        return market_segment, industry_ja, industry_en
    return market_segment, ja, en


def build_records(words: Sequence[Word], section1_pages: set[int], file_date: str) -> list[FactRow]:
    """セクション1の単語データを、銘柄×file_dateの1行1レコードへ構造化する。"""
    by_page: dict[int, list[Word]] = {}
    for w in words:
        if w.page in section1_pages:
            by_page.setdefault(w.page, []).append(w)

    records: list[FactRow] = []
    market_segment: str | None = None
    industry_ja: str | None = None
    industry_en: str | None = None
    last_record: FactRow | None = None

    for page in sorted(by_page):
        page_words = sorted(by_page[page], key=lambda w: (w.top, w.x0))
        rows: dict[int, list[Word]] = {}
        for w in page_words:
            rows.setdefault(round(w.top), []).append(w)

        for top in sorted(rows):
            row_words = sorted(rows[top], key=lambda w: w.x0)

            if any(w.size >= _HEADING_SIZE_THRESHOLD for w in row_words):
                market_segment, industry_ja, industry_en = _classify_heading(
                    row_words, market_segment, industry_ja, industry_en
                )
                continue

            code_word = next((w for w in row_words if _column_for_x0(w.x0) == "code"), None)
            if code_word is not None and _CODE_RE.match(code_word.text):
                cells: dict[str, str | None] = {}
                for w in row_words:
                    col = _column_for_x0(w.x0)
                    if col == "code":
                        continue
                    if col == "unit_name":
                        m = _UNIT_NAME_RE.match(w.text)
                        if m:
                            cells["trading_unit"] = m.group(1)
                            cells["name_ja"] = m.group(2)
                        else:
                            cells["trading_unit"] = None
                            cells["name_ja"] = w.text
                    else:
                        cells[col] = w.text
                record = FactRow(
                    file_date=file_date,
                    code=code_word.text,
                    trading_unit=cells.get("trading_unit"),
                    name_ja=cells.get("name_ja"),
                    name_en=None,
                    market_segment=market_segment,
                    industry_sector_ja=industry_ja,
                    industry_sector_en=industry_en,
                    am_open=cells.get("am_open"),
                    am_high=cells.get("am_high"),
                    am_low=cells.get("am_low"),
                    am_close=cells.get("am_close"),
                    pm_open=cells.get("pm_open"),
                    pm_high=cells.get("pm_high"),
                    pm_low=cells.get("pm_low"),
                    pm_close=cells.get("pm_close"),
                    final_special_quote=cells.get("final_special_quote"),
                    net_change=cells.get("net_change"),
                    vwap=cells.get("vwap"),
                    trading_volume=cells.get("trading_volume"),
                    trading_value=cells.get("trading_value"),
                )
                records.append(record)
                last_record = record
            else:
                # 継続行(英語社名の可能性)。直前レコードにまだ name_en が無ければ結合する。
                text = "".join(w.text for w in row_words)
                if last_record is not None and last_record.name_en is None and text:
                    last_record.name_en = text

    return records


def run(words: Sequence[Word], file_date: str) -> list[FactRow]:
    """セクション1の特定→構造化までを一括で行う便利関数。"""
    section1_pages = find_section1_pages(words)
    return build_records(words, section1_pages, file_date)
