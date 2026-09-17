"""landing への日付単位の Parquet 取り込み（DuckDB 版）。

`edinet_csv_fdw.py` の日付走査・パース・破損ファイル耐性ロジックをそのまま再利用する。
DuckDB 自身の CSV パーサーは EDINET の長大なテキストブロック（引用符付きフィールドが
数万文字に及ぶ）で解析エラーになることを実機で確認したため、CSV 文字列を DuckDB に
再度読ませることはしない。Python の csv モジュール（`edinet_csv_fdw`）でパース済みの
行を列ごとに集約し、pyarrow Table 経由で DuckDB へ渡して Parquet を書く
（行ごとの `executemany` は遅い実測があったため不採用。列指向集約 + pyarrow で
実測 15〜20 万行/秒）。

書き出し先: {LANDING_ROOT}/edinet_csv_facts/file_date={date}/part.parquet
（一時ファイルに書いてから rename でアトミックに置換）。

注意（DuckDB の Hive パーティショニング自動検出）: パスに `file_date=YYYY-MM-DD` を
含むため、`read_parquet()` で読む側は単一ファイル指定でも自動的に Hive パーティション
とみなし、ファイル内の file_date 列（ここでは VARCHAR で書く）を DATE 型の値で
上書きして返す（実機で確認済み。書き込み自体は常に VARCHAR で正しく、読み取り側の
挙動）。素の文字列が欲しい読み取りは `hive_partitioning=false` を明示すること。
下流（dbt-duckdb 等）で使うときは、この自動 DATE 型カラムを積極的に使うか
明示的に潰すかを設計時に決めること。

取り込み対象日 = 直近 LOOKBACK_DAYS 日 ∪（レイクに日付ディレクトリがあるが landing に
まだ無い日）。前者は edinet-dl の DAYS_WINDOW による遡及取得、後者は過去バックフィルを拾う。
「日付ディレクトリの有無」だけを見る（landing 側は part.parquet の存在＝取り込み済みの
印なので、0 行の日でも一度書けば翌日以降は再走査されない）。

このうち「直近 LOOKBACK_DAYS 日」を**保証枠**、それより前の未取り込み日を**バックログ**
として分離している（2026-09-15決定、load_jpx_stq.pyと同じ設計）。理由: レイク層で
edinet-dlの過去分（2022年、365日）を一括バックフィルした直後、DWH側のバックログが
大量に積み上がる状況が発生した。バックログの量はレイク側の事情次第で不定・大きく
なりうる一方、直近数日分の保証枠は必ず小さく完了する。両者を1つのタスクにまとめて
いると、後者の存在が前者の量に引きずられ、dbt build/レポート/Slack通知に一度も
到達できないまま電源枠が尽きる（2026-09-13のJPX形式B、2026-09-14夜間のJPX形式Cで
実際に発生した障害と同種）。`load_edinet_csv_facts_recent`(保証枠、dbt buildより前)と
`load_edinet_csv_facts_backlog`(バックログ、dbt build/レポート/Slack通知より後)の
2タスクに分割した(daily_transform.py参照)。
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
from prefect import get_run_logger, task

import edinet_csv_fdw

LAKE_ROOT = os.environ.get("LAKE_ROOT", "/lake")
LANDING_ROOT = os.environ.get("LANDING_ROOT", "/data/landing")
LOOKBACK_DAYS = int(os.environ.get("LANDING_LOOKBACK_DAYS", "7"))

JST = datetime.timezone(datetime.timedelta(hours=9))

# edinet_csv_fdw の出力列順をそのまま踏襲する。
COLUMNS = edinet_csv_fdw.OUTPUT_HEADER


class _ColumnCollector:
    """edinet_csv_fdw.run() の RowWriter として渡し、列ごとのリストへ集約する。"""

    def __init__(self, ncols: int) -> None:
        self.columns: list[list[str]] = [[] for _ in range(ncols)]

    def writerow(self, row: list[str]) -> object:
        cols = self.columns
        for i, value in enumerate(row):
            cols[i].append(value)
        return None


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


def _is_valid_part(path: Path) -> bool:
    """part.parquetが存在し、かつ空でないか(電源断でrenameだけ完了し中身が定着
    しなかった0バイトファイルを「取り込み済み」と誤認しないため。2026-09-17実機で
    発生・確認済み)。"""
    return path.exists() and path.stat().st_size > 0


def landing_logged_dates(landing_root: Path) -> set[str]:
    """landing/edinet_csv_facts/file_date=*/ が既に存在する日の集合（=取り込み済み）。"""
    base = landing_root / "edinet_csv_facts"
    out: set[str] = set()
    if not base.is_dir():
        return out
    for d in base.iterdir():
        if d.is_dir() and d.name.startswith("file_date=") and _is_valid_part(d / "part.parquet"):
            out.add(d.name.removeprefix("file_date="))
    return out


def recent_dates(today: datetime.date, days: int) -> set[str]:
    return {(today - datetime.timedelta(days=i)).isoformat() for i in range(days + 1)}


def dates_to_load(lake_root: Path, landing_root: Path, today: datetime.date, lookback: int) -> list[str]:
    """保証枠・バックログを合わせた全対象日(参考・テスト用)。実際のフローは
    recent_dates_to_load / backlog_dates_to_load を別タスクとして使う。"""
    on_disk = disk_dates(lake_root)
    done = landing_logged_dates(landing_root)
    return sorted((on_disk - done) | (on_disk & recent_dates(today, lookback)))


def recent_dates_to_load(lake_root: Path, landing_root: Path, today: datetime.date, lookback: int) -> list[str]:
    """保証枠: 直近lookback日のうち、レイクにはあるがlandingにまだ無い日。
    サイズは常にlookback+1日以下で、dbt buildより前に必ず完了させたい。"""
    on_disk = disk_dates(lake_root)
    done = landing_logged_dates(landing_root)
    return sorted((on_disk - done) & recent_dates(today, lookback))


def backlog_dates_to_load(lake_root: Path, landing_root: Path, today: datetime.date, lookback: int) -> list[str]:
    """バックログ: 直近lookback日より前で、レイクにはあるがlandingにまだ無い日。
    件数は不定・大きくなりうるため、dbt build/レポート/Slack通知より後に処理する。"""
    on_disk = disk_dates(lake_root)
    done = landing_logged_dates(landing_root)
    return sorted((on_disk - done) - recent_dates(today, lookback))


def load_one(lake_root: Path, landing_root: Path, date: str) -> int:
    """1 日ぶんを Parquet として書き出す（一時ファイル→rename でアトミックに置換）。

    戻り値は書き出した行数。
    """
    collector = _ColumnCollector(len(COLUMNS))
    skipped = edinet_csv_fdw.run(lake_root, collector, date)
    if skipped:
        print(f"warning: {date}: {skipped} 個の CSV を読み飛ばした", file=sys.stderr)

    tbl = pa.table({col: pa.array(collector.columns[i], type=pa.string()) for i, col in enumerate(COLUMNS)})

    out_dir = landing_root / "edinet_csv_facts" / f"file_date={date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = out_dir / "part.parquet.tmp"
    final_path = out_dir / "part.parquet"

    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT * FROM tbl) TO '{tmp_path.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
    # renameだけでは電源断時のディスク定着を保証しない。ext4はデフォルトで書き込み
    # データをページキャッシュに留め置くため、renameの後にTapoの物理電源断が起きると
    # メタデータ上はファイルが存在するのに中身が定着しておらず0バイトになり得る
    # (2026-09-17実機で発生・確認済み)。rename前にtmpファイルをfsyncし、rename後は
    # 親ディレクトリのエントリ更新もfsyncする。
    with open(tmp_path, "rb") as f:
        os.fsync(f.fileno())
    tmp_path.replace(final_path)
    dir_fd = os.open(out_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    return len(collector.columns[0])


_Logger = logging.Logger | logging.LoggerAdapter[logging.Logger]


def _load_dates(dates: list[str], lake_root: Path, landing_root: Path, logger: _Logger) -> dict[str, int]:
    total = 0
    for date in dates:
        rows = load_one(lake_root, landing_root, date)
        total += rows
        logger.info(f"  {date}: {rows} 行")
    return {"dates": len(dates), "rows": total}


@task
def load_edinet_csv_facts_recent() -> dict[str, int]:
    """landing/edinet_csv_facts のうち、直近LOOKBACK_DAYS日分(保証枠)を最新化する。
    サイズが小さく抑えられているため、dbt buildより前に必ず実行する
    (daily_transform.py参照)。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    today = datetime.datetime.now(JST).date()
    dates = recent_dates_to_load(lake_root, landing_root, today, LOOKBACK_DAYS)
    head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
    logger.info(f"landing 取り込み対象(直近{LOOKBACK_DAYS}日): {len(dates)} 日 [{head}]")
    return _load_dates(dates, lake_root, landing_root, logger)


@task
def load_edinet_csv_facts_backlog() -> dict[str, int]:
    """landing/edinet_csv_facts のうち、直近LOOKBACK_DAYS日より前のバックログを
    最新化する。件数が不定・大きくなりうるため、dbt build/レポート/Slack通知より
    後に実行する(daily_transform.py参照)。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    today = datetime.datetime.now(JST).date()
    dates = backlog_dates_to_load(lake_root, landing_root, today, LOOKBACK_DAYS)
    head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
    logger.info(f"landing 取り込み対象(バックログ): {len(dates)} 日 [{head}]")
    return _load_dates(dates, lake_root, landing_root, logger)
