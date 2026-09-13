"""landing への JPX形式B(株式相場表・月次簡易OHLC)の月単位取り込み(DuckDB版)。

`load_jpx_stq.py`(形式C)と同じ設計([[jpx_stq_pdf]]・docs/raw_landing_design.md
参照)だが、1ファイル=1ヶ月ぶんの全営業日を含むため2点異なる:

- 「取り込み済み」の判定は**月単位**で行う（`landing/jpx_monthly_ohlc_loaded_months/
  file_month=YYYY-MM/part.parquet` という専用マーカー。本体の
  `landing/jpx_monthly_ohlc_facts/`とは別ディレクトリに置き、dbt source の
  glob（`jpx_monthly_ohlc_facts/*/part.parquet`）に紛れ込まないようにする）。
- 形式Bは既にレイク側で一回限りバックフィル済みの**確定済み過去アーカイブ**
  （確定後に内容が変わることは無い）のため、EDINET/形式Cのような「直近
  LANDING_LOOKBACK_DAYS日は毎回再取り込み」という遡及窓は不要。未取り込みの
  月をすべて処理するだけでよい。

1つの月次PDFから得られるレコードは`file_date`ごとにグループ化し、既存の
landingパーティション設計（`file_date=YYYY-MM-DD/part.parquet`、一時ファイル→
renameでアトミックに置換）へ書く。1つのPDFから複数日ぶんのパーティションが
まとめて更新される点が形式Cとの違い。

レイク上のパス: {LAKE_ROOT}/jpx-daily-pdf-dl/raw/monthly-ohlc/{yyyy}/{mm}/stq_monthly.pdf
"""

from __future__ import annotations

import datetime
import os
from collections import defaultdict
from pathlib import Path

import duckdb
import pyarrow as pa
from prefect import get_run_logger, task

import jpx_monthly_ohlc_facts
import jpx_stq_pdf

LAKE_ROOT = os.environ.get("LAKE_ROOT", "/lake")
LANDING_ROOT = os.environ.get("LANDING_ROOT", "/data/landing")


def disk_months(lake_root: Path) -> set[str]:
    """レイクにJPX形式Bの stq_monthly.pdf が存在する月(raw/monthly-ohlc/{yyyy}/{mm})。"""
    base = lake_root / "jpx-daily-pdf-dl" / "raw" / "monthly-ohlc"
    out: set[str] = set()
    if not base.is_dir():
        return out
    for year in base.iterdir():
        if not (year.is_dir() and year.name.isdigit() and len(year.name) == 4):
            continue
        for month in year.iterdir():
            if month.is_dir() and month.name.isdigit() and (month / "stq_monthly.pdf").exists():
                out.add(f"{year.name}-{month.name}")
    return out


def landing_logged_months(landing_root: Path) -> set[str]:
    """landing/jpx_monthly_ohlc_loaded_months/file_month=*/ が既に存在する月(=取り込み済み)。"""
    base = landing_root / "jpx_monthly_ohlc_loaded_months"
    out: set[str] = set()
    if not base.is_dir():
        return out
    for d in base.iterdir():
        if d.is_dir() and d.name.startswith("file_month=") and (d / "part.parquet").exists():
            out.add(d.name.removeprefix("file_month="))
    return out


def months_to_load(lake_root: Path, landing_root: Path) -> list[str]:
    return sorted(disk_months(lake_root) - landing_logged_months(landing_root))


def _atomic_write_parquet(tbl: pa.Table, out_dir: Path) -> None:
    # 変数名 tbl は必須: DuckDB がフレーム内のこの名前の変数を自動でテーブルとして
    # 見つける(replacement scan)ため。load_edinet.py / load_jpx_stq.py と同じ書き方。
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = out_dir / "part.parquet.tmp"
    final_path = out_dir / "part.parquet"
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT * FROM tbl) TO '{tmp_path.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
    tmp_path.replace(final_path)


def _pdf_path(lake_root: Path, month: str) -> Path:
    yyyy, mm = month.split("-")
    return lake_root / "jpx-daily-pdf-dl" / "raw" / "monthly-ohlc" / yyyy / mm / "stq_monthly.pdf"


def load_one_month(lake_root: Path, landing_root: Path, month: str) -> dict[str, int]:
    """1ヶ月ぶんを処理し、日付ごとに landing.jpx_monthly_ohlc_facts へ書く。

    戻り値は {"days": 書き出した日数, "rows": 総レコード数}。
    """
    # words は list() 化せず、ジェネレータのままStage2へ渡す(1ヶ月ぶんを一度に
    # メモリへ保持しないため。実機でのメモリ逼迫を踏まえた修正、
    # jpx_monthly_ohlc_facts.pyのモジュールdocstring参照)。
    words = jpx_stq_pdf.iter_words(_pdf_path(lake_root, month))
    records = jpx_monthly_ohlc_facts.build_records(words)

    by_date: dict[str, list[jpx_monthly_ohlc_facts.FactRow]] = defaultdict(list)
    for r in records:
        by_date[r.file_date].append(r)

    loaded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cols = jpx_monthly_ohlc_facts.FACT_COLUMNS
    for date, rows in by_date.items():
        data: dict[str, list[str | None]] = {col: [getattr(r, col) for r in rows] for col in cols}
        data["_loaded_at"] = [loaded_at] * len(rows)
        tbl = pa.table({col: pa.array(vals, type=pa.string()) for col, vals in data.items()})
        _atomic_write_parquet(tbl, landing_root / "jpx_monthly_ohlc_facts" / f"file_date={date}")

    marker_tbl = pa.table(
        {
            "file_month": pa.array([month], type=pa.string()),
            "_loaded_at": pa.array([loaded_at], type=pa.string()),
        }
    )
    _atomic_write_parquet(marker_tbl, landing_root / "jpx_monthly_ohlc_loaded_months" / f"file_month={month}")

    return {"days": len(by_date), "rows": len(records)}


@task
def load_jpx_monthly_ohlc_facts() -> dict[str, int]:
    """landing/jpx_monthly_ohlc_facts を最新化する(未取り込みの月をすべて処理)。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    months = months_to_load(lake_root, landing_root)
    head = ", ".join(months[:3]) + (" ..." if len(months) > 3 else "")
    logger.info(f"jpx_monthly_ohlc 取り込み対象: {len(months)} ヶ月 [{head}]")
    total_days = 0
    total_rows = 0
    for month in months:
        result = load_one_month(lake_root, landing_root, month)
        total_days += result["days"]
        total_rows += result["rows"]
        logger.info(f"  {month}: {result['days']}日 / {result['rows']}行")
    return {"months": len(months), "days": total_days, "rows": total_rows}
