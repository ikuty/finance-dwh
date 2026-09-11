"""load_edinet.py（DuckDB 版）の単体テスト。DuckDB は組み込みなので実物を使う
（モック不要）。合成の .csv.gz フィクスチャでレイク→Parquet の一連を検証する。"""

from __future__ import annotations

import csv
import datetime
import gzip
import io
from pathlib import Path

import duckdb
import pytest

from flows import load_edinet as m


# --- フィクスチャ生成ヘルパー（postgres/fdw/tests の edinet_csv_fdw 版と同型） -----------


def write_csv_gz(path: Path, data_rows: list[list[str]]) -> None:
    header = [
        "要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別",
        "期間・時点", "ユニットID", "単位", "値",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", quotechar='"', quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(header)
    for row in data_rows:
        writer.writerow(row)
    raw = ("﻿" + buf.getvalue()).encode("utf-16-le")
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        fh.write(raw)


def csv_path(lake: Path, date: str, ec: str, doc: str, name: str) -> Path:
    yyyy, mm, dd = date.split("-")
    return lake / "edinet-dl" / "raw" / yyyy / mm / dd / ec / "csv" / doc / f"{name}.csv.gz"


def make_day_dir(lake: Path, date: str) -> None:
    yyyy, mm, dd = date.split("-")
    (lake / "edinet-dl" / "raw" / yyyy / mm / dd).mkdir(parents=True, exist_ok=True)


def parquet_rows(path: Path) -> list[tuple[object, ...]]:
    """part.parquet を読んで生の行を返す。

    hive_partitioning=false が必須: パスに `file_date=YYYY-MM-DD` を含むため、
    DuckDB は単一ファイル指定でも Hive パーティショニングを自動検出し、ファイル内の
    file_date 列（VARCHAR で書いてある）を DATE 型の値で上書きしてしまう
    （実機で確認、load_edinet.py の書き込み側に問題は無い）。
    """
    con = duckdb.connect()
    try:
        return con.execute(
            f"select * from read_parquet('{path.as_posix()}', hive_partitioning=false)"
        ).fetchall()
    finally:
        con.close()


# --- disk_dates / recent_dates ----------------------------------------------


def test_disk_dates_lists_only_date_dirs(tmp_path: Path) -> None:
    make_day_dir(tmp_path, "2026-09-01")
    make_day_dir(tmp_path, "2026-09-02")
    make_day_dir(tmp_path, "2025-06-10")
    (tmp_path / "edinet-dl" / "raw" / "response" / "2026" / "09" / "01").mkdir(parents=True)
    (tmp_path / "edinet-dl" / "raw" / "2026" / "09" / "notaday").mkdir(parents=True)

    assert m.disk_dates(tmp_path) == {"2026-09-01", "2026-09-02", "2025-06-10"}


def test_disk_dates_empty_when_no_lake(tmp_path: Path) -> None:
    assert m.disk_dates(tmp_path) == set()


def test_recent_dates_is_inclusive_window() -> None:
    got = m.recent_dates(datetime.date(2026, 9, 10), 3)
    assert got == {"2026-09-10", "2026-09-09", "2026-09-08", "2026-09-07"}


# --- landing_logged_dates ----------------------------------------------------


def test_landing_logged_dates_requires_part_parquet(tmp_path: Path) -> None:
    done_dir = tmp_path / "edinet_csv_facts" / "file_date=2026-09-01"
    done_dir.mkdir(parents=True)
    (done_dir / "part.parquet").write_bytes(b"x")
    # 書きかけ（tmp のみ）は「未完了」扱い
    partial_dir = tmp_path / "edinet_csv_facts" / "file_date=2026-09-02"
    partial_dir.mkdir(parents=True)
    (partial_dir / "part.parquet.tmp").write_bytes(b"x")

    assert m.landing_logged_dates(tmp_path) == {"2026-09-01"}


def test_landing_logged_dates_empty_when_no_landing(tmp_path: Path) -> None:
    assert m.landing_logged_dates(tmp_path) == set()


# --- dates_to_load -----------------------------------------------------------


def test_dates_to_load_unions_backfill_and_lookback(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    for d in ("2024-01-05", "2026-09-07", "2026-09-08", "2026-09-10"):
        make_day_dir(lake, d)
    for d in ("2024-01-05", "2026-09-07"):
        done_dir = landing / "edinet_csv_facts" / f"file_date={d}"
        done_dir.mkdir(parents=True)
        (done_dir / "part.parquet").write_bytes(b"x")

    got = m.dates_to_load(lake, landing, datetime.date(2026, 9, 10), lookback=3)

    # backfill 未取り込み: 2026-09-08, 2026-09-10
    # lookback(09-07..09-10) ∩ disk: 09-07, 09-08, 09-10 → 09-07 も再取り込み対象
    assert got == ["2026-09-07", "2026-09-08", "2026-09-10"]


# --- load_one（実 DuckDB） ----------------------------------------------------


def test_load_one_writes_parquet_with_all_rows_and_long_value(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    long_value = "あ" * 30_000  # DuckDB の CSV 再パースだと失敗した長大フィールドを模す
    write_csv_gz(
        csv_path(lake, "2026-09-10", "E00011", "S1", "a"),
        [
            ["e1", "n1", "c1", "y1", "連結", "期間", "JPY", "円", "1000"],
            ["e2", "n2", "c2", "y2", "その他", "時点", "pure", "", long_value],
        ],
    )
    write_csv_gz(
        csv_path(lake, "2026-09-10", "E00022", "S2", "b"),
        [["e3", "n3", "c3", "y3", "個別", "期間", "JPY", "円", "3000"]],
    )

    n = m.load_one(lake, landing, "2026-09-10")

    assert n == 3
    out = landing / "edinet_csv_facts" / "file_date=2026-09-10" / "part.parquet"
    assert out.exists()
    assert not out.with_suffix(".parquet.tmp").exists()
    rows = parquet_rows(out)
    assert len(rows) == 3
    values = [str(r[-1]) for r in rows]
    by_value_prefix = {v[:10]: r for v, r in zip(values, rows)}
    assert by_value_prefix["1000"][:3] == ("2026-09-10", "E00011", "S1")
    long_row = next((r, v) for r, v in zip(rows, values) if len(v) == 30_000)
    assert long_row[1] == long_value  # 長大フィールドが欠損・切断なく往復する


def test_load_one_empty_date_produces_zero_row_parquet(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    make_day_dir(lake, "2026-09-11")  # ディレクトリはあるが csv.gz なし（休日等）

    n = m.load_one(lake, landing, "2026-09-11")

    assert n == 0
    out = landing / "edinet_csv_facts" / "file_date=2026-09-11" / "part.parquet"
    assert out.exists()
    assert parquet_rows(out) == []
    # 0 行でも「取り込み済み」として記録される（毎晩の再走査を避ける）
    assert m.landing_logged_dates(landing) == {"2026-09-11"}


def test_load_one_skips_corrupt_gz_without_aborting(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lake = tmp_path / "lake"
    landing = tmp_path / "landing"
    write_csv_gz(
        csv_path(lake, "2026-09-10", "E00011", "S1", "good"),
        [["e1", "n1", "c1", "y1", "連結", "期間", "JPY", "円", "1"]],
    )
    bad = csv_path(lake, "2026-09-10", "E00011", "S1", "bad")
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not gzip")

    n = m.load_one(lake, landing, "2026-09-10")

    assert n == 1
    assert "読み飛ばした" in capsys.readouterr().err
