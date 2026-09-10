"""landing.edinet_csv_facts への日付単位の取り込み。

file_fdw は述語プッシュダウン不可で全量スキャンが重い（実測 約12分）。日付を渡せる
edinet_csv_fdw.py --date で 1 日ぶんだけ抽出し、native テーブル
landing.edinet_csv_facts へ COPY する。以降 dbt はこの native テーブルを source にする。

取り込み対象日 = 「直近 LOOKBACK_DAYS 日」∪「レイクに日付ディレクトリがあるが
landing._load_log に無い日」。前者は edinet-dl の DAYS_WINDOW による遡及取得を、
後者は過去バックフィルを拾う。1 日ぶんは BEGIN; DELETE; COPY; COMMIT で冪等に置換する。
"""

from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path
from typing import Protocol

import psycopg2
from prefect import get_run_logger, task

WRAPPER = os.environ.get("EDINET_CSV_FDW", "/app/fdw/edinet_csv_fdw.py")
LAKE_ROOT = os.environ.get("LAKE_ROOT", "/lake")
LOOKBACK_DAYS = int(os.environ.get("LANDING_LOOKBACK_DAYS", "7"))

JST = datetime.timezone(datetime.timedelta(hours=9))

_DDL = """
create schema if not exists landing;
create table if not exists landing.edinet_csv_facts (
    file_date               text not null,
    edinet_code             text,
    doc_id                  text,
    element_id              text,
    item_name               text,
    context_id              text,
    relative_year           text,
    consolidated_individual text,
    period_instant          text,
    unit_id                 text,
    unit                    text,
    value                   text
);
create index if not exists ix_landing_edinet_csv_facts_file_date
    on landing.edinet_csv_facts (file_date);
create table if not exists landing.edinet_csv_facts_load_log (
    file_date  text primary key,
    row_count  bigint not null,
    loaded_at  timestamptz not null default now()
);
"""


class DbCursor(Protocol):
    rowcount: int

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> object: ...
    def copy_expert(self, sql: str, file: object) -> object: ...
    def fetchall(self) -> list[tuple[object, ...]]: ...
    def __enter__(self) -> DbCursor: ...
    def __exit__(self, *exc: object) -> None: ...


class DbConn(Protocol):
    autocommit: bool

    def cursor(self) -> DbCursor: ...
    def __enter__(self) -> DbConn: ...
    def __exit__(self, *exc: object) -> None: ...
    def close(self) -> None: ...


def _dsn_from_env() -> str:
    return (
        f"host={os.environ.get('DBT_HOST', 'postgres')} "
        f"port={os.environ.get('DBT_PORT', '5432')} "
        f"dbname={os.environ.get('DBT_DBNAME', 'finance_dwh')} "
        f"user={os.environ.get('DBT_USER', 'finance')} "
        f"password={os.environ.get('DBT_PASSWORD', '')}"
    )


def disk_dates(lake_root: Path) -> set[str]:
    """レイクに日付ディレクトリ raw/{yyyy}/{mm}/{dd} が存在する日（中身は見ない）。"""
    base = lake_root / "edinet-dl" / "raw"
    out: set[str] = set()
    if not base.is_dir():
        return out
    for year in base.iterdir():
        if not (year.is_dir() and year.name.isdigit() and len(year.name) == 4):
            continue
        for month in year.iterdir():
            if not (month.is_dir() and month.name.isdigit()):
                continue
            for day in month.iterdir():
                if day.is_dir() and day.name.isdigit():
                    out.add(f"{year.name}-{month.name}-{day.name}")
    return out


def logged_dates(conn: DbConn) -> set[str]:
    """landing._load_log に記録済みの日。"""
    with conn.cursor() as cur:
        cur.execute("select file_date from landing.edinet_csv_facts_load_log")
        return {str(row[0]) for row in cur.fetchall()}


def recent_dates(today: datetime.date, days: int) -> set[str]:
    return {(today - datetime.timedelta(days=i)).isoformat() for i in range(days + 1)}


def dates_to_load(conn: DbConn, lake_root: Path, today: datetime.date, lookback: int) -> list[str]:
    on_disk = disk_dates(lake_root)
    done = logged_dates(conn)
    return sorted((on_disk - done) | (on_disk & recent_dates(today, lookback)))


def load_one(conn: DbConn, lake_root: Path, wrapper: str, date: str) -> int:
    """1 日ぶんを DELETE→COPY で置換し、_load_log を upsert する。戻り値は投入行数。"""
    proc = subprocess.Popen(  # noqa: S603
        ["python3", wrapper, str(lake_root), "--date", date],
        stdout=subprocess.PIPE,
    )
    if proc.stdout is None:  # pragma: no cover - Popen(stdout=PIPE) では None にならない
        raise RuntimeError("wrapper の stdout を取得できなかった")
    try:
        with conn, conn.cursor() as cur:
            cur.execute("delete from landing.edinet_csv_facts where file_date = %s", (date,))
            cur.copy_expert(
                "copy landing.edinet_csv_facts from stdin with (format csv)", proc.stdout
            )
            rows = cur.rowcount
            cur.execute(
                "insert into landing.edinet_csv_facts_load_log (file_date, row_count) "
                "values (%s, %s) "
                "on conflict (file_date) do update set row_count = excluded.row_count, loaded_at = now()",
                (date, rows),
            )
    finally:
        proc.stdout.close()
        returncode = proc.wait()
    if returncode != 0:
        raise RuntimeError(f"edinet_csv_fdw.py --date {date} が exit {returncode}")
    return rows


@task
def load_edinet_csv_facts(dsn: str | None = None) -> dict[str, int]:
    """landing.edinet_csv_facts を最新化する。"""
    logger = get_run_logger()
    conn = psycopg2.connect(dsn or _dsn_from_env())
    conn.autocommit = False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(_DDL)
        today = datetime.datetime.now(JST).date()
        dates = dates_to_load(conn, Path(LAKE_ROOT), today, LOOKBACK_DAYS)  # type: ignore[arg-type]
        head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
        logger.info(f"landing 取り込み対象: {len(dates)} 日 [{head}]")
        total = 0
        for date in dates:
            rows = load_one(conn, Path(LAKE_ROOT), WRAPPER, date)  # type: ignore[arg-type]
            total += rows
            logger.info(f"  {date}: {rows} 行")
        return {"dates": len(dates), "rows": total}
    finally:
        conn.close()
