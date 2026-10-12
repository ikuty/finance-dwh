"""load_ir_disclosure_reit.py の単体テスト。test_load_ir_disclosure_kessan.pyと
同じ方針(メタデータJSONからの対象判別・日付選定・landingへのアトミック書き込みに
絞る)。ir_disclosure_reit_ixbrl.parse_facts自身は別ファイルで検証済み。
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import duckdb

from flows import load_ir_disclosure_reit as m

_SAMPLE_IXBRL = (
    '<ix:nonFraction name="tse-re-t:NetIncomePerUnitREIT" '
    'contextRef="CurrentYearDuration_NonConsolidatedMember_ResultMember" '
    'unitRef="JPYPerUnits" decimals="0" scale="0" format="ixt:numdotdecimal">2,847</ix:nonFraction>'
    '<ix:nonFraction name="tse-re-t:NetAssets" '
    'contextRef="CurrentYearInstant_NonConsolidatedMember_ResultMember" '
    'unitRef="JPY" decimals="-6" scale="6" format="ixt:numdotdecimal">90,438</ix:nonFraction>'
)


def make_reit_ixbrl(
    lake: Path, date: str, edinet_code: str, pdf_docid: str, ixbrl_docid: str,
    ixbrl_html: str = _SAMPLE_IXBRL,
) -> Path:
    """レイクにメタデータJSON+iXBRL HTMLを置く(ir-disclosure-dl側の実際の着地形式を
    模す: PDFのdocidとiXBRLのdocidはJPX側の別番号体系で異なる)。"""
    yyyy, mm, dd = date.split("-")
    dest_dir = lake / "ir-disclosure-dl" / "raw" / yyyy / mm / dd / edinet_code
    dest_dir.mkdir(parents=True, exist_ok=True)
    ixbrl_filename = f"{ixbrl_docid}_tse-anrejpsm-sample-ixbrl.htm"
    (dest_dir / ixbrl_filename).write_text(ixbrl_html, encoding="utf-8")
    meta = {
        "edinet_code": edinet_code,
        "sec_code": "32690",
        "company_name": "テスト投資法人",
        "disclosure_kind": "kessan_tanshin",
        "jpx_title": "2026年07月期決算短信（REIT）",
        "pdf_url": f"https://www2.jpx.co.jp/disc/32690/{pdf_docid}.pdf",
        "ixbrl_url": f"https://www2.jpx.co.jp/disc/32690/{ixbrl_filename}",
    }
    json_path = dest_dir / f"{pdf_docid}.json"
    json_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return json_path


def make_non_reit_pdf(lake: Path, date: str, edinet_code: str, docid: str) -> None:
    yyyy, mm, dd = date.split("-")
    dest_dir = lake / "ir-disclosure-dl" / "raw" / yyyy / mm / dd / edinet_code
    dest_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "edinet_code": edinet_code,
        "sec_code": "13010",
        "disclosure_kind": "kessan_tanshin",
        "jpx_title": "2027年５月期決算短信〔日本基準〕（連結）",
        "pdf_url": f"https://www2.jpx.co.jp/disc/13010/{docid}.pdf",
        "ixbrl_url": None,
    }
    (dest_dir / f"{docid}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


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


# --- disk_dates / _is_reit_ixbrl_json -----------------------------------------------------


def test_disk_dates_requires_ixbrl_url(tmp_path: Path) -> None:
    make_reit_ixbrl(tmp_path, "2026-09-17", "E24347", "pdf1", "ixbrl1")
    make_non_reit_pdf(tmp_path, "2026-09-18", "E00012", "doc2")

    assert m.disk_dates(tmp_path) == {"2026-09-17"}


def test_is_reit_ixbrl_json_rejects_non_reit_metadata(tmp_path: Path) -> None:
    make_non_reit_pdf(tmp_path, "2026-09-18", "E00012", "doc2")
    json_path = tmp_path / "ir-disclosure-dl" / "raw" / "2026" / "09" / "18" / "E00012" / "doc2.json"

    assert m._is_reit_ixbrl_json(json_path) is False


def test_disk_dates_empty_when_no_lake(tmp_path: Path) -> None:
    assert m.disk_dates(tmp_path) == set()


# --- landing_logged_dates / recent・backlog分割 ---------------------------------------------


def test_landing_logged_dates_excludes_empty_part_parquet(tmp_path: Path) -> None:
    corrupt_dir = tmp_path / "ir_disclosure_reit_ixbrl_facts" / "file_date=2026-04-30"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "part.parquet").touch()

    assert m.landing_logged_dates(tmp_path) == set()


def test_recent_and_backlog_dates_to_load_partition_disk_dates(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    for i, d in enumerate(("2024-01-05", "2026-09-07", "2026-09-08", "2026-09-10")):
        make_reit_ixbrl(lake, d, "E24347", f"pdf_{i}", f"ixbrl_{i}")
    done_dir = landing / "ir_disclosure_reit_ixbrl_facts" / "file_date=2024-01-05"
    done_dir.mkdir(parents=True)
    (done_dir / "part.parquet").write_bytes(b"x")

    today = datetime.date(2026, 9, 10)
    recent = m.recent_dates_to_load(lake, landing, today, lookback=3)
    backlog = m.backlog_dates_to_load(lake, landing, today, lookback=3)

    assert recent == ["2026-09-07", "2026-09-08", "2026-09-10"]
    assert backlog == []  # 2024-01-05は取り込み済み


# --- load_one ------------------------------------------------------------------------------


def test_load_one_writes_melted_facts(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    make_reit_ixbrl(lake, "2026-09-17", "E24347", "pdf1", "ixbrl1")

    n = m.load_one(lake, landing, "2026-09-17")

    assert n == 1
    out = landing / "ir_disclosure_reit_ixbrl_facts" / "file_date=2026-09-17" / "part.parquet"
    assert out.exists() and not out.with_suffix(".parquet.tmp").exists()

    rows = parquet_rows(out)
    columns = parquet_columns(out)
    assert len(rows) == 2  # _SAMPLE_IXBRLの2要素分
    by_element = {dict(zip(columns, r, strict=True))["element_id"]: dict(zip(columns, r, strict=True)) for r in rows}

    net_income = by_element["tse-re-t:NetIncomePerUnitREIT"]
    assert net_income["edinet_code"] == "E24347"
    assert net_income["sec_code"] == "32690"
    assert net_income["file_date"] == "2026-09-17"
    assert net_income["context_id"] == "CurrentYearDuration_NonConsolidatedMember_ResultMember"
    assert float(str(net_income["value_num"])) == 2847.0
    # make_reit_ixbrlのjpx_title「2026年07月期決算短信（REIT）」から算出(annual固定)。
    assert net_income["period_end"] == "2026-07-31"

    net_assets = by_element["tse-re-t:NetAssets"]
    assert float(str(net_assets["value_num"])) == 90_438_000_000.0


def test_load_one_no_reit_docs_produces_zero_row_parquet(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    (lake / "ir-disclosure-dl" / "raw" / "2026" / "09" / "18").mkdir(parents=True)

    n = m.load_one(lake, landing, "2026-09-18")

    assert n == 0
    out = landing / "ir_disclosure_reit_ixbrl_facts" / "file_date=2026-09-18" / "part.parquet"
    assert parquet_rows(out) == []


def test_load_one_skips_non_reit_metadata_in_same_day(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    make_reit_ixbrl(lake, "2026-09-17", "E24347", "pdf1", "ixbrl1")
    make_non_reit_pdf(lake, "2026-09-17", "E00012", "doc2")

    n = m.load_one(lake, landing, "2026-09-17")

    assert n == 1  # 事業会社向け(ixbrl_url無し)はカウントされない
