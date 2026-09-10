"""jpx_catalog_fdw.py の単体テスト。合成のファイルツリー（内容は空でよい）で
形式判定・period 生成・目録行の組み立てを検証する。"""

from __future__ import annotations

from pathlib import Path

import pytest

import jpx_catalog_fdw as m


def touch(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def raw(lake: Path) -> Path:
    return lake / "jpx-daily-pdf-dl" / "raw"


class FakeWriter:
    def __init__(self) -> None:
        self.rows: list[list[str]] = []

    def writerow(self, row: list[str]) -> object:
        self.rows.append(list(row))
        return None


# --- _period -------------------------------------------------------------


def test_period_daily_and_monthly() -> None:
    assert m._period(m._DAILY, ("2019", "12", "02", "stq.pdf")) == "2019-12-02"
    assert m._period(m._MONTHLY, ("2025", "01", "stq_monthly.pdf")) == "2025-01"


def test_period_rejects_non_numeric_components() -> None:
    with pytest.raises(ValueError):
        m._period(m._DAILY, ("20XX", "12", "02", "stq.pdf"))
    with pytest.raises(ValueError):
        m._period(m._MONTHLY, ("2025", "Jan", "stq_monthly.pdf"))


# --- row_for_file ---------------------------------------------------------


def test_row_for_file_legacy_daily_pdf(tmp_path: Path) -> None:
    p = touch(raw(tmp_path) / "legacy-daily" / "2019" / "12" / "02" / "stq.pdf", b"abcde")
    row = m.row_for_file(tmp_path, "legacy-daily", p)
    assert row[:5] == [
        "legacy-daily",
        "daily",
        "2019-12-02",
        "pdf",
        "jpx-daily-pdf-dl/raw/legacy-daily/2019/12/02/stq.pdf",
    ]
    assert row[5] == "5"  # byte_size
    assert row[6].endswith("+00:00")  # modified_at is ISO 8601 UTC


def test_row_for_file_legacy_daily_tif(tmp_path: Path) -> None:
    p = touch(raw(tmp_path) / "legacy-daily" / "1985" / "03" / "15" / "stq.tif")
    row = m.row_for_file(tmp_path, "legacy-daily", p)
    assert row[1:4] == ["daily", "1985-03-15", "tif"]


def test_row_for_file_monthly_ohlc(tmp_path: Path) -> None:
    p = touch(raw(tmp_path) / "monthly-ohlc" / "2025" / "01" / "stq_monthly.pdf")
    row = m.row_for_file(tmp_path, "monthly-ohlc", p)
    assert row[:4] == ["monthly-ohlc", "monthly", "2025-01", "pdf"]
    assert row[4] == "jpx-daily-pdf-dl/raw/monthly-ohlc/2025/01/stq_monthly.pdf"


# --- iter_catalog_files / run -------------------------------------------


def test_run_covers_all_three_formats_sorted(tmp_path: Path) -> None:
    touch(raw(tmp_path) / "detailed-daily" / "2026" / "08" / "13" / "stq.pdf")
    touch(raw(tmp_path) / "monthly-ohlc" / "2025" / "01" / "stq_monthly.pdf")
    touch(raw(tmp_path) / "legacy-daily" / "2019" / "12" / "02" / "stq.pdf")
    touch(raw(tmp_path) / "legacy-daily" / "1985" / "03" / "15" / "stq.tif")

    fw = FakeWriter()
    skipped = m.run(tmp_path, fw)
    assert skipped == 0
    assert [r[4] for r in fw.rows] == [
        "jpx-daily-pdf-dl/raw/detailed-daily/2026/08/13/stq.pdf",
        "jpx-daily-pdf-dl/raw/legacy-daily/1985/03/15/stq.tif",
        "jpx-daily-pdf-dl/raw/legacy-daily/2019/12/02/stq.pdf",
        "jpx-daily-pdf-dl/raw/monthly-ohlc/2025/01/stq_monthly.pdf",
    ]
    assert all(len(r) == len(m.OUTPUT_HEADER) for r in fw.rows)


def test_run_ignores_non_pdf_tif_siblings(tmp_path: Path) -> None:
    touch(raw(tmp_path) / "legacy-daily" / "2019" / "12" / "02" / "stq.pdf")
    touch(raw(tmp_path) / "legacy-daily" / "2019" / "12" / "02" / "stq.txt")
    touch(raw(tmp_path) / "legacy-daily" / "2019" / "12" / "02" / "notes.json")

    fw = FakeWriter()
    m.run(tmp_path, fw)
    assert [r[3] for r in fw.rows] == ["pdf"]


def test_row_for_file_rejects_unexpected_path_depth(tmp_path: Path) -> None:
    # glob はグロブ側で階層を固定するため通常は起きないが、防御的ガードを直接検証する。
    p = touch(raw(tmp_path) / "legacy-daily" / "2019" / "12" / "stq.pdf")
    with pytest.raises(ValueError):
        m.row_for_file(tmp_path, "legacy-daily", p)


def test_run_continues_past_a_bad_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    touch(raw(tmp_path) / "legacy-daily" / "2019" / "12" / "02" / "stq.pdf")
    touch(raw(tmp_path) / "legacy-daily" / "2020" / "01" / "06" / "stq.pdf")

    real_stat = Path.stat

    def flaky_stat(self: Path, *a: object, **k: object) -> object:
        if self.as_posix().endswith("2019/12/02/stq.pdf"):
            raise OSError("boom")
        return real_stat(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", flaky_stat)
    fw = FakeWriter()
    skipped = m.run(tmp_path, fw)
    assert skipped == 1
    assert [r[2] for r in fw.rows] == ["2020-01-06"]


def test_run_on_empty_lake_returns_nothing(tmp_path: Path) -> None:
    fw = FakeWriter()
    assert m.run(tmp_path, fw) == 0
    assert fw.rows == []
