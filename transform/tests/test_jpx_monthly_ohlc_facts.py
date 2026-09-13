"""jpx_monthly_ohlc_facts.py の単体テスト。

実際のJPX PDFは利用規約上コミットできないため、`jpx_stq_pdf.Word`を直接
組み立ててテストする。座標値は実機データ（2020-01・2022-12・2023-01・
2025-09の4サンプル、PDF生成ソフトウェアがAntennaHouse→iTextへ切り替わる
前後を含む）で実測した値を使用している。

列境界はヘッダー行から動的に算出する設計（実機データで、AntennaHouse世代は
月によって座標が変動することが判明したため固定座標定数では対応できないと
判断した経緯はjpx_monthly_ohlc_facts.pyのモジュールdocstring参照）。
"""

from __future__ import annotations

import jpx_monthly_ohlc_facts as m
from jpx_stq_pdf import Word

# --- ヘッダー行ビルダー（実測座標） -----------------------------------------


def header_antennahouse_2020(page: int, top: float = 57.0) -> list[Word]:
    """2020-01実測（短縮ラベル"年月日"/"コード"/"銘柄名"）。"""
    labels = [
        (52.3, 68.2, "年月日"), (79.7, 95.5, "コード"), (109.9, 125.8, "銘柄名"),
        (223.3, 244.4, "前場始値"), (249.2, 270.4, "前場高値"), (275.2, 296.3, "前場安値"),
        (301.1, 322.2, "前場終値"), (327.0, 348.1, "後場始値"), (352.9, 374.0, "後場高値"),
        (378.8, 400.0, "後場安値"), (404.8, 425.9, "後場終値"),
    ]
    return [Word(page=page, top=top, x0=x0, x1=x1, size=6.0, text=t) for x0, x1, t in labels]


def header_antennahouse_2022(page: int, top: float = 57.0) -> list[Word]:
    """2022-12実測（正式ラベル、AntennaHouse末期。座標は2020-01と別物）。"""
    labels = [
        (51.6, 81.0, "約定年月日"), (87.2, 116.6, "銘柄コード"), (122.9, 146.4, "銘柄名称"),
        (298.2, 321.7, "前場始値"), (327.4, 350.9, "前場高値"), (356.5, 380.0, "前場安値"),
        (385.7, 409.2, "前場終値"), (414.8, 438.4, "後場始値"), (444.0, 467.5, "後場高値"),
        (473.2, 496.7, "後場安値"), (502.3, 525.8, "後場終値"),
    ]
    return [Word(page=page, top=top, x0=x0, x1=x1, size=6.0, text=t) for x0, x1, t in labels]


def header_itext(page: int, top: float = 46.0) -> list[Word]:
    """2023-01以降実測（iText世代、2025-09の形式Cと同系統の座標）。"""
    labels = [
        (52.7, 90.0, "約定年月日"), (90.0, 127.1, "銘柄コード"), (127.1, 152.6, "銘柄名称"),
        (306.4, 331.9, "前場始値"), (336.1, 361.6, "前場高値"), (365.8, 391.3, "前場安値"),
        (395.5, 421.0, "前場終値"), (425.2, 450.7, "後場始値"), (454.9, 480.4, "後場高値"),
        (484.6, 510.1, "後場安値"), (514.3, 539.8, "後場終値"),
    ]
    return [Word(page=page, top=top, x0=x0, x1=x1, size=6.36, text=t) for x0, x1, t in labels]


# --- データ行ビルダー --------------------------------------------------------


def antennahouse_data_row(
    page: int, top: float, date: str, code_name_token: str,
    values: list[tuple[float, float, str]],
) -> list[Word]:
    """AntennaHouse世代: コード+名前が1トークンに結合。"""
    words = [Word(page=page, top=top, x0=54.7, x1=77.8, size=6.0, text=date)]
    words.append(Word(page=page, top=top, x0=93.6, x1=93.6 + len(code_name_token) * 6, size=6.0, text=code_name_token))
    words += [Word(page=page, top=top, x0=x0, x1=x1, size=6.0, text=t) for x0, x1, t in values]
    return words


