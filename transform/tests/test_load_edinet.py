"""load_edinet.py の単体テスト。subprocess / DB は Fake で差し替える。"""

from __future__ import annotations

import datetime
import io
from pathlib import Path
from typing import Any

import pytest

from flows import load_edinet as m


# --- disk_dates / recent_dates -----------------------------------------------


def make_day_dir(lake: Path, date: str) -> None:
    yyyy, mm, dd = date.split("-")
    (lake / "edinet-dl" / "raw" / yyyy / mm / dd).mkdir(parents=True, exist_ok=True)


def test_disk_dates_lists_only_date_dirs(tmp_path: Path) -> None:
    make_day_dir(tmp_path, "2026-09-01")
    make_day_dir(tmp_path, "2026-09-02")
    make_day_dir(tmp_path, "2025-06-10")
    # 紛れ込み: response ディレクトリと非数値ディレクトリ
    (tmp_path / "edinet-dl" / "raw" / "response" / "2026" / "09" / "01").mkdir(parents=True)
    (tmp_path / "edinet-dl" / "raw" / "2026" / "09" / "notaday").mkdir(parents=True)

    assert m.disk_dates(tmp_path) == {"2026-09-01", "2026-09-02", "2025-06-10"}


def test_disk_dates_empty_when_no_lake(tmp_path: Path) -> None:
    assert m.disk_dates(tmp_path) == set()


def test_recent_dates_is_inclusive_window() -> None:
    got = m.recent_dates(datetime.date(2026, 9, 10), 3)
    assert got == {"2026-09-10", "2026-09-09", "2026-09-08", "2026-09-07"}


# --- dates_to_load ---------------------------------------------------------


class FakeCursor:
    def __init__(self, log_rows: list[tuple[object, ...]]) -> None:
        self._log_rows = log_rows
        self.rowcount = 0
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.copies: list[str] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        self.calls.append((sql, params))

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._log_rows

    def copy_expert(self, sql: str, file: object) -> None:
        self.copies.append(sql)
        self.rowcount = len(file.read().splitlines())  # type: ignore[attr-defined]


class FakeConn:
    def __init__(self, cur: FakeCursor) -> None:
        self._cur = cur
        self.autocommit = False
        self.commits = 0

    def cursor(self) -> FakeCursor:
        return self._cur

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *exc: object) -> None:
        self.commits += 1

    def close(self) -> None:
        return None


def test_dates_to_load_unions_backfill_and_lookback(tmp_path: Path) -> None:
    for d in ("2024-01-05", "2026-09-07", "2026-09-08", "2026-09-10"):
        make_day_dir(tmp_path, d)
    # ログ済み: 2024-01-05 と 2026-09-07
    conn = FakeConn(FakeCursor([("2024-01-05",), ("2026-09-07",)]))

    got = m.dates_to_load(conn, tmp_path, datetime.date(2026, 9, 10), lookback=3)

    # backfill 未ログ: 2026-09-08, 2026-09-10
    # lookback(09-07..09-10) ∩ disk: 09-07, 09-08, 09-10  → 09-07 も再取込対象
    assert got == ["2026-09-07", "2026-09-08", "2026-09-10"]


# --- load_one ------------------------------------------------------------


class FakePopen:
    def __init__(self, argv: list[str], stdout: Any = None) -> None:
        self.argv = argv
        self.date = argv[argv.index("--date") + 1]
        self.stdout: io.BytesIO | None = io.BytesIO(f"row-for-{self.date}\n".encode())
        self._rc = 0

    def wait(self) -> int:
        return self._rc


def test_load_one_deletes_copies_and_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("flows.load_edinet.subprocess.Popen", FakePopen)
    cur = FakeCursor([])
    conn = FakeConn(cur)

    rows = m.load_one(conn, tmp_path, "/app/fdw/edinet_csv_fdw.py", "2026-09-10")

    assert rows == 1
    assert conn.commits == 1  # 1 日 = 1 トランザクション
    assert cur.copies == ["copy landing.edinet_csv_facts from stdin with (format csv)"]
    sqls = [c[0] for c in cur.calls]
    assert any("delete from landing.edinet_csv_facts where file_date" in s for s in sqls)
    assert any("insert into landing.edinet_csv_facts_load_log" in s for s in sqls)
    assert cur.calls[0][1] == ("2026-09-10",)  # DELETE の params


def test_load_one_raises_on_wrapper_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FailingPopen(FakePopen):
        def wait(self) -> int:
            return 3

    monkeypatch.setattr("flows.load_edinet.subprocess.Popen", FailingPopen)
    with pytest.raises(RuntimeError, match="exit 3"):
        m.load_one(FakeConn(FakeCursor([])), tmp_path, "/w.py", "2026-09-10")
