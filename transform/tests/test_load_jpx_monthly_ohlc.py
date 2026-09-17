"""load_jpx_monthly_ohlc.py の単体テスト。

`jpx_stq_pdf.iter_words`(PDF抽出)・`jpx_monthly_ohlc_facts.build_records`
(構造化)は既に個別のテストファイルで検証済みのため、ここでは
load_jpx_monthly_ohlc.py自身の責務（月単位の取り込み対象判定・1PDFから
複数日付パーティションへの書き分け・月マーカーの付与）に絞って検証する。
`jpx_stq_pdf.iter_words`はmonkeypatchで差し替える（test_load_jpx_stq.pyと
同じ方針）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

import jpx_stq_pdf
from flows import load_jpx_monthly_ohlc as m


def month_pdf_path(lake: Path, month: str) -> Path:
    yyyy, mm = month.split("-")
    return lake / "jpx-daily-pdf-dl" / "raw" / "monthly-ohlc" / yyyy / mm / "stq_monthly.pdf"


def make_month_pdf(lake: Path, month: str) -> None:
    path = month_pdf_path(lake, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"dummy")


def header_row(page: int) -> list[jpx_stq_pdf.Word]:
    """列境界の動的検出に必要なヘッダー行(実測座標、iText世代)。"""
    labels = [
        (52.7, 90.0, "約定年月日"), (90.0, 127.1, "銘柄コード"), (127.1, 152.6, "銘柄名称"),
        (306.4, 331.9, "前場始値"), (336.1, 361.6, "前場高値"), (365.8, 391.3, "前場安値"),
        (395.5, 421.0, "前場終値"), (425.2, 450.7, "後場始値"), (454.9, 480.4, "後場高値"),
        (484.6, 510.1, "後場安値"), (514.3, 539.8, "後場終値"),
    ]
    return [jpx_stq_pdf.Word(page=page, top=46.0, x0=x0, x1=x1, size=6.36, text=t) for x0, x1, t in labels]


def sample_words_two_days(page: int) -> list[jpx_stq_pdf.Word]:
    """2025-09-01(1301)・2025-09-02(1332)の2日ぶんを1PDF(2ページ)想定で返す。"""
    return [
        *header_row(page),
        # 2025-09-01, code 1301
        jpx_stq_pdf.Word(page=page, top=56.0, x0=54.4, x1=82.7, size=6.36, text="20250901"),
        jpx_stq_pdf.Word(page=page, top=56.0, x0=97.0, x1=114.7, size=6.36, text="1301"),
        jpx_stq_pdf.Word(page=page, top=56.0, x0=127.1, x1=139.8, size=6.36, text="極洋"),
        jpx_stq_pdf.Word(page=page, top=56.0, x0=317.7, x1=331.9, size=6.36, text="4710"),
        jpx_stq_pdf.Word(page=page, top=56.0, x0=347.5, x1=361.6, size=6.36, text="4790"),
        jpx_stq_pdf.Word(page=page, top=56.0, x0=377.2, x1=391.4, size=6.36, text="4685"),
        jpx_stq_pdf.Word(page=page, top=56.0, x0=407.0, x1=421.1, size=6.36, text="4760"),
        jpx_stq_pdf.Word(page=page, top=56.0, x0=436.7, x1=450.9, size=6.36, text="4760"),
        jpx_stq_pdf.Word(page=page, top=56.0, x0=466.5, x1=480.7, size=6.36, text="4785"),
        jpx_stq_pdf.Word(page=page, top=56.0, x0=496.3, x1=510.4, size=6.36, text="4735"),
        jpx_stq_pdf.Word(page=page, top=56.0, x0=526.0, x1=540.2, size=6.36, text="4785"),
        # 2025-09-02, code 1332
        jpx_stq_pdf.Word(page=page, top=66.0, x0=54.4, x1=82.7, size=6.36, text="20250902"),
        jpx_stq_pdf.Word(page=page, top=66.0, x0=97.0, x1=114.7, size=6.36, text="1332"),
        jpx_stq_pdf.Word(page=page, top=66.0, x0=127.1, x1=152.5, size=6.36, text="ニッスイ"),
        jpx_stq_pdf.Word(page=page, top=66.0, x0=317.7, x1=331.9, size=6.36, text="1004"),
        jpx_stq_pdf.Word(page=page, top=66.0, x0=347.5, x1=361.6, size=6.36, text="1023"),
        jpx_stq_pdf.Word(page=page, top=66.0, x0=377.2, x1=391.4, size=6.36, text="1000"),
        jpx_stq_pdf.Word(page=page, top=66.0, x0=407.0, x1=421.1, size=6.36, text="1018"),
        jpx_stq_pdf.Word(page=page, top=66.0, x0=436.7, x1=450.9, size=6.36, text="1016"),
        jpx_stq_pdf.Word(page=page, top=66.0, x0=466.5, x1=480.7, size=6.36, text="1020"),
        jpx_stq_pdf.Word(page=page, top=66.0, x0=496.3, x1=510.4, size=6.36, text="1012"),
        jpx_stq_pdf.Word(page=page, top=66.0, x0=526.0, x1=540.2, size=6.36, text="1020"),
    ]


def parquet_rows(path: Path) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        return con.execute(f"select * from read_parquet('{path.as_posix()}', hive_partitioning=false)").fetchall()
    finally:
        con.close()


# --- disk_months / landing_logged_months / months_to_load --------------------


def test_disk_months_requires_stq_monthly_pdf(tmp_path: Path) -> None:
    make_month_pdf(tmp_path, "2025-09")
    (tmp_path / "jpx-daily-pdf-dl" / "raw" / "monthly-ohlc" / "2025" / "10").mkdir(parents=True)

    assert m.disk_months(tmp_path) == {"2025-09"}


def test_disk_months_empty_when_no_lake(tmp_path: Path) -> None:
    assert m.disk_months(tmp_path) == set()


def test_landing_logged_months_requires_marker_part_parquet(tmp_path: Path) -> None:
    done_dir = tmp_path / "jpx_monthly_ohlc_loaded_months" / "file_month=2025-08"
    done_dir.mkdir(parents=True)
    (done_dir / "part.parquet").write_bytes(b"x")
    partial_dir = tmp_path / "jpx_monthly_ohlc_loaded_months" / "file_month=2025-09"
    partial_dir.mkdir(parents=True)
    (partial_dir / "part.parquet.tmp").write_bytes(b"x")

    assert m.landing_logged_months(tmp_path) == {"2025-08"}


def test_landing_logged_months_excludes_empty_part_parquet(tmp_path: Path) -> None:
    """電源断でrenameだけ完了し中身が定着しなかった0バイトファイルは
    「取り込み済み」と見なさない(2026-09-17実機で発生・確認済み)。"""
    corrupt_dir = tmp_path / "jpx_monthly_ohlc_loaded_months" / "file_month=2025-08"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "part.parquet").touch()

    assert m.landing_logged_months(tmp_path) == set()


def test_months_to_load_excludes_already_loaded(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    for mo in ("2020-01", "2020-02", "2025-09"):
        make_month_pdf(lake, mo)
    done_dir = landing / "jpx_monthly_ohlc_loaded_months" / "file_month=2020-01"
    done_dir.mkdir(parents=True)
    (done_dir / "part.parquet").write_bytes(b"x")

    assert m.months_to_load(lake, landing) == ["2020-02", "2025-09"]


# --- load_one_month(jpx_stq_pdf.iter_words を monkeypatch) -------------------


def test_load_one_month_splits_into_day_partitions_and_writes_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    make_month_pdf(lake, "2025-09")
    words = sample_words_two_days(page=1)

    def fake_iter_words(pdf_path: Path) -> Iterator[jpx_stq_pdf.Word]:
        assert pdf_path == month_pdf_path(lake, "2025-09")
        yield from words

    monkeypatch.setattr(jpx_stq_pdf, "iter_words", fake_iter_words)

    result = m.load_one_month(lake, landing, "2025-09")

    assert result == {"days": 2, "rows": 2}

    day1 = landing / "jpx_monthly_ohlc_facts" / "file_date=2025-09-01" / "part.parquet"
    day2 = landing / "jpx_monthly_ohlc_facts" / "file_date=2025-09-02" / "part.parquet"
    marker = landing / "jpx_monthly_ohlc_loaded_months" / "file_month=2025-09" / "part.parquet"
    assert day1.exists() and day2.exists() and marker.exists()

    rows1 = parquet_rows(day1)
    assert len(rows1) == 1
    columns = [
        d[0]
        for d in duckdb.connect()
        .execute(f"select * from read_parquet('{day1.as_posix()}', hive_partitioning=false) limit 0")
        .description
    ]
    row = dict(zip(columns, rows1[0], strict=True))
    assert row["code"] == "1301"
    assert row["file_date"] == "2025-09-01"
    assert row["am_open"] == "4710"
    assert row["_loaded_at"] is not None

    # 月マーカーが取り込み済み判定に使われる
    assert m.landing_logged_months(landing) == {"2025-09"}
