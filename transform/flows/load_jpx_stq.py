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

レイク上のパス: {LAKE_ROOT}/jpx-daily-pdf-dl/raw/detailed-daily/{yyyy}/{mm}/{dd}/stq.pdf
"""

from __future__ import annotations

import datetime
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


def landing_logged_dates(landing_root: Path) -> set[str]:
    """landing/jpx_stq_facts/file_date=*/ が既に存在する日の集合(=取り込み済み)。"""
    base = landing_root / "jpx_stq_facts"
    out: set[str] = set()
    if not base.is_dir():
        return out
    for d in base.iterdir():
        if d.is_dir() and d.name.startswith("file_date=") and (d / "part.parquet").exists():
            out.add(d.name.removeprefix("file_date="))
    return out


def recent_dates(today: datetime.date, days: int) -> set[str]:
    return {(today - datetime.timedelta(days=i)).isoformat() for i in range(days + 1)}


def dates_to_load(lake_root: Path, landing_root: Path, today: datetime.date, lookback: int) -> list[str]:
    on_disk = disk_dates(lake_root)
    done = landing_logged_dates(landing_root)
    return sorted((on_disk - done) | (on_disk & recent_dates(today, lookback)))


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
    tmp_path.replace(final_path)


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


@task
def load_jpx_stq_prices() -> dict[str, int]:
    """landing/jpx_stq_words, landing/jpx_stq_facts を最新化する。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    today = datetime.datetime.now(JST).date()
    dates = dates_to_load(lake_root, landing_root, today, LOOKBACK_DAYS)
    head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
    logger.info(f"jpx_stq 取り込み対象: {len(dates)} 日 [{head}]")
    total = 0
    for date in dates:
        rows = load_one(lake_root, landing_root, date)
        total += rows
        logger.info(f"  {date}: {rows} 銘柄")
    return {"dates": len(dates), "rows": total}
