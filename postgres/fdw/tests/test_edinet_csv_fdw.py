"""edinet_csv_fdw.py の単体テスト。合成の .csv.gz（BOM 付き UTF-16LE・CRLF・
タブ区切り・全クォート）フィクスチャで、走査・provenance 抽出・列正規化を検証する。"""

from __future__ import annotations

import csv
import gzip
import io
from pathlib import Path

import pytest

import edinet_csv_fdw as m

HEADER = [
    "要素ID",
    "項目名",
    "コンテキストID",
    "相対年度",
    "連結・個別",
    "期間・時点",
    "ユニットID",
    "単位",
    "値",
]


def write_csv_gz(path: Path, data_rows: list[list[str]]) -> None:
    """EDINET CSV と同じバイト列（BOM 付き UTF-16LE・CRLF・タブ区切り・全クォート）で
    gzip 圧縮したファイルを path に作る。"""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", quotechar='"', quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(HEADER)
    for row in data_rows:
        writer.writerow(row)
    raw = ("﻿" + buf.getvalue()).encode("utf-16-le")
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        fh.write(raw)


def csv_path(lake: Path, date: str, ec: str, doc: str, name: str) -> Path:
    """date は 'YYYY-MM-DD'。実レイクは raw/{yyyy}/{mm}/{dd}/{ec}/csv/{doc}/*.csv.gz。"""
    yyyy, mm, dd = date.split("-")
    return lake / "edinet-dl" / "raw" / yyyy / mm / dd / ec / "csv" / doc / f"{name}.csv.gz"


class FakeWriter:
    def __init__(self) -> None:
        self.rows: list[list[str]] = []

    def writerow(self, row: list[str]) -> object:
        self.rows.append(list(row))
        return None


# --- provenance_from_path -------------------------------------------------


def test_provenance_from_path_extracts_date_ec_doc(tmp_path: Path) -> None:
    p = csv_path(tmp_path, "2026-08-13", "E00011", "S100YW2X", "jpcrp040300-ssr-001_x")
    assert m.provenance_from_path(p, tmp_path) == ("2026-08-13", "E00011", "S100YW2X")


def test_provenance_from_path_rejects_unexpected_structure(tmp_path: Path) -> None:
    bad = tmp_path / "edinet-dl" / "raw" / "2026" / "08" / "13" / "E00011" / "pdf" / "S1.pdf"
    with pytest.raises(ValueError):
        m.provenance_from_path(bad, tmp_path)


# --- normalize_row -----------------------------------------------------------


def test_normalize_row_passes_through_exact_nine() -> None:
    row = [str(i) for i in range(9)]
    assert m.normalize_row(row) == row


def test_normalize_row_pads_when_short() -> None:
    assert m.normalize_row(["a", "b"]) == ["a", "b", "", "", "", "", "", "", ""]


def test_normalize_row_merges_tail_when_too_long() -> None:
    row = [str(i) for i in range(8)] + ["x", "y", "z"]
    out = m.normalize_row(row)
    assert len(out) == 9
    assert out[8] == "x\ty\tz"


# --- iter_data_rows --------------------------------------------------------


def test_iter_data_rows_skips_header_and_keeps_multiline_value(tmp_path: Path) -> None:
    p = csv_path(tmp_path, "2026-08-13", "E00011", "S100YW2X", "doc")
    multiline = "1行目\r\n2行目\tタブ入り"
    write_csv_gz(
        p,
        [
            ["jppfs_cor:NetSales", "売上高", "CurrentYearDuration", "当期", "連結", "期間", "JPY", "円", "1000"],
            ["jpcrp_cor:TextBlock", "注記", "FilingDateInstant", "提出日時点", "その他", "時点", "pure", "－", multiline],
        ],
    )
    rows = list(m.iter_data_rows(p))
    assert len(rows) == 2
    assert rows[0][0] == "jppfs_cor:NetSales"
    assert rows[0][8] == "1000"
    assert rows[1][8] == multiline
    assert all(len(r) == 9 for r in rows)


# --- run -----------------------------------------------------------------


