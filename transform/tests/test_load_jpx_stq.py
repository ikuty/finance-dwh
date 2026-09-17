"""load_jpx_stq.py の単体テスト。

`jpx_stq_pdf.iter_words`(PDF抽出、Stage1)は既に test_jpx_stq_pdf.py で、
`jpx_stq_facts.run`(構造化、Stage2)は既に test_jpx_stq_facts.py で個別に
検証済みのため、ここでは load_jpx_stq.py 自身の責務(日付選定・landingへの
アトミック書き込み・_loaded_at付与)に絞って検証する。`jpx_stq_pdf.iter_words`
は monkeypatch で差し替える(test_notify.py で boto3.client / urlopen を
差し替えているのと同じ、外部境界のモック方針)。
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

import jpx_stq_pdf
from flows import load_jpx_stq as m


def day_pdf_path(lake: Path, date: str) -> Path:
    yyyy, mm, dd = date.split("-")
    return lake / "jpx-daily-pdf-dl" / "raw" / "detailed-daily" / yyyy / mm / dd / "stq.pdf"


def make_day_pdf(lake: Path, date: str) -> None:
    """レイクにファイル(中身は問わない、存在確認のみ)を置く。"""
    path = day_pdf_path(lake, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"dummy")


def sample_words(page: int, code: str, unit_name: str) -> list[jpx_stq_pdf.Word]:
    return [
        jpx_stq_pdf.Word(page=page, top=73.6, x0=1068.0, x1=1087.0, size=10.0, text="1-1"),
        jpx_stq_pdf.Word(page=page, top=407.9, x0=74.0, x1=100.0, size=6.0, text=code),
        jpx_stq_pdf.Word(page=page, top=407.9, x0=140.7, x1=200.0, size=6.0, text=unit_name),
        jpx_stq_pdf.Word(page=page, top=407.9, x0=316.2, x1=340.0, size=6.0, text="4,610.00"),
        jpx_stq_pdf.Word(page=page, top=407.9, x0=1079.9, x1=1120.0, size=6.0, text="124,710.500"),
        jpx_stq_pdf.Word(page=page, top=407.9, x0=1024.1, x1=1040.0, size=6.0, text="27.0"),
    ]


def parquet_rows(path: Path) -> list[tuple[object, ...]]:
    """hive_partitioning=falseが必須(load_edinet.pyのテストと同じ理由)。"""
    con = duckdb.connect()
    try:
        return con.execute(f"select * from read_parquet('{path.as_posix()}', hive_partitioning=false)").fetchall()
    finally:
        con.close()


# --- disk_dates / landing_logged_dates / dates_to_load ----------------------


def test_disk_dates_requires_stq_pdf(tmp_path: Path) -> None:
    make_day_pdf(tmp_path, "2026-09-01")
    # ディレクトリだけあって stq.pdf が無い日(休日等)は対象外
    (tmp_path / "jpx-daily-pdf-dl" / "raw" / "detailed-daily" / "2026" / "09" / "02").mkdir(parents=True)

    assert m.disk_dates(tmp_path) == {"2026-09-01"}


def test_disk_dates_empty_when_no_lake(tmp_path: Path) -> None:
    assert m.disk_dates(tmp_path) == set()


def test_landing_logged_dates_requires_facts_part_parquet(tmp_path: Path) -> None:
    done_dir = tmp_path / "jpx_stq_facts" / "file_date=2026-09-01"
    done_dir.mkdir(parents=True)
    (done_dir / "part.parquet").write_bytes(b"x")
    partial_dir = tmp_path / "jpx_stq_facts" / "file_date=2026-09-02"
    partial_dir.mkdir(parents=True)
    (partial_dir / "part.parquet.tmp").write_bytes(b"x")

    assert m.landing_logged_dates(tmp_path) == {"2026-09-01"}


def test_landing_logged_dates_excludes_empty_part_parquet(tmp_path: Path) -> None:
    """電源断でrenameだけ完了し中身が定着しなかった0バイトファイルは
    「取り込み済み」と見なさない(2026-09-17実機で発生・確認済み)。"""
    corrupt_dir = tmp_path / "jpx_stq_facts" / "file_date=2026-04-30"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "part.parquet").touch()

    assert m.landing_logged_dates(tmp_path) == set()


def test_dates_to_load_unions_backfill_and_lookback(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    for d in ("2024-01-05", "2026-09-07", "2026-09-08", "2026-09-10"):
        make_day_pdf(lake, d)
    for d in ("2024-01-05", "2026-09-07"):
        done_dir = landing / "jpx_stq_facts" / f"file_date={d}"
        done_dir.mkdir(parents=True)
        (done_dir / "part.parquet").write_bytes(b"x")

    got = m.dates_to_load(lake, landing, datetime.date(2026, 9, 10), lookback=3)

    assert got == ["2026-09-07", "2026-09-08", "2026-09-10"]


def test_recent_dates_to_load_excludes_backlog(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    for d in ("2024-01-05", "2026-09-07", "2026-09-08", "2026-09-10"):
        make_day_pdf(lake, d)
    for d in ("2024-01-05", "2026-09-07"):
        done_dir = landing / "jpx_stq_facts" / f"file_date={d}"
        done_dir.mkdir(parents=True)
        (done_dir / "part.parquet").write_bytes(b"x")

    got = m.recent_dates_to_load(lake, landing, datetime.date(2026, 9, 10), lookback=3)

    # 2024-01-05はlookback窓の外(バックログ)なので含まない
    assert got == ["2026-09-08", "2026-09-10"]


def test_backlog_dates_to_load_excludes_recent_window(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    for d in ("2024-01-05", "2026-09-07", "2026-09-08", "2026-09-10"):
        make_day_pdf(lake, d)
    for d in ("2026-09-07",):
        done_dir = landing / "jpx_stq_facts" / f"file_date={d}"
        done_dir.mkdir(parents=True)
        (done_dir / "part.parquet").write_bytes(b"x")

    got = m.backlog_dates_to_load(lake, landing, datetime.date(2026, 9, 10), lookback=3)

    # 2026-09-08/10はlookback窓の内側(保証枠)なので含まない
    assert got == ["2024-01-05"]


def test_recent_and_backlog_dates_to_load_partition_dates_to_load(tmp_path: Path) -> None:
    # 2つに分割しても、合わせればdates_to_load全体(過不足・重複なし)と一致する
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    for d in ("2024-01-05", "2026-09-07", "2026-09-08", "2026-09-10"):
        make_day_pdf(lake, d)
    for d in ("2024-01-05",):
        done_dir = landing / "jpx_stq_facts" / f"file_date={d}"
        done_dir.mkdir(parents=True)
        (done_dir / "part.parquet").write_bytes(b"x")

    today = datetime.date(2026, 9, 10)
    whole = m.dates_to_load(lake, landing, today, lookback=3)
    recent = m.recent_dates_to_load(lake, landing, today, lookback=3)
    backlog = m.backlog_dates_to_load(lake, landing, today, lookback=3)

    assert sorted(recent + backlog) == whole
    assert set(recent).isdisjoint(backlog)


# --- load_one(jpx_stq_pdf.iter_words を monkeypatch) -------------------------


def test_load_one_writes_words_and_facts_with_loaded_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    make_day_pdf(lake, "2026-09-10")
    words = sample_words(page=1, code="1301", unit_name="100極洋")

    def fake_iter_words(pdf_path: Path) -> Iterator[jpx_stq_pdf.Word]:
        assert pdf_path == day_pdf_path(lake, "2026-09-10")
        yield from words

    monkeypatch.setattr(jpx_stq_pdf, "iter_words", fake_iter_words)

    n = m.load_one(lake, landing, "2026-09-10")

    assert n == 1
    words_out = landing / "jpx_stq_words" / "file_date=2026-09-10" / "part.parquet"
    facts_out = landing / "jpx_stq_facts" / "file_date=2026-09-10" / "part.parquet"
    assert words_out.exists() and not words_out.with_suffix(".parquet.tmp").exists()
    assert facts_out.exists() and not facts_out.with_suffix(".parquet.tmp").exists()

    words_rows = parquet_rows(words_out)
    assert len(words_rows) == len(words)

    facts_rows = parquet_rows(facts_out)
    assert len(facts_rows) == 1
    columns = [d[0] for d in duckdb.connect().execute(
        f"select * from read_parquet('{facts_out.as_posix()}', hive_partitioning=false) limit 0"
    ).description]
    row = dict(zip(columns, facts_rows[0], strict=True))
    assert row["code"] == "1301"
    assert row["file_date"] == "2026-09-10"
    assert row["trading_volume"] == "27.0"
    assert row["_loaded_at"] is not None
    # ISO8601形式で、正常にパースできること
    datetime.datetime.fromisoformat(str(row["_loaded_at"]))


def test_load_one_empty_pdf_produces_zero_row_facts_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    make_day_pdf(lake, "2026-09-11")

    def fake_iter_words(pdf_path: Path) -> Iterator[jpx_stq_pdf.Word]:
        return iter(())

    monkeypatch.setattr(jpx_stq_pdf, "iter_words", fake_iter_words)

    n = m.load_one(lake, landing, "2026-09-11")

    assert n == 0
    facts_out = landing / "jpx_stq_facts" / "file_date=2026-09-11" / "part.parquet"
    assert facts_out.exists()
    assert parquet_rows(facts_out) == []
    assert m.landing_logged_dates(landing) == {"2026-09-11"}
