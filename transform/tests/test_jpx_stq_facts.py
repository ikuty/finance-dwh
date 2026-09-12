"""jpx_stq_facts.py の単体テスト。

実際のJPX PDFはコミットできない(利用規約上、個人利用限定)ため、
`jpx_stq_pdf.Word`を直接組み立ててテストする。座標値は実機データ
(2026-09-10分の形式C PDF)で実測した値を使用している。
"""

from __future__ import annotations

import jpx_stq_facts as m
from jpx_stq_pdf import Word

FILE_DATE = "2026-09-10"


def section_id(page: int, text: str) -> Word:
    """ページ右上のセクション番号トークン(例: "1-5")。"""
    return Word(page=page, top=73.6, x0=1068.0, x1=1087.0, size=10.0, text=text)


def date_line(page: int) -> Word:
    return Word(page=page, top=73.6, x0=86.0, x1=196.0, size=10.0, text=f"{FILE_DATE}(木曜日)")


def column_header_row(page: int, top: float = 95.0) -> list[Word]:
    """繰り返し出現する列ヘッダー行(サイズ10pt)。"""
    labels = [
        (78.0, "コード"), (123.0, "単位"), (203.0, "銘柄名"),
        (303.0, "始値"), (363.0, "高値"), (423.0, "安値"), (483.0, "終値"),
        (543.0, "始値"), (603.0, "高値"), (663.0, "安値"), (723.0, "終値"),
        (776.0, "最終気配"), (846.0, "前日比"), (901.0, "売買高加重"),
        (983.0, "売買高"), (1058.0, "売買代金"),
    ]
    return [Word(page=page, top=top, x0=x0, x1=x0 + 20, size=10.0, text=t) for x0, t in labels]


def am_pm_session_row(page: int, top: float = 85.0) -> list[Word]:
    return [
        Word(page=page, top=top, x0=319.0, x1=340.0, size=10.0, text="午前"),
        Word(page=page, top=top, x0=556.0, x1=577.0, size=10.0, text="午後"),
    ]


def market_segment(page: int, text: str, top: float) -> Word:
    return Word(page=page, top=top, x0=564.5, x1=620.0, size=10.0, text=text)


def industry_heading(page: int, ja: str, en: str, top: float) -> list[Word]:
    return [
        Word(page=page, top=top, x0=116.0, x1=200.0, size=10.0, text=ja),
        Word(page=page, top=top, x0=246.0, x1=380.0, size=10.0, text=en),
    ]


def data_row(page: int, top: float, code: str, unit_name: str) -> list[Word]:
    """実機実測の座標(1301 極洋、2026-09-10)を使ったデータ行。"""
    return [
        Word(page=page, top=top, x0=74.0, x1=100.0, size=6.0, text=code),
        Word(page=page, top=top, x0=140.7, x1=200.0, size=6.0, text=unit_name),
        Word(page=page, top=top, x0=316.2, x1=340.0, size=6.0, text="4,610.00"),
        Word(page=page, top=top, x0=376.2, x1=400.0, size=6.0, text="4,630.00"),
        Word(page=page, top=top, x0=436.2, x1=460.0, size=6.0, text="4,590.00"),
        Word(page=page, top=top, x0=496.2, x1=520.0, size=6.0, text="4,615.00"),
        Word(page=page, top=top, x0=556.2, x1=580.0, size=6.0, text="4,615.00"),
        Word(page=page, top=top, x0=616.2, x1=640.0, size=6.0, text="4,640.00"),
        Word(page=page, top=top, x0=676.2, x1=700.0, size=6.0, text="4,605.00"),
        Word(page=page, top=top, x0=736.2, x1=760.0, size=6.0, text="4,640.00"),
        Word(page=page, top=top, x0=821.0, x1=830.0, size=6.0, text="－"),
        Word(page=page, top=top, x0=875.3, x1=895.0, size=6.0, text="20.00"),
        Word(page=page, top=top, x0=923.6, x1=960.0, size=6.0, text="4,618.9074"),
        Word(page=page, top=top, x0=1024.1, x1=1040.0, size=6.0, text="27.0"),
        Word(page=page, top=top, x0=1079.9, x1=1120.0, size=6.0, text="124,710.500"),
    ]


def name_en_row(page: int, top: float, text: str) -> Word:
    return Word(page=page, top=top, x0=154.5, x1=250.0, size=6.0, text=text)


# --- find_section1_pages ---------------------------------------------------


def test_find_section1_pages_matches_leading_component_one() -> None:
    words = [
        section_id(1, "1-1"),
        section_id(2, "1-190"),
        section_id(3, "2-1"),
        section_id(4, "5-1-1"),
        section_id(5, "13-1-1"),
    ]
    assert m.find_section1_pages(words) == {1, 2}


# --- build_records: 基本のデータ行 -----------------------------------------