def itext_data_row(
    page: int, top: float, date: str, code: str, name_tokens: list[tuple[float, float, str]],
    values: list[tuple[float, float, str]],
) -> list[Word]:
    """iText世代: コードと名前が別トークン。"""
    words = [
        Word(page=page, top=top, x0=54.5, x1=82.9, size=6.36, text=date),
        Word(page=page, top=top, x0=97.2, x1=114.9, size=6.36, text=code),
    ]
    words += [Word(page=page, top=top, x0=x0, x1=x1, size=6.36, text=t) for x0, x1, t in name_tokens]
    words += [Word(page=page, top=top, x0=x0, x1=x1, size=6.36, text=t) for x0, x1, t in values]
    return words


# 実測(1301極洋、2020-01-06)のOHLC座標
VALUES_2020 = [
    (235.8, 247.4, "2860"), (261.7, 273.3, "2873"), (287.6, 299.2, "2851"), (313.6, 325.1, "2865"),
    (339.5, 351.1, "2863"), (365.4, 377.0, "2869"), (391.3, 402.9, "2857"), (417.2, 428.8, "2862"),
]
# 実測(1301極洋、2023-01-04)のOHLC座標
VALUES_ITEXT = [
    (317.7, 331.9, "3815"), (347.4, 361.6, "3815"), (377.1, 391.3, "3740"), (406.8, 421.0, "3745"),
    (436.5, 450.7, "3745"), (466.2, 480.4, "3755"), (495.9, 510.1, "3735"), (525.6, 539.8, "3750"),
]


# --- AntennaHouse世代(2020-01) -----------------------------------------------


def test_build_records_antennahouse_2020_splits_code_and_name() -> None:
    words = [
        *header_antennahouse_2020(1),
        *antennahouse_data_row(1, 65.0, "20200106", "13010極洋", VALUES_2020),
    ]
    records = m.build_records(words)
    assert len(records) == 1
    r = records[0]
    assert r.file_date == "2020-01-06"
    assert r.code == "13010"
    assert r.name_ja == "極洋"
    assert (r.am_open, r.am_high, r.am_low, r.am_close) == ("2860", "2873", "2851", "2865")
    assert (r.pm_open, r.pm_high, r.pm_low, r.pm_close) == ("2863", "2869", "2857", "2862")


def test_build_records_antennahouse_preserves_non_zero_branch_code() -> None:
    """優先株等の枝番は末尾0とは限らない(実機で"25935"=伊藤園優先株式を確認)。
    生データのまま保持し、正規化(末尾0除去等)はしない。"""
    words = [
        *header_antennahouse_2020(1),
        *antennahouse_data_row(1, 65.0, "20200106", "25935伊藤園優先株式", VALUES_2020),
    ]
    records = m.build_records(words)
    assert records[0].code == "25935"
    assert records[0].name_ja == "伊藤園優先株式"


# --- AntennaHouse世代(2022-12、座標が2020-01と異なる) ------------------------


def test_build_records_antennahouse_2022_different_coordinates_than_2020() -> None:
    """同じAntennaHouse世代でも月によって座標が変動する(実機で確認)。
    ヘッダーから動的に算出するため、2020-01用の座標を使っても正しく処理できる。"""
    values_2022 = [
        (312.2, 325.2, "3710"), (341.4, 354.4, "3720"), (370.6, 383.5, "3695"), (399.7, 412.7, "3695"),
        (428.9, 441.9, "3690"), (458.0, 471.0, "3700"), (487.2, 500.2, "3675"), (516.4, 529.3, "3695"),
    ]
    words = [
        *header_antennahouse_2022(1),
        *antennahouse_data_row(1, 66.0, "20221201", "13010極洋", values_2022),
    ]
    records = m.build_records(words)
    assert len(records) == 1
    r = records[0]
    assert r.code == "13010"
    assert (r.am_open, r.am_high, r.am_low, r.am_close) == ("3710", "3720", "3695", "3695")
    assert (r.pm_open, r.pm_high, r.pm_low, r.pm_close) == ("3690", "3700", "3675", "3695")


# --- iText世代(2023-01以降) --------------------------------------------------


