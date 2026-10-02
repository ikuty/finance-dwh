"""load_ir_disclosure_kessan.py の単体テスト。

Stage1(ir_disclosure_kessan_pdf.extract_text)・Stage2(ir_disclosure_kessan_facts.run)
は既に個別のテストファイルで検証済みのため、ここではload_ir_disclosure_kessan.py
自身の責務(メタデータJSONからの対象PDF判別・日付選定・landingへのアトミック
書き込み)に絞って検証する(test_load_jpx_stq.pyと同じ方針)。extract_text/runは
monkeypatchで差し替える。
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import duckdb
import pytest

import ir_disclosure_kessan_pdf
from flows import load_ir_disclosure_kessan as m


def make_kessan_pdf(lake: Path, date: str, edinet_code: str, docid: str, disclosure_kind: str = "kessan_tanshin") -> Path:
    """レイクにPDF(中身はダミー)とメタデータJSONを置く(ir-disclosure-dlのbuild_pdf_metadata
    が実際に作る形式を模す)。"""
    yyyy, mm, dd = date.split("-")
    dest_dir = lake / "ir-disclosure-dl" / "raw" / yyyy / mm / dd / edinet_code
    dest_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = dest_dir / f"{docid}.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    meta = {
        "edinet_code": edinet_code,
        "sec_code": "13010",
        "company_name": "テスト株式会社",
        "disclosure_kind": disclosure_kind,
        "tdnet_event_date": date,
        "tdnet_kj_time": "15:00",
        "tdnet_title": "2027年５月期第１四半期決算短信〔日本基準〕（連結）",
        "jpx_disclosure_date": date.replace("-", "/"),
        "jpx_title": "2027年５月期第１四半期決算短信〔日本基準〕（連結）",
        "pdf_url": f"https://www2.jpx.co.jp/disc/13010/{docid}.pdf",
    }
    (dest_dir / f"{docid}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return pdf_path


def parquet_rows(path: Path) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        return con.execute(f"select * from read_parquet('{path.as_posix()}', hive_partitioning=false)").fetchall()
    finally:
        con.close()


def parquet_columns(path: Path) -> list[str]:
    con = duckdb.connect()
    try:
        return [
            d[0]
            for d in con.execute(
                f"select * from read_parquet('{path.as_posix()}', hive_partitioning=false) limit 0"
            ).description
        ]
    finally:
        con.close()


# --- disk_dates --------------------------------------------------------------------------


def test_disk_dates_requires_kessan_tanshin_metadata(tmp_path: Path) -> None:
    make_kessan_pdf(tmp_path, "2026-09-01", "E00012", "doc1")
    # forecast_revisionのみの日は対象外(決算短信本体のみ取り込む)
    make_kessan_pdf(tmp_path, "2026-09-02", "E00012", "doc2", disclosure_kind="forecast_revision")

    assert m.disk_dates(tmp_path) == {"2026-09-01"}


def test_disk_dates_empty_when_no_lake(tmp_path: Path) -> None:
    assert m.disk_dates(tmp_path) == set()


# --- landing_logged_dates / recent・backlog分割 (load_jpx_stqと同じロジックの流用確認) ---


def test_landing_logged_dates_excludes_empty_part_parquet(tmp_path: Path) -> None:
    corrupt_dir = tmp_path / "ir_disclosure_kessan_facts" / "file_date=2026-04-30"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "part.parquet").touch()

    assert m.landing_logged_dates(tmp_path) == set()


def test_recent_and_backlog_dates_to_load_partition_disk_dates(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    for d in ("2024-01-05", "2026-09-07", "2026-09-08", "2026-09-10"):
        make_kessan_pdf(lake, d, "E00012", f"doc_{d}")
    done_dir = landing / "ir_disclosure_kessan_facts" / "file_date=2024-01-05"
    done_dir.mkdir(parents=True)
    (done_dir / "part.parquet").write_bytes(b"x")

    today = datetime.date(2026, 9, 10)
    recent = m.recent_dates_to_load(lake, landing, today, lookback=3)
    backlog = m.backlog_dates_to_load(lake, landing, today, lookback=3)

    assert recent == ["2026-09-07", "2026-09-08", "2026-09-10"]
    assert backlog == []  # 2024-01-05は取り込み済み


# --- load_one(ir_disclosure_kessan_pdf.extract_text / ir_disclosure_kessan_facts.run を monkeypatch) -


def test_load_one_writes_text_and_facts_with_loaded_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    pdf_path = make_kessan_pdf(lake, "2026-09-10", "E00012", "docABC")

    def fake_extract_text(path: Path) -> str:
        assert path == pdf_path
        return "ダミー本文"

    monkeypatch.setattr(ir_disclosure_kessan_pdf, "extract_text", fake_extract_text)

    n = m.load_one(lake, landing, "2026-09-10")

    assert n == 1
    text_out = landing / "ir_disclosure_kessan_text" / "file_date=2026-09-10" / "part.parquet"
    facts_out = landing / "ir_disclosure_kessan_facts" / "file_date=2026-09-10" / "part.parquet"
    assert text_out.exists() and not text_out.with_suffix(".parquet.tmp").exists()
    assert facts_out.exists() and not facts_out.with_suffix(".parquet.tmp").exists()

    text_rows = parquet_rows(text_out)
    assert len(text_rows) == 1
    text_columns = parquet_columns(text_out)
    text_row = dict(zip(text_columns, text_rows[0], strict=True))
    assert text_row["docid"] == "docABC"
    assert text_row["text"] == "ダミー本文"
    assert text_row["file_date"] == "2026-09-10"

    facts_rows = parquet_rows(facts_out)
    assert len(facts_rows) == 1
    facts_columns = parquet_columns(facts_out)
    facts_row = dict(zip(facts_columns, facts_rows[0], strict=True))
    assert facts_row["docid"] == "docABC"
    assert facts_row["file_date"] == "2026-09-10"
    assert facts_row["period_type"] == "q1"
    # メタデータJSONのedinet_code/sec_codeがFactRowまで素通しされること
    # (2026-10-03追加、EDINET由来のmartと企業単位で結合するためのキー)。
    assert facts_row["edinet_code"] == "E00012"
    assert facts_row["sec_code"] == "13010"
    assert facts_row["period_end"] == "2026-08-31"
    assert facts_row["_loaded_at"] is not None
    datetime.datetime.fromisoformat(str(facts_row["_loaded_at"]))


def test_load_one_no_kessan_pdfs_produces_zero_row_parquets(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    (lake / "ir-disclosure-dl" / "raw" / "2026" / "09" / "11").mkdir(parents=True)

    n = m.load_one(lake, landing, "2026-09-11")

    assert n == 0
    facts_out = landing / "ir_disclosure_kessan_facts" / "file_date=2026-09-11" / "part.parquet"
    assert facts_out.exists()
    assert parquet_rows(facts_out) == []
    assert m.landing_logged_dates(landing) == {"2026-09-11"}


def test_load_one_routes_correction_title_via_real_classify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # extract_textだけmonkeypatchし、ir_disclosure_kessan_facts.runは実物を使って、
    # 分類ロジック(訂正等)がload_one経由でも正しく動くことを確認する(回帰防止)。
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    dest_dir = lake / "ir-disclosure-dl" / "raw" / "2026" / "09" / "12" / "E00099"
    dest_dir.mkdir(parents=True)
    (dest_dir / "docXYZ.pdf").write_bytes(b"%PDF-fake")
    meta = {
        "edinet_code": "E00099", "sec_code": "10000", "company_name": "テスト株式会社",
        "disclosure_kind": "kessan_tanshin", "tdnet_event_date": "2026-09-12",
        "tdnet_kj_time": "15:00",
        "tdnet_title": "（訂正）「2026年６月期 決算短信〔日本基準〕(連結)」の一部訂正について",
        "jpx_disclosure_date": "2026/09/12",
        "jpx_title": "（訂正）「2026年６月期 決算短信〔日本基準〕(連結)」の一部訂正について",
        "pdf_url": "https://www2.jpx.co.jp/disc/10000/docXYZ.pdf",
    }
    (dest_dir / "docXYZ.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ir_disclosure_kessan_pdf, "extract_text", lambda path: "自由記述の訂正notice本文")

    n = m.load_one(lake, landing, "2026-09-12")

    assert n == 1
    facts_out = landing / "ir_disclosure_kessan_facts" / "file_date=2026-09-12" / "part.parquet"
    facts_columns = parquet_columns(facts_out)
    facts_row = dict(zip(facts_columns, parquet_rows(facts_out)[0], strict=True))
    assert facts_row["extraction_status"] == "correction"
    assert facts_row["sales"] is None
