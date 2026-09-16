"""landing への mufg-corporate-actions（株式分割・株式併合・商号変更）取り込み(DuckDB版)。

レイク層は週次スナップショット（3ページの生HTML）を保存するのみで、パース・型付けは
このモジュール（Stage1+2相当、`mufg_corporate_actions.py`参照）とdbtのcleansedモデル
が担う。レイク上のパス: {LAKE_ROOT}/mufg-corporate-actions/raw/{yyyy}/{mm}/{dd}/に
3ファイル（bunkatu.html・gensi.html・syougou_henkou.html）がまとまって置かれる。

取り込み対象日 = レイクに3ファイルすべて揃った日付ディレクトリがあるが、landingに
まだ無い日、のみ（edinet-dl/jpx-daily-pdf-dlのようなLOOKBACK_DAYSの遡及窓は不要。
週次かつ軽量なため、未取り込みの週をすべて処理してもバックログが問題化しない。
recent/backlogの分割も不要）。

各ページは「その時点での全履歴」を毎回再掲載するため、landingは週ごとに
file_date=YYYY-MM-DDでパーティションして蓄積する（監査証跡、既存パターンと同じ）。
cleansed層は最新file_dateのみを読む設計にする（dbtモデル側で実施、landing側は
関与しない）。
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
from prefect import get_run_logger, task

import mufg_corporate_actions as mca

LAKE_ROOT = os.environ.get("LAKE_ROOT", "/lake")
LANDING_ROOT = os.environ.get("LANDING_ROOT", "/data/landing")

# レイク側のformat名(ファイル名) -> (landingテーブル名, パース関数, 列名リスト)
_PAGES: dict[str, tuple[str, Callable[[str], list[Any]], list[str]]] = {
    "bunkatu": ("mufg_stock_splits", mca.parse_stock_splits, mca.STOCK_SPLIT_COLUMNS),
    "gensi": (
        "mufg_stock_consolidations",
        mca.parse_stock_consolidations,
        mca.STOCK_CONSOLIDATION_COLUMNS,
    ),
    "syougou_henkou": (
        "mufg_company_name_changes",
        mca.parse_company_name_changes,
        mca.COMPANY_NAME_CHANGE_COLUMNS,
    ),
}


def disk_dates(lake_root: Path) -> set[str]:
    """レイクにmufg-corporate-actionsの3ファイルすべてが揃っている日
    (raw/{yyyy}/{mm}/{dd})。"""
    base = lake_root / "mufg-corporate-actions" / "raw"
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
                if not (day.is_dir() and day.name.isdigit()):
                    continue
                if all((day / f"{fmt}.html").exists() for fmt in _PAGES):
                    out.add(f"{year.name}-{month.name}-{day.name}")
    return out


def landing_logged_dates(landing_root: Path) -> set[str]:
    """landing/mufg_stock_splits/file_date=*/ が既に存在する日の集合(=取り込み済み)。
    3テーブルは必ず同時に書くため、代表として1テーブルだけ見ればよい。"""
    base = landing_root / "mufg_stock_splits"
    out: set[str] = set()
    if not base.is_dir():
        return out
    for d in base.iterdir():
        if d.is_dir() and d.name.startswith("file_date=") and (d / "part.parquet").exists():
            out.add(d.name.removeprefix("file_date="))
    return out


def dates_to_load(lake_root: Path, landing_root: Path) -> list[str]:
    return sorted(disk_dates(lake_root) - landing_logged_dates(landing_root))


def _atomic_write_parquet(tbl: pa.Table, out_dir: Path) -> None:
    # 変数名 tbl は必須: DuckDBがフレーム内のこの名前の変数を自動でテーブルとして
    # 見つける(replacement scan)ため。load_edinet.py等と同じ書き方。
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = out_dir / "part.parquet.tmp"
    final_path = out_dir / "part.parquet"
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT * FROM tbl) TO '{tmp_path.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
    tmp_path.replace(final_path)


def load_one_date(lake_root: Path, landing_root: Path, date: str) -> dict[str, int]:
    """1日(週)ぶんを処理し、3つのlandingテーブルへ書く。戻り値は
    {landingテーブル名: 行数}。"""
    yyyy, mm, dd = date.split("-")
    base_dir = lake_root / "mufg-corporate-actions" / "raw" / yyyy / mm / dd
    loaded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    counts: dict[str, int] = {}
    for fmt, (table_name, parse_fn, columns) in _PAGES.items():
        html = (base_dir / f"{fmt}.html").read_text(encoding="utf-8")
        records = parse_fn(html)

        data: dict[str, list[str]] = {col: [getattr(r, col) for r in records] for col in columns}
        data["file_date"] = [date] * len(records)
        data["_loaded_at"] = [loaded_at] * len(records)
        tbl = pa.table({col: pa.array(vals, type=pa.string()) for col, vals in data.items()})
        _atomic_write_parquet(tbl, landing_root / table_name / f"file_date={date}")
        counts[table_name] = len(records)

    return counts


@task
def load_mufg_corporate_actions() -> dict[str, int]:
    """landing/mufg_stock_splits・mufg_stock_consolidations・
    mufg_company_name_changesを最新化する。処理量は常に小さい（週次・3ファイルの
    みのため、recent/backlog分割は行わずdbt_buildより前に置く（daily_transform.py
    参照）。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    dates = dates_to_load(lake_root, landing_root)
    head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
    logger.info(f"mufg-corporate-actions 取り込み対象: {len(dates)} 日 [{head}]")

    total: dict[str, int] = {"dates": 0}
    for date in dates:
        counts = load_one_date(lake_root, landing_root, date)
        total["dates"] += 1
        for table_name, n in counts.items():
            total[table_name] = total.get(table_name, 0) + n
        logger.info(f"  {date}: {counts}")
    return total
