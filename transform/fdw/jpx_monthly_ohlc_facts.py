"""JPX形式B(株式相場表・月次簡易OHLC)の座標付き単語データ(`jpx_stq_pdf.iter_words`の
出力)から、銘柄×日付の明細行を構造化する。

Stage1(`jpx_stq_pdf.py`、PDF形式に依存しない汎用実装のためそのまま再利用)の後段。
形式C(`jpx_stq_facts.py`)と異なり、この形式は市場区分・業種の見出しが一切無い
単一のフラットな表（1行=1銘柄×1日、コード順に列挙）で、日付は各行に明示的に
含まれる（`約定年月日`列、YYYYMMDD）。1ファイル=1ヶ月ぶんの全営業日を含む。

単純なテキスト抽出でも形式Cほど深刻な列の入れ替わりは起きないが、**数値が
途中でスペース混入により破損する**不具合を実機データで確認したため（例:
"4785" が "478 5" に分断される）、形式Cと同じく座標(x0)ベースの列割り当てが
必要と判断した。

列境界はヘッダー行の実測x0の中点から算出した定数（`_COLUMN_BOUNDS`）。
ヘッダー行自体も本文と同じフォントサイズ(6.36pt)のため、形式Cのような
フォントサイズでの見出し判定はできず、「行の全単語が既知のヘッダーラベル
文字列と一致するか」で判定する。

**OHLC8列はx0ではなくx1(右端)で列判定する**（実機データで発見した重要な
落とし穴）。数値は右揃えのため、桁数が少ない値（低位株の"20"等）はセルの
右端に寄り、x0が隣の列との境界を越えてしまう（実測: "4710"のx0=317.7だが
"20"のx0=324.8で、両者とも同じam_open列のはずが単純な中点判定では別列に
誤分類される）。x1（右端）は桁数によらず列ごとに完全に固定される（実測:
"4710"も"20"も同じam_open列ではx1=331.9で一致）ため、x1ベースで判定する。
date/code/name列は左揃えのためx0のままでよい。

銘柄名称欄は「名称」+「株式種別（普通株式/優先株式等）」の2トークンが典型だが、
"株式会社伊藤園第１種優先株式"のように名前と種別が渾然一体で1トークンになる
銘柄もあり、機械的な分離は安全にできないと判断した。`name_ja`は名称欄の
全トークンをそのまま結合したものとする（分離しない）。

詳細設計はdocs/raw_landing_design.md参照。
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Sequence
from dataclasses import dataclass, fields

from jpx_stq_pdf import Word

# date/code/name は左揃えなので x0 の下限で判定する。
# [(閾値, 列名), ...] 昇順。bisect.bisect_right で「その閾値未満の列」を選ぶ。
_LEFT_ALIGNED_BOUNDS: list[tuple[float, str]] = [
    (75.7, "date"),
    (112.05, "code"),
    (300.0, "name"),
]
_LEFT_BOUND_VALUES = [b[0] for b in _LEFT_ALIGNED_BOUNDS]

# OHLC8列は右揃えなので x1(右端) の下限で判定する（実測、桁数によらず固定）。
_NUMERIC_BOUNDS: list[tuple[float, str]] = [
    (346.75, "am_open"),
    (376.5, "am_high"),
    (406.25, "am_low"),
    (436.0, "am_close"),
    (465.8, "pm_open"),
    (495.55, "pm_high"),
    (525.3, "pm_low"),
    (float("inf"), "pm_close"),
]
_NUMERIC_BOUND_VALUES = [b[0] for b in _NUMERIC_BOUNDS]

# 銘柄コード: 4〜5桁の英数字混在(2024年1月導入のJPX新証券コード制度により、
# 従来の4桁数字に加えて"130A0"のような数字+英字混在の5桁コードが実機データで
# 258種確認できた。英字の位置は末尾とは限らない)。数字を1文字以上含むことのみ要求する。
_CODE_RE = re.compile(r"^(?=.*[0-9])[0-9A-Za-z]{4,5}$")
_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

_HEADER_LABELS = {
    "約定年月日", "銘柄コード", "銘柄名称",
    "前場始値", "前場高値", "前場安値", "前場終値",
    "後場始値", "後場高値", "後場安値", "後場終値",
}


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


def _column_for_word(w: Word) -> str | None:
    if w.x0 < _LEFT_BOUND_VALUES[-1]:
        idx = bisect.bisect_right(_LEFT_BOUND_VALUES, w.x0)
        return _LEFT_ALIGNED_BOUNDS[idx][1]
    idx = bisect.bisect_right(_NUMERIC_BOUND_VALUES, w.x1)
    if idx >= len(_NUMERIC_BOUNDS):
        return None
    return _NUMERIC_BOUNDS[idx][1]


def _to_file_date(raw: str) -> str | None:
    m = _DATE_RE.match(raw)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def build_records(words: Sequence[Word]) -> list[FactRow]:
    """全ページの単語データを、銘柄×日付の1行1レコードへ構造化する。"""
    by_page: dict[int, list[Word]] = {}
    for w in words:
        by_page.setdefault(w.page, []).append(w)

    records: list[FactRow] = []

    for page in sorted(by_page):
        page_words = sorted(by_page[page], key=lambda w: (w.top, w.x0))
        rows: dict[int, list[Word]] = {}
        for w in page_words:
            rows.setdefault(round(w.top), []).append(w)

        for top in sorted(rows):
            row_words = sorted(rows[top], key=lambda w: w.x0)

            if all(w.text in _HEADER_LABELS for w in row_words):
                continue  # 繰り返しヘッダー行

            date_word = next((w for w in row_words if _column_for_word(w) == "date"), None)
            code_word = next((w for w in row_words if _column_for_word(w) == "code"), None)
            if date_word is None or code_word is None:
                continue  # 認識できない行(該当なし)は破棄
            file_date = _to_file_date(date_word.text)
            if file_date is None or not _CODE_RE.match(code_word.text):
                continue

            cells: dict[str, list[str]] = {}
            for w in row_words:
                col = _column_for_word(w)
                if col is None or col in ("date", "code"):
                    continue
                cells.setdefault(col, []).append(w.text)

            def cell(col: str, sep: str = "") -> str | None:
                parts = cells.get(col)
                return sep.join(parts) if parts else None

            records.append(
                FactRow(
                    file_date=file_date,
                    code=code_word.text,
                    name_ja=cell("name", sep="　"),
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