def test_run_prepends_provenance_and_covers_all_docs(tmp_path: Path) -> None:
    write_csv_gz(
        csv_path(tmp_path, "2026-08-13", "E00011", "DOC_A", "a"),
        [["e1", "n1", "c1", "y1", "連結", "期間", "JPY", "円", "1"]],
    )
    write_csv_gz(
        csv_path(tmp_path, "2026-08-13", "E00011", "DOC_A", "a2"),
        [["e2", "n2", "c2", "y2", "個別", "期間", "JPY", "円", "2"]],
    )
    write_csv_gz(
        csv_path(tmp_path, "2026-08-14", "E00022", "DOC_B", "b"),
        [["e3", "n3", "c3", "y3", "連結", "時点", "JPY", "円", "3"]],
    )
    fw = FakeWriter()
    skipped = m.run(tmp_path, fw)
    assert skipped == 0
    assert len(fw.rows) == 3
    assert all(len(r) == 12 for r in fw.rows)
    assert {(r[0], r[1], r[2]) for r in fw.rows} == {
        ("2026-08-13", "E00011", "DOC_A"),
        ("2026-08-14", "E00022", "DOC_B"),
    }
    by_value = {r[-1]: r for r in fw.rows}
    assert by_value["1"][:4] == ["2026-08-13", "E00011", "DOC_A", "e1"]


def test_run_skips_corrupt_gz_without_aborting(tmp_path: Path) -> None:
    write_csv_gz(
        csv_path(tmp_path, "2026-08-13", "E00011", "DOC_A", "good"),
        [["e1", "n1", "c1", "y1", "連結", "期間", "JPY", "円", "1"]],
    )
    corrupt = csv_path(tmp_path, "2026-08-13", "E00011", "DOC_A", "bad")
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"this is not gzip")

    fw = FakeWriter()
    skipped = m.run(tmp_path, fw)
    assert skipped == 1
    assert len(fw.rows) == 1
    assert fw.rows[0][-1] == "1"


def test_iter_csv_files_date_filter_scopes_to_one_day(tmp_path: Path) -> None:
    write_csv_gz(csv_path(tmp_path, "2026-09-01", "E1", "D1", "a"), [])
    write_csv_gz(csv_path(tmp_path, "2026-09-02", "E1", "D2", "b"), [])
    write_csv_gz(csv_path(tmp_path, "2025-06-10", "E1", "D3", "c"), [])

    found = list(m.iter_csv_files(tmp_path, "2026-09-02"))
    assert [p.name for p in found] == ["b.csv.gz"]


def test_run_date_filter_emits_only_that_day(tmp_path: Path) -> None:
    write_csv_gz(
        csv_path(tmp_path, "2026-09-01", "E1", "D1", "a"),
        [["e1", "n", "c", "y", "連結", "期間", "JPY", "円", "1"]],
    )
    write_csv_gz(
        csv_path(tmp_path, "2026-09-02", "E1", "D2", "b"),
        [["e2", "n", "c", "y", "連結", "期間", "JPY", "円", "2"]],
    )
    fw = FakeWriter()
    m.run(tmp_path, fw, "2026-09-02")
    assert [(r[0], r[-1]) for r in fw.rows] == [("2026-09-02", "2")]


def test_main_rejects_malformed_date(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        m.main(["edinet_csv_fdw.py", str(tmp_path), "--date", "2026-9-2"])


def test_iter_csv_files_only_matches_csv_gz_in_order(tmp_path: Path) -> None:
    write_csv_gz(csv_path(tmp_path, "2026-08-13", "E00011", "D1", "z"), [])
    write_csv_gz(csv_path(tmp_path, "2026-08-13", "E00011", "D1", "a"), [])
    pdf_dir = tmp_path / "edinet-dl" / "raw" / "2026" / "08" / "13" / "E00011" / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    (pdf_dir / "S1.pdf").write_bytes(b"x")

    found = list(m.iter_csv_files(tmp_path))
    assert [p.name for p in found] == ["a.csv.gz", "z.csv.gz"]