def test_build_records_itext_code_and_name_already_separate() -> None:
    words = [
        *header_itext(1),
        *itext_data_row(1, 57.0, "20230104", "13010", [(127.2, 139.9, "極洋"), (146.3, 171.7, "普通株式")], VALUES_ITEXT),
    ]
    records = m.build_records(words)
    assert len(records) == 1
    r = records[0]
    assert r.file_date == "2023-01-04"
    assert r.code == "13010"
    assert r.name_ja == "極洋　普通株式"
    assert (r.am_open, r.am_high, r.am_low, r.am_close) == ("3815", "3815", "3740", "3745")


def test_build_records_itext_alphanumeric_code_not_split() -> None:
    """新証券コード("130A0"等)は数字の後に半角英字が続くため、
    AntennaHouse世代のコード+名前分離ロジックに誤って引っかからない。"""
    words = [
        *header_itext(1),
        *itext_data_row(1, 57.0, "20250930", "130A0", [(127.1, 200.0, "テストコーポレーション")], VALUES_ITEXT),
    ]
    records = m.build_records(words)
    assert records[0].code == "130A0"
    assert records[0].name_ja == "テストコーポレーション"


# --- 共通ロジック -------------------------------------------------------------


def test_build_records_ignores_repeated_header_row() -> None:
    words = [
        *header_itext(1),
        *itext_data_row(1, 57.0, "20230104", "13010", [(127.2, 139.9, "極洋")], VALUES_ITEXT),
        *header_itext(1, top=200.0),  # 次ページの繰り返しヘッダーを模す
        *itext_data_row(1, 210.0, "20230105", "13010", [(127.2, 139.9, "極洋")], VALUES_ITEXT),
    ]
    records = m.build_records(words)
    assert [r.file_date for r in records] == ["2023-01-04", "2023-01-05"]


def test_build_records_accepts_one_shot_generator() -> None:
    """build_recordsはSequenceではなくIterableを受け取る(月全体をlist化しない
    ためのストリーム処理、2026-09-13修正)。一度しか反復できないジェネレータ
    (list()化されていない`jpx_stq_pdf.iter_words`相当)でも動作することを検証。"""
    words = [
        *header_itext(1),
        *itext_data_row(1, 57.0, "20230104", "13010", [(127.2, 139.9, "極洋")], VALUES_ITEXT),
    ]
    records = m.build_records(w for w in words)  # ジェネレータ式(一度しか反復不可)
    assert len(records) == 1
    assert records[0].code == "13010"


def test_build_records_no_header_yet_discards_rows() -> None:
    """ヘッダーが見つかる前のデータ行(想定外)は安全側に倒して破棄する。"""
    words = itext_data_row(1, 57.0, "20230104", "13010", [(127.2, 139.9, "極洋")], VALUES_ITEXT)
    records = m.build_records(words)
    assert records == []


def test_build_records_header_only_on_first_page_carries_across_pages() -> None:
    """実データは全ページにヘッダーが繰り返されるが、ページ単位ストリーム処理
    (2026-09-13修正)がヘッダー無しページでも直前の列境界を正しく引き継ぐことを
    明示的に検証する回帰テスト。"""
    words = [
        *header_itext(1),
        *itext_data_row(2, 57.0, "20230104", "13010", [(127.2, 139.9, "極洋")], VALUES_ITEXT),
        *itext_data_row(3, 57.0, "20230105", "13320", [(127.2, 152.5, "ニッスイ")], VALUES_ITEXT),
    ]
    records = m.build_records(words)
    assert [(r.file_date, r.code, r.am_open) for r in records] == [
        ("2023-01-04", "13010", "3815"),
        ("2023-01-05", "13320", "3815"),
    ]


def test_build_records_spans_multiple_dates_and_pages() -> None:
    words = [
        *header_itext(1),
        *itext_data_row(1, 57.0, "20230104", "13010", [(127.2, 139.9, "極洋")], VALUES_ITEXT),
        *header_itext(2),
        *itext_data_row(2, 57.0, "20230105", "13320", [(127.2, 152.5, "ニッスイ")], VALUES_ITEXT),
    ]
    records = m.build_records(words)
    assert [(r.file_date, r.code) for r in records] == [
        ("2023-01-04", "13010"),
        ("2023-01-05", "13320"),
    ]
