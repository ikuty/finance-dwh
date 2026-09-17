"""landing への JPX形式C(株式相場表・詳細日次)の日付単位取り込み(DuckDB版)。

`load_edinet.py`と同じ設計([[edinet_csv_fdw]]・docs/raw_landing_design.md参照)。
1日ぶんにつき2つのlanding Parquetを書く:
    - landing/jpx_stq_words/file_date=X/part.parquet
        Stage1(`jpx_stq_pdf.iter_words`)の出力そのまま。PDF→座標付き単語データ
        への機械的な変換のみで、ビジネスロジックを持たない。
    - landing/jpx_stq_facts/file_date=X/part.parquet
        Stage2(`jpx_stq_facts.run`)の出力。セクション1(立会市場普通取引)の
        銘柄別明細行に構造化したもの(全列 str|None)。「取り込み済み」の判定は
        こちらの part.parquet の存在で行う。

いずれも一時ファイル→renameでアトミックに置換する(load_edinet.pyと同じ)。

取り込み対象日 = 直近 LANDING_LOOKBACK_DAYS 日 ∪ (レイクに日付ディレクトリが
あるが landing.jpx_stq_facts にまだ無い日)。

このうち「直近 LANDING_LOOKBACK_DAYS 日」を**保証枠**、それより前の未取り込み日を
**バックログ**として明確に分離している（2026-09-15決定）。理由: レイク層で
過去日付ぶんのPDFを大量にバックフィルした直後、バックログが数百日分に膨れ上がり
（1日あたりPDF解析に約2分半かかるため）、dbt build/レポート/Slack通知に
一度も到達できないままMac Miniの電源枠(2時間)が尽きる障害が実機で2日連続発生した
（docs/deployment_design.md参照）。バックログの量はレイク側の事情次第で不定・
大きくなりうる一方、直近数日分の保証枠は必ず小さく完了する。両者を1つの
タスクにまとめていると、後者の存在が前者の量に引きずられてしまうため、
`load_jpx_stq_prices_recent`(保証枠、dbt buildより前)と
`load_jpx_stq_prices_backlog`(バックログ、dbt build/レポート/Slack通知より後)の
2タスクに分割した(`daily_transform.py`参照)。

レイク上のパス: {LAKE_ROOT}/jpx-daily-pdf-dl/raw/detailed-daily/{yyyy}/{mm}/{dd}/stq.pdf
"""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path

import duckdb
import pyarrow as pa
from prefect import get_run_logger, task

import jpx_stq_facts
import jpx_stq_pdf

LAKE_ROOT = os.environ.get("LAKE_ROOT", "/lake")
LANDING_ROOT = os.environ.get("LANDING_ROOT", "/data/landing")
LOOKBACK_DAYS = int(os.environ.get("LANDING_LOOKBACK_DAYS", "7"))

JST = datetime.timezone(datetime.timedelta(hours=9))


def disk_dates(lake_root: Path) -> set[str]:
    """レイクにJPX形式Cの stq.pdf が存在する日(raw/detailed-daily/{yyyy}/{mm}/{dd})。"""
    base = lake_root / "jpx-daily-pdf-dl" / "raw" / "detailed-daily"
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
                if day.is_dir() and day.name.isdigit() and (day / "stq.pdf").exists():
                    out.add(f"{year.name}-{month.name}-{day.name}")
    return out


def _is_valid_part(path: Path) -> bool:
    """part.parquetが存在し、かつ空でないか(電源断でrenameだけ完了し中身が定着
    しなかった0バイトファイルを「取り込み済み」と誤認しないため。2026-09-17実機で
    発生・確認済み)。"""
    return path.exists() and path.stat().st_size > 0


def landing_logged_dates(landing_root: Path) -> set[str]:
    """landing/jpx_stq_facts/file_date=*/ が既に存在する日の集合(=取り込み済み)。"""
    base = landing_root / "jpx_stq_facts"
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


