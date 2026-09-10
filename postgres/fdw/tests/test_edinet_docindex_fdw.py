"""edinet_docindex_fdw.py の単体テスト。合成の
response/{yyyy}/{mm}/{dd}/document_list.json フィクスチャで
走査・file_date 抽出・null の空文字化・列順を検証する。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import edinet_docindex_fdw as m


def make_record(**over: object) -> dict[str, object]:
    """RESULT_KEYS を全て持つ 1 レコードを作る（既定は空文字、seqNumber は 1）。"""
    rec: dict[str, object] = {key: "" for key in m.RESULT_KEYS}
    rec["seqNumber"] = 1
    rec.update(over)
    return rec


def write_response(path: Path, records: list[dict[str, object]] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"metadata": {"resultset": {"count": len(records or [])}}}
    if records is not None:
        payload["results"] = records
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def response_path(lake: Path, date: str) -> Path:
    """date は 'YYYY-MM-DD'。実レイクは raw/response/{yyyy}/{mm}/{dd}/document_list.json。"""
    yyyy, mm, dd = date.split("-")
    return lake / "edinet-dl" / "raw" / "response" / yyyy / mm / dd / "document_list.json"


# --- file_date_from_path ---------------------------------------------------


def test_file_date_from_path_extracts_date_from_path_segments() -> None:
    p = Path("/lake/edinet-dl/raw/response/2026/08/13/document_list.json")
    assert m.file_date_from_path(p) == "2026-08-13"


def test_file_date_from_path_rejects_unexpected_structure() -> None:
    with pytest.raises(ValueError):
        m.file_date_from_path(Path("/x/response/2026/aug/13/document_list.json"))
    with pytest.raises(ValueError):
        m.file_date_from_path(Path("/x/document_list.json"))


# --- _cell ---------------------------------------------------------------


def test_cell_maps_none_to_empty_and_stringifies() -> None:
    assert m._cell(None) == ""
    assert m._cell(1) == "1"
    assert m._cell("S100") == "S100"


# --- iter_records ------------------------------------------------------------


def test_iter_records_orders_by_result_keys_and_blanks_nulls(tmp_path: Path) -> None:
    p = response_path(tmp_path, "2026-08-13")
    write_response(
        p,
        [
            make_record(docID="S100AAAA", edinetCode="E00001", secCode="12345", seqNumber=1),
            make_record(docID="S100BBBB", edinetCode="E00002", secCode=None, seqNumber=2),
        ],
    )
    rows = list(m.iter_records(p))
    assert len(rows) == 2
    # RESULT_KEYS: [seqNumber, docID, edinetCode, secCode, ...]
    assert rows[0][:4] == ["1", "S100AAAA", "E00001", "12345"]
    assert rows[1][:4] == ["2", "S100BBBB", "E00002", ""]
    assert all(len(r) == len(m.RESULT_KEYS) for r in rows)


def test_iter_records_tolerates_missing_or_empty_results(tmp_path: Path) -> None:
    p1 = response_path(tmp_path, "2026-08-13")
    write_response(p1, [])
    assert list(m.iter_records(p1)) == []

    p2 = response_path(tmp_path, "2026-08-14")
    write_response(p2, None)
    assert list(m.iter_records(p2)) == []


# --- run ---------------------------------------------------------------------


class FakeWriter:
    def __init__(self) -> None:
        self.rows: list[list[str]] = []

    def writerow(self, row: list[str]) -> object:
        self.rows.append(list(row))
        return None


def test_run_prepends_file_date_and_covers_all_files(tmp_path: Path) -> None:
    write_response(response_path(tmp_path, "2026-08-13"), [make_record(docID="S1")])
    write_response(
        response_path(tmp_path, "2026-08-14"),
        [make_record(docID="S2"), make_record(docID="S3")],
    )
    fw = FakeWriter()
    skipped = m.run(tmp_path, fw)
    assert skipped == 0
    assert len(fw.rows) == 3
    assert all(len(r) == len(m.OUTPUT_HEADER) for r in fw.rows)
    assert [r[0] for r in fw.rows] == ["2026-08-13", "2026-08-14", "2026-08-14"]
    assert fw.rows[0][2] == "S1"  # file_date, seq_number, doc_id, ...


def test_run_skips_corrupt_json_without_aborting(tmp_path: Path) -> None:
    write_response(response_path(tmp_path, "2026-08-13"), [make_record(docID="OK")])
    bad = response_path(tmp_path, "2026-08-14")
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")

    fw = FakeWriter()
    skipped = m.run(tmp_path, fw)
    assert skipped == 1
    assert [r[2] for r in fw.rows] == ["OK"]


def test_iter_response_files_matches_only_document_list_json_sorted(tmp_path: Path) -> None:
    base = tmp_path / "edinet-dl" / "raw" / "response"
    write_response(response_path(tmp_path, "2026-08-14"), [])
    write_response(response_path(tmp_path, "2026-08-13"), [])
    (base / "2026" / "08" / "13" / "notes.txt").write_text("x", encoding="utf-8")

    found = list(m.iter_response_files(tmp_path))
    assert all(p.name == "document_list.json" for p in found)
    assert [m.file_date_from_path(p) for p in found] == ["2026-08-13", "2026-08-14"]
