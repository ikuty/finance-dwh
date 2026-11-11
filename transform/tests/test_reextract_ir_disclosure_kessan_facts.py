"""reextract_ir_disclosure_kessan_facts.py の単体テスト(一時ディレクトリ上の小さなlanding/lake)。"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import ir_disclosure_kessan_facts as facts_stage
import pyarrow as pa
from flows import reextract_ir_disclosure_kessan_facts as m

TEXT = """
１．2027年５月期第１四半期の連結業績
2027年５月期第１四半期 6,334 5.5 585 0.5 600 0.4 400 0.3
２．配当の状況
年間配当金
2026年５月期 － 38.00 － 39.00 77.00
2027年５月期 －
2027年５月期（予想） 42.00 － 42.00 84.00
３．2027年５月期の連結業績予想
"""
TITLE = "2027年５月期第１四半期決算短信〔日本基準〕(連結)"
DATE = "2026-08-07"


def _write(path: Path, cols: dict[str, list[str | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tbl = pa.table({c: pa.array(v, type=pa.string()) for c, v in cols.items()})
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * FROM tbl) TO '{path.as_posix()}' (FORMAT PARQUET)")
    con.close()


def _old_facts(docids: list[str]) -> dict[str, list[str | None]]:
    """配当列追加前のスキーマ(FACT_COLUMNSから配当列を除いたもの)のfacts。"""
    rows = [
        facts_stage.run(TEXT, d, TITLE, edinet_code="E00001", sec_code="12340") for d in docids
    ]
    cols = [c for c in facts_stage.FACT_COLUMNS if not c.startswith("dividend_")]
    data: dict[str, list[str | None]] = {c: [getattr(r, c) for r in rows] for c in cols}
    data["file_date"] = [DATE] * len(rows)
    data["_loaded_at"] = ["2026-08-07T00:00:00+00:00"] * len(rows)
    return data


def _setup(tmp_path: Path, docids: list[str], with_meta: list[str]) -> tuple[Path, Path]:
    landing, lake = tmp_path / "landing", tmp_path / "lake"
    _write(landing / "ir_disclosure_kessan_facts" / f"file_date={DATE}" / "part.parquet", _old_facts(docids))
    _write(
        landing / "ir_disclosure_kessan_text" / f"file_date={DATE}" / "part.parquet",
        {"file_date": [DATE] * len(docids), "docid": [*docids], "text": [TEXT] * len(docids)},
    )
    day = lake / "ir-disclosure-dl" / "raw" / "2026" / "08" / "07" / "E00001"
    day.mkdir(parents=True)
    for d in with_meta:
        (day / f"{d}.json").write_text(
            json.dumps({"jpx_title": TITLE, "edinet_code": "E00001", "sec_code": "12340"}), encoding="utf-8"
        )
    return landing, lake


def _read(landing: Path) -> list[dict[str, str | None]]:
    return m._read_parquet(landing / "ir_disclosure_kessan_facts" / f"file_date={DATE}" / "part.parquet")


def test_reextract_fills_dividend_columns_and_keeps_other_columns(tmp_path: Path) -> None:
    landing, lake = _setup(tmp_path, ["d1", "d2"], ["d1", "d2"])
    before = {r["docid"]: r for r in _read(landing)}
    stats = m.reextract_date(landing, lake, DATE)
    after = {r["docid"]: r for r in _read(landing)}
    assert stats["docs"] == 2 and stats["with_dividend"] == 2 and stats["written_dates"] == 1
    assert stats["changed_docs"] == 0  # 配当列以外は変わらない
    for d in ("d1", "d2"):
        assert after[d]["dividend_prior_total"] == "77.00"
        assert after[d]["dividend_fcst_total"] == "84.00"
        assert after[d]["_loaded_at"] == before[d]["_loaded_at"]
        assert after[d]["sales"] == before[d]["sales"]
        assert after[d]["file_date"] == DATE


def test_reextract_is_idempotent(tmp_path: Path) -> None:
    landing, lake = _setup(tmp_path, ["d1"], ["d1"])
    m.reextract_date(landing, lake, DATE)
    stats = m.reextract_date(landing, lake, DATE)
    assert stats["skipped_dates"] == 1 and stats["written_dates"] == 0


def test_reextract_dry_run_does_not_write(tmp_path: Path) -> None:
    landing, lake = _setup(tmp_path, ["d1"], ["d1"])
    stats = m.reextract_date(landing, lake, DATE, dry_run=True)
    assert stats["with_dividend"] == 1 and stats["written_dates"] == 0
    assert "dividend_prior_total" not in _read(landing)[0]


def test_reextract_keeps_old_row_when_metadata_is_missing(tmp_path: Path) -> None:
    landing, lake = _setup(tmp_path, ["d1", "d2"], ["d1"])
    stats = m.reextract_date(landing, lake, DATE)
    rows = {r["docid"]: r for r in _read(landing)}
    assert stats["no_meta_or_text"] == 1
    assert rows["d1"]["dividend_prior_total"] == "77.00"
    assert rows["d2"]["dividend_prior_total"] is None
    assert rows["d2"]["sales"] == "6334"  # 既存の行は残る


def test_reextract_reports_changed_non_dividend_columns(tmp_path: Path) -> None:
    landing, lake = _setup(tmp_path, ["d1"], ["d1"])
    path = landing / "ir_disclosure_kessan_facts" / f"file_date={DATE}" / "part.parquet"
    data = _old_facts(["d1"])
    data["sales"] = ["999"]  # 再抽出の結果と食い違う古い値
    _write(path, data)
    stats = m.reextract_date(landing, lake, DATE)
    assert stats["changed_docs"] == 1 and stats["changed:sales"] == 1


def test_reextract_force_rewrites_partitions_that_already_have_dividend_columns(tmp_path: Path) -> None:
    landing, lake = _setup(tmp_path, ["d1"], ["d1"])
    m.reextract_date(landing, lake, DATE)
    path = landing / "ir_disclosure_kessan_facts" / f"file_date={DATE}" / "part.parquet"
    rows = _read(landing)
    rows[0]["dividend_prior_total"] = "1.00"  # 古い抽出器の誤った値
    _write(path, {c: [rows[0][c]] for c in rows[0]})
    assert m.reextract_date(landing, lake, DATE)["skipped_dates"] == 1
    stats = m.reextract_date(landing, lake, DATE, force=True)
    assert stats["changed:dividend_prior_total"] == 1 and stats["written_dates"] == 1
    assert _read(landing)[0]["dividend_prior_total"] == "77.00"
