"""JPX形式B(株式相場表・月次簡易OHLC)の座標付き単語データ(`jpx_stq_pdf.iter_words`の
出力)から、銘柄×日付の明細行を構造化する。

Stage1(`jpx_stq_pdf.py`、PDF形式に依存しない汎用実装のためそのまま再利用)の後段。
形式C(`jpx_stq_facts.py`)と異なり、この形式は市場区分・業種の見出しが一切無い
単一のフラットな表（1行=1銘柄×1日、コード順に列挙）で、日付は各行に明示的に
含まれる（`約定年月日`列、YYYYMMDD）。1ファイル=1ヶ月ぶんの全営業日を含む。

**列境界は固定座標定数ではなく、各月PDFの先頭ページのヘッダー行から動的に
算出する**（2026-09-13、実機データで判明した重要な設計変更）。PDF生成
ソフトウェアが2022年12月→2023年1月でAntennaHouse→iTextに切り替わる際、
列の座標が全く別物になるだけでなく、**AntennaHouse世代内でも月によって
座標が変動する**ことを実データ（2020-01と2022-12で前場始値のx1が
244.4/321.7と別の値）で確認した。固定座標定数では対応できない。

ヘッダーラベル文字列も世代で異なる（"年月日"/"コード"/"銘柄名" vs
"約定年月日"/"銘柄コード"/"銘柄名称"）ため、両方のバリエーションを許容する。

AntennaHouse世代は銘柄コードと銘柄名称が**スペース無しで1つのトークンに
結合**される（例: "13010極洋"）。コードは4桁の実コードの末尾に枝番1桁が
付いた5桁表記（普通株式は"0"、優先株式等は別の数字。実データで"25935"
＝株式会社伊藤園の優先株式を確認、末尾0以外の枝番もあるため単純な末尾0
除去による正規化はしない。生データのまま保持する）。「数字が続いたあと
非ASCII文字（日本語）が来る」ことを目印に分離する。新証券コード制度
（2024年1月導入）の英数字混在コード（例: "130A0"）は数字の後に半角
アルファベットが続くため、この判定に引っかからず正しく1トークンのまま
コードとして扱われる。

詳細設計はdocs/raw_landing_design.md参照。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, fields

from jpx_stq_pdf import Word

_DATE_LABELS = {"年月日", "約定年月日"}
_CODE_LABELS = {"コード", "銘柄コード"}
_NAME_LABELS = {"銘柄名", "銘柄名称"}
_OHLC_LABELS_ORDER = [
    "前場始値", "前場高値", "前場安値", "前場終値",
    "後場始値", "後場高値", "後場安値", "後場終値",
]
_OHLC_FIELD_NAMES = [
    "am_open", "am_high", "am_low", "am_close",
    "pm_open", "pm_high", "pm_low", "pm_close",
]
_ALL_HEADER_LABELS = _DATE_LABELS | _CODE_LABELS | _NAME_LABELS | set(_OHLC_LABELS_ORDER)

_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
# 数字が続いたあと非ASCII文字(日本語)が来る場合のみ分離する。新証券コード制度の
# 英数字混在コード(例:"130A0")は数字の後に半角英字が続くため対象外(意図的)。
_CODE_NAME_SPLIT_RE = re.compile(r"^(\d+)([^\x00-\x7F].*)$")


@dataclass
class FactRow:
    """landing.jpx_monthly_ohlc_facts の1行(1銘柄×1日)。全列 str|None(型付けはcleansed層)。"""

    file_date: str
    code: str
    name_ja: str | None
    am_open: str | None
    am_high: str | None
    am_low: str | None
    am_close: str | None
    pm_open: str | None
    pm_high: str | None
    pm_low: str | None
    pm_close: str | None


FACT_COLUMNS = [f.name for f in fields(FactRow)]


@dataclass
class _HeaderLayout:
    """1PDFにつき1回、先頭で検出したヘッダー行から算出する列境界。"""

    date_code_boundary: float  # date列とcode/name領域の境界(中点)
    name_area_end: float  # code/name領域の右端(前場始値ラベルのx0)
    ohlc_bounds: list[float]  # 8列分の右端境界(昇順、最後はinf)


def _detect_header(row_words: list[Word]) -> _HeaderLayout | None:
    by_text: dict[str, Word] = {}
    for w in row_words:
        by_text.setdefault(w.text, w)

    date_w = next((by_text[t] for t in _DATE_LABELS if t in by_text), None)
    code_w = next((by_text[t] for t in _CODE_LABELS if t in by_text), None)
    if date_w is None or code_w is None:
        return None
    ohlc_words = [by_text.get(label) for label in _OHLC_LABELS_ORDER]
    if any(w is None for w in ohlc_words):
        return None
    x1s = [w.x1 for w in ohlc_words if w is not None]
    if x1s != sorted(x1s):
        return None  # 想定外の並び順(安全側に倒して見送る)

    bounds = [(x1s[i] + x1s[i + 1]) / 2 for i in range(len(x1s) - 1)]
    bounds.append(float("inf"))

    first_ohlc_word = ohlc_words[0]
    assert first_ohlc_word is not None
    return _HeaderLayout(
        date_code_boundary=(date_w.x0 + code_w.x0) / 2,
        name_area_end=first_ohlc_word.x0,
        ohlc_bounds=bounds,
    )


def _is_header_fragment(row_words: list[Word]) -> bool:
    return any(w.text in _ALL_HEADER_LABELS for w in row_words)


def _ohlc_field_for_x1(layout: _HeaderLayout, x1: float) -> str | None:
    for bound, field in zip(layout.ohlc_bounds, _OHLC_FIELD_NAMES, strict=True):
        if x1 < bound:
            return field
    return None


def _split_code_name(token: str) -> tuple[str, str | None]:
    m = _CODE_NAME_SPLIT_RE.match(token)
    if m:
        return m.group(1), m.group(2)
    return token, None


def _to_file_date(raw: str) -> str | None:
    m = _DATE_RE.match(raw)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def build_records(words: Sequence[Word]) -> list[FactRow]:
    """全ページの単語データを、銘柄×日付の1行1レコードへ構造化する。

    先頭で見つかったヘッダー行から列境界を算出し、以降のページでも
    (通常は同一の)ヘッダー行が見つかるたびに更新する。
    """
    by_page: dict[int, list[Word]] = {}
    for w in words:
        by_page.setdefault(w.page, []).append(w)

    records: list[FactRow] = []
    layout: _HeaderLayout | None = None

    for page in sorted(by_page):
        page_words = sorted(by_page[page], key=lambda w: (w.top, w.x0))
        rows: dict[int, list[Word]] = {}
        for w in page_words:
            rows.setdefault(round(w.top), []).append(w)

        for top in sorted(rows):
            row_words = sorted(rows[top], key=lambda w: w.x0)

            detected = _detect_header(row_words)
            if detected is not None:
                layout = detected
                continue
            if _is_header_fragment(row_words):
                continue  # ヘッダーの断片(検出しきれなかった行)も無視
            if layout is None:
                continue  # まだヘッダー未検出(想定外、安全側に倒して破棄)

            date_word = next((w for w in row_words if w.x0 < layout.date_code_boundary), None)
            code_name_words = [
                w for w in row_words if layout.date_code_boundary <= w.x0 < layout.name_area_end
            ]
            if date_word is None or not code_name_words:
                continue
            file_date = _to_file_date(date_word.text)
            if file_date is None:
                continue

            code, name_head = _split_code_name(code_name_words[0].text)
            name_rest = [w.text for w in code_name_words[1:]]
            name_parts = ([name_head] if name_head else []) + name_rest
            name_ja = "　".join(p for p in name_parts if p) or None

            cells: dict[str, list[str]] = {}
            for w in row_words:
                if w is date_word or w in code_name_words:
                    continue
                field = _ohlc_field_for_x1(layout, w.x1)
                if field is not None:
                    cells.setdefault(field, []).append(w.text)

            def cell(field: str) -> str | None:
                parts = cells.get(field)
                return "".join(parts) if parts else None

            records.append(
                FactRow(
                    file_date=file_date,
                    code=code,
                    name_ja=name_ja,
                    am_open=cell("am_open"),
                    am_high=cell("am_high"),
                    am_low=cell("am_low"),
                    am_close=cell("am_close"),
                    pm_open=cell("pm_open"),
                    pm_high=cell("pm_high"),
                    pm_low=cell("pm_low"),
                    pm_close=cell("pm_close"),
                )
            )

    return records
