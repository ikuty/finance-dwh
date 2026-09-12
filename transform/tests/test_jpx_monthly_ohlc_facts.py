"""jpx_monthly_ohlc_facts.py の単体テスト。

実際のJPX PDFは利用規約上コミットできないため、`jpx_stq_pdf.Word`を直接
組み立ててテストする。座標値は実機データ(2025-09分の形式B月次PDF)で実測
した値を使用している。

実測の落とし穴（テストで固定する挙動）: OHLC8列は右揃えのため、桁数の
少ない値（低位株の"20"等）はx0が隣の列との境界を越える。x1(右端)は
桁数によらず列ごとに固定されるため、x1で列判定する必要がある
（`test_build_records_low_price_stock_x1_based_column_assignment`）。
"""

from __future__ import annotations

import jpx_monthly_ohlc_facts as m
from jpx_stq_pdf import Word

HEADER_ROW_TOP = 46.0


def header_row(page: int = 1) -> list[Word]:
    labels = [
        (52.7, "約定年月日"), (90.0, "銘柄コード"), (127.1, "銘柄名称"),
        (306.4, "前場始値"), (336.2, "前場高値"), (365.9, "前場安値"), (395.7, "前場終値"),
        (425.5, "後場始値"), (455.2, "後場高値"), (485.0, "後場安値"), (514.7, "後場終値"),
    ]
    return [Word(page=page, top=HEADER_ROW_TOP, x0=x0, x1=x0 + 20, size=6.36, text=t) for x0, t in labels]


def data_row(
    page: int, top: float, date: str, code: str, name_tokens: list[tuple[float, float, str]],
    values: list[tuple[float, float, str]],
) -> list[Word]:
    """values は8個: am_open/high/low/close, pm_open/high/low/close の (x0, x1, text)。"""
    words = [
        Word(page=page, top=top, x0=54.4, x1=82.7, size=6.36, text=date),
        Word(page=page, top=top, x0=97.0, x1=114.7, size=6.36, text=code),
    ]
    words += [Word(page=page, top=top, x0=x0, x1=x1, size=6.36, text=t) for x0, x1, t in name_tokens]
    words += [Word(page=page, top=top, x0=x0, x1=x1, size=6.36, text=t) for x0, x1, t in values]
    return words


# 実測(1301極洋、2025-09-01)の座標
KYOKUYO_VALUES = [
    (317.7, 331.9, "4710"), (347.5, 361.6, "4790"), (377.2, 391.4, "4685"), (407.0, 421.1, "4760"),
    (436.7, 450.9, "4760"), (466.5, 480.7, "4785"), (496.3, 510.4, "4735"), (526.0, 540.2, "4785"),
]
# 実測(67400ジャパンディスプレイ、低位株、2025-09-01)の座標
JDI_VALUES = [
    (324.8, 331.9, "20"), (354.5, 361.6, "20"), (384.3, 391.4, "19"), (414.1, 421.1, "20"),
    (443.8, 450.9, "19"), (473.6, 480.7, "20"), (503.3, 510.4, "19"), (533.1, 540.2, "20"),
]


def test_build_records_extracts_all_columns() -> None:
    words = data_row(
        1, 56.0, "20250901", "13010",
        [(127.1, 139.8, "極洋"), (146.2, 171.6, "普通株式")],
        KYOKUYO_VALUES,
    )
    records = m.build_records(words)
    assert len(records) == 1
    r = records[0]
    assert r.file_date == "2025-09-01"
    assert r.code == "13010"
    assert r.name_ja == "極洋　普通株式"
    assert (r.am_open, r.am_high, r.am_low, r.am_close) == ("4710", "4790", "4685", "4760")
    assert (r.pm_open, r.pm_high, r.pm_low, r.pm_close) == ("4760", "4785", "4735", "4785")


def test_build_records_low_price_stock_x1_based_column_assignment() -> None:
    """x0だけで判定すると"20"がam_high等の隣列に誤分類される実機バグの回帰テスト。"""
    words = data_row(
        1, 56.0, "20250901", "67400",
        [(127.1, 190.0, "ジャパンディスプレイ"), (196.3, 221.8, "普通株式")],
        JDI_VALUES,
    )
    records = m.build_records(words)
    assert len(records) == 1
    r = records[0]
    assert (r.am_open, r.am_high, r.am_low, r.am_close) == ("20", "20", "19", "20")
    assert (r.pm_open, r.pm_high, r.pm_low, r.pm_close) == ("19", "20", "19", "20")


def test_build_records_ignores_repeated_header_row() -> None:
    words = [
        *header_row(1),
        *data_row(1, 56.0, "20250901", "13010", [(127.1, 139.8, "極洋")], KYOKUYO_VALUES),
    ]
    records = m.build_records(words)
    assert len(records) == 1
    assert records[0].code == "13010"


def test_build_records_missing_name_is_none() -> None:
    """元PDF自体に名称欄の印字が無い銘柄がある(実機で確認、パースのバグではない)。"""
    words = data_row(1, 56.0, "20250901", "75320", [], KYOKUYO_VALUES)
    records = m.build_records(words)
    assert records[0].name_ja is None


def test_build_records_spans_multiple_dates_and_pages() -> None:
    """1ファイル=1ヶ月ぶんの全営業日を含むため、複数ページ・複数日付を1回で処理できる。"""
    words = [
        *data_row(1, 56.0, "20250901", "13010", [(127.1, 139.8, "極洋")], KYOKUYO_VALUES),
        *data_row(2, 56.0, "20250902", "13320", [(127.1, 152.5, "ニッスイ")], KYOKUYO_VALUES),
    ]
    records = m.build_records(words)
    assert [(r.file_date, r.code) for r in records] == [
        ("2025-09-01", "13010"),
        ("2025-09-02", "13320"),
    ]


def test_build_records_accepts_alphanumeric_reit_code() -> None:
    words = data_row(1, 56.0, "20250930", "207A0", [(127.1, 200.0, "ライジングコーポレーション")], KYOKUYO_VALUES)
    records = m.build_records(words)
    assert records[0].code == "207A0"


def test_build_records_different_codes_same_date_are_separate_records() -> None:
    words = [
        *data_row(1, 56.0, "20250901", "13010", [(127.1, 139.8, "極洋")], KYOKUYO_VALUES),
        *data_row(1, 66.0, "20250901", "13320", [(127.1, 152.5, "ニッスイ")], KYOKUYO_VALUES),
    ]
    records = m.build_records(words)
    assert {r.code for r in records} == {"13010", "13320"}
    assert len(records) == 2