def _atomic_write_parquet(tbl: pa.Table, out_dir: Path) -> None:
    # 変数名 tbl は必須: DuckDB がフレーム内のこの名前の変数を自動でテーブルとして
    # 見つける(replacement scan)ため。load_edinet.py と同じ書き方。
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


def _pdf_path(lake_root: Path, date: str) -> Path:
    yyyy, mm, dd = date.split("-")
    return lake_root / "jpx-daily-pdf-dl" / "raw" / "detailed-daily" / yyyy / mm / dd / "stq.pdf"


def load_one(lake_root: Path, landing_root: Path, date: str) -> int:
    """1日ぶんを処理し、landing.jpx_stq_words / landing.jpx_stq_facts へ書く。

    戻り値は書き出したレコード数(jpx_stq_facts、銘柄数)。
    """
    words = list(jpx_stq_pdf.iter_words(_pdf_path(lake_root, date)))

    words_tbl = pa.table(
        {
            "page": pa.array([w.page for w in words], type=pa.int16()),
            "top": pa.array([w.top for w in words], type=pa.float32()),
            "x0": pa.array([w.x0 for w in words], type=pa.float32()),
            "x1": pa.array([w.x1 for w in words], type=pa.float32()),
            "size": pa.array([w.size for w in words], type=pa.float32()),
            "text": pa.array([w.text for w in words], type=pa.string()),
        }
    )
    _atomic_write_parquet(words_tbl, landing_root / "jpx_stq_words" / f"file_date={date}")

    records = jpx_stq_facts.run(words, date)
    loaded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    facts_data: dict[str, list[str | None]] = {
        col: [getattr(r, col) for r in records] for col in jpx_stq_facts.FACT_COLUMNS
    }
    facts_data["_loaded_at"] = [loaded_at] * len(records)
    facts_tbl = pa.table({col: pa.array(vals, type=pa.string()) for col, vals in facts_data.items()})
    _atomic_write_parquet(facts_tbl, landing_root / "jpx_stq_facts" / f"file_date={date}")

    return len(records)


_Logger = logging.Logger | logging.LoggerAdapter[logging.Logger]


def _load_dates(dates: list[str], lake_root: Path, landing_root: Path, logger: _Logger) -> dict[str, int]:
    total = 0
    for date in dates:
        rows = load_one(lake_root, landing_root, date)
        total += rows
        logger.info(f"  {date}: {rows} 銘柄")
    return {"dates": len(dates), "rows": total}


@task
def load_jpx_stq_prices_recent() -> dict[str, int]:
    """landing/jpx_stq_words, landing/jpx_stq_facts のうち、直近LOOKBACK_DAYS日分
    (保証枠)を最新化する。サイズが小さく抑えられているため、dbt buildより前に
    必ず実行する(daily_transform.py参照)。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    today = datetime.datetime.now(JST).date()
    dates = recent_dates_to_load(lake_root, landing_root, today, LOOKBACK_DAYS)
    head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
    logger.info(f"jpx_stq 取り込み対象(直近{LOOKBACK_DAYS}日): {len(dates)} 日 [{head}]")
    return _load_dates(dates, lake_root, landing_root, logger)


@task
def load_jpx_stq_prices_backlog() -> dict[str, int]:
    """landing/jpx_stq_words, landing/jpx_stq_facts のうち、直近LOOKBACK_DAYS日より
    前のバックログを最新化する。件数が不定・大きくなりうるため、dbt build/
    レポート/Slack通知より後に実行する(daily_transform.py参照)。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    today = datetime.datetime.now(JST).date()
    dates = backlog_dates_to_load(lake_root, landing_root, today, LOOKBACK_DAYS)
    head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
    logger.info(f"jpx_stq 取り込み対象(バックログ): {len(dates)} 日 [{head}]")
    return _load_dates(dates, lake_root, landing_root, logger)