def test_build_records_extracts_all_columns_from_single_row() -> None:
    words = [
        date_line(1),
        *column_header_row(1),
        *am_pm_session_row(1),
        market_segment(1, "プライム市場", top=357.6),
        *industry_heading(1, "水産・農林業", "Fishery,Agriculture&Forestry", top=388.1),
        *data_row(1, 407.9, "1301", "100極洋"),
    ]
    records = m.build_records(words, {1}, FILE_DATE)
    assert len(records) == 1
    r = records[0]
    assert r.file_date == FILE_DATE
    assert r.code == "1301"
    assert r.trading_unit == "100"
    assert r.name_ja == "極洋"
    assert r.market_segment == "プライム市場"
    assert r.industry_sector_ja == "水産・農林業"
    assert r.industry_sector_en == "Fishery,Agriculture&Forestry"
    assert (r.am_open, r.am_high, r.am_low, r.am_close) == ("4,610.00", "4,630.00", "4,590.00", "4,615.00")
    assert (r.pm_open, r.pm_high, r.pm_low, r.pm_close) == ("4,615.00", "4,640.00", "4,605.00", "4,640.00")
    assert r.final_special_quote == "－"
    assert r.net_change == "20.00"
    assert r.vwap == "4,618.9074"
    assert r.trading_volume == "27.0"
    assert r.trading_value == "124,710.500"


def test_build_records_merges_english_name_continuation_row() -> None:
    words = [
        *data_row(1, 407.9, "1301", "100極洋"),
        name_en_row(1, 419.9, "KYOKUYOCO.,LTD."),
    ]
    records = m.build_records(words, {1}, FILE_DATE)
    assert records[0].name_en == "KYOKUYOCO.,LTD."


def test_build_records_ignores_words_outside_section1_pages() -> None:
    words = [
        *data_row(1, 407.9, "1301", "100極洋"),
        *data_row(2, 407.9, "9999", "100他ページ"),
    ]
    records = m.build_records(words, {1}, FILE_DATE)
    assert [r.code for r in records] == ["1301"]


# --- 見出し行の継承・除外 ----------------------------------------------------


def test_build_records_inherits_heading_state_across_rows() -> None:
    words = [
        market_segment(1, "プライム市場", top=357.6),
        *industry_heading(1, "水産・農林業", "Fishery,Agriculture&Forestry", top=388.1),
        *data_row(1, 407.9, "1301", "100極洋"),
        *data_row(1, 431.9, "1332", "100ニッスイ"),
    ]
    records = m.build_records(words, {1}, FILE_DATE)
    assert len(records) == 2
    assert all(r.market_segment == "プライム市場" for r in records)
    assert all(r.industry_sector_ja == "水産・農林業" for r in records)


def test_build_records_ignores_repeated_column_header_row() -> None:
    words = [
        market_segment(1, "プライム市場", top=100.0),
        *industry_heading(1, "水産・農林業", "Fishery,Agriculture&Forestry", top=150.0),
        *column_header_row(1, top=300.0),  # 次ページの繰り返しヘッダーを模す
        *data_row(1, 407.9, "1301", "100極洋"),
    ]
    records = m.build_records(words, {1}, FILE_DATE)
    assert records[0].industry_sector_ja == "水産・農林業"  # ヘッダー行に上書きされない


def test_build_records_ignores_am_pm_session_label_row() -> None:
    words = [
        market_segment(1, "プライム市場", top=100.0),
        *industry_heading(1, "水産・農林業", "Fishery,Agriculture&Forestry", top=150.0),
        *am_pm_session_row(1, top=250.0),
        *data_row(1, 407.9, "1301", "100極洋"),
    ]
    records = m.build_records(words, {1}, FILE_DATE)
    assert records[0].industry_sector_ja == "水産・農林業"


def test_build_records_ignores_date_line() -> None:
    words = [
        market_segment(1, "プライム市場", top=100.0),
        *industry_heading(1, "水産・農林業", "Fishery,Agriculture&Forestry", top=150.0),
        date_line(1),
        *data_row(1, 407.9, "1301", "100極洋"),
    ]
    records = m.build_records(words, {1}, FILE_DATE)
    assert records[0].industry_sector_ja == "水産・農林業"


def test_build_records_ignores_transaction_type_heading_containing_shijou() -> None:
    """「立会市場普通取引」は"市場"を含むが取引種別見出しであり、market_segmentではない。"""
    words = [
        Word(page=1, top=209.6, x0=450.0, x1=760.0, size=10.0, text="立会市場普通取引"),
        market_segment(1, "プライム市場", top=357.6),
        *data_row(1, 407.9, "1301", "100極洋"),
    ]
    records = m.build_records(words, {1}, FILE_DATE)
    assert records[0].market_segment == "プライム市場"


# --- REIT等の投資口コード・単位欄なし ---------------------------------------


def test_build_records_accepts_alphanumeric_reit_code() -> None:
    words = data_row(1, 407.9, "256A", "Ｒ－ＫＤＸ不動産")
    records = m.build_records(words, {1}, FILE_DATE)
    assert records[0].code == "256A"


def test_build_records_trading_unit_none_when_no_leading_digit() -> None:
    words = data_row(1, 407.9, "8972", "Ｒ－ＫＤＸ不動産")
    records = m.build_records(words, {1}, FILE_DATE)
    assert records[0].trading_unit is None
    assert records[0].name_ja == "Ｒ－ＫＤＸ不動産"


# --- run ---------------------------------------------------------------


def test_run_combines_section_detection_and_structuring() -> None:
    words = [
        section_id(1, "1-1"),
        section_id(2, "2-1"),
        *data_row(1, 407.9, "1301", "100極洋"),
        *data_row(2, 407.9, "9999", "100対象外"),
    ]
    records = m.run(words, FILE_DATE)
    assert [r.code for r in records] == ["1301"]
