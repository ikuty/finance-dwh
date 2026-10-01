"""landing への ir-disclosure-dl 決算短信PDFの日付単位取り込み。

load_jpx_stq.pyと同じ設計(docs/raw_landing_design.md参照)。1日ぶんにつき2つの
landing Parquetを書く:
    - landing/ir_disclosure_kessan_text/file_date=X/part.parquet
        Stage1(ir_disclosure_kessan_pdf.extract_text)の出力そのまま
        (docid, text)。PDF→テキストへの機械的な変換のみ。
    - landing/ir_disclosure_kessan_facts/file_date=X/part.parquet
        Stage2(ir_disclosure_kessan_facts.run)の出力(全列str|None)。
        「取り込み済み」の判定はこちらのpart.parquetの存在で行う。

jpx_stqと異なり、1日のうちに複数(0〜N)件のPDFが存在する(1社1PDFがN社ぶん)。
file_dateはir-disclosure-dl側のディレクトリ構造({yyyy}/{mm}/{dd}/{edinet_code}/
{docid}.pdf)のうち日付部分に対応する。

対象PDFの判別はメタデータJSON(ir-disclosure-dlが着地させる{docid}.json、
disclosure_kind=kessan_tanshinのもの)をglobするだけで行う(edinet-dlの
document_list.jsonと同じ発想、ir-disclosure-dl自身のSQLite進捗DBは読まない)。

jpx_stqと同じ理由(docs/deployment_design.md参照)で「直近LANDING_LOOKBACK_DAYS日
(保証枠)」と「それより前の未取り込み日(バックログ)」を分離している。
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path

import duckdb
import pyarrow as pa
from prefect import get_run_logger, task

import ir_disclosure_kessan_facts as facts_stage
import ir_disclosure_kessan_pdf as pdf_stage

LAKE_ROOT = os.environ.get("LAKE_ROOT", "/lake")
LANDING_ROOT = os.environ.get("LANDING_ROOT", "/data/landing")
LOOKBACK_DAYS = int(os.environ.get("LANDING_LOOKBACK_DAYS", "7"))

JST = datetime.timezone(datetime.timedelta(hours=9))


def disk_dates(lake_root: Path) -> set[str]:
    """レイクにir-disclosure-dlのkessan_tanshinメタデータJSONが存在する日
    (raw/{yyyy}/{mm}/{dd}/*/*.json のうちdisclosure_kind=kessan_tanshinを含む日)。"""
    base = lake_root / "ir-disclosure-dl" / "raw"
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
                if any(_is_kessan_tanshin_json(p) for p in day.glob("*/*.json")):
                    out.add(f"{year.name}-{month.name}-{day.name}")
    return out


def _is_kessan_tanshin_json(path: Path) -> bool:
    try:
        meta: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return meta.get("disclosure_kind") == "kessan_tanshin"


def _is_valid_part(path: Path) -> bool:
    """part.parquetが存在し、かつ空でないか(load_jpx_stq.pyと同じ理由:
    電源断でrenameだけ完了し中身が定着しなかった0バイトファイル対策)。"""
    return path.exists() and path.stat().st_size > 0


def landing_logged_dates(landing_root: Path) -> set[str]:
    base = landing_root / "ir_disclosure_kessan_facts"
    out: set[str] = set()
    if not base.is_dir():
        return out
    for d in base.iterdir():
        if d.is_dir() and d.name.startswith("file_date=") and _is_valid_part(d / "part.parquet"):
            out.add(d.name.removeprefix("file_date="))
    return out


def recent_dates(today: datetime.date, days: int) -> set[str]:
    return {(today - datetime.timedelta(days=i)).isoformat() for i in range(days + 1)}


def recent_dates_to_load(lake_root: Path, landing_root: Path, today: datetime.date, lookback: int) -> list[str]:
    on_disk = disk_dates(lake_root)
    done = landing_logged_dates(landing_root)
    return sorted((on_disk - done) & recent_dates(today, lookback))


def backlog_dates_to_load(lake_root: Path, landing_root: Path, today: datetime.date, lookback: int) -> list[str]:
    on_disk = disk_dates(lake_root)
    done = landing_logged_dates(landing_root)
    return sorted((on_disk - done) - recent_dates(today, lookback))


def _atomic_write_parquet(tbl: pa.Table, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = out_dir / "part.parquet.tmp"
    final_path = out_dir / "part.parquet"
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT * FROM tbl) TO '{tmp_path.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
    with open(tmp_path, "rb") as f:
        os.fsync(f.fileno())
    tmp_path.replace(final_path)
    dir_fd = os.open(out_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _day_dir(lake_root: Path, date: str) -> Path:
    yyyy, mm, dd = date.split("-")
    return lake_root / "ir-disclosure-dl" / "raw" / yyyy / mm / dd


def _kessan_pdf_paths(lake_root: Path, date: str) -> list[tuple[Path, dict[str, str]]]:
    """対象日のkessan_tanshin PDFパスと、対応するメタデータJSONの中身を返す。"""
    out: list[tuple[Path, dict[str, str]]] = []
    for json_path in sorted(_day_dir(lake_root, date).glob("*/*.json")):
        if not _is_kessan_tanshin_json(json_path):
            continue
        pdf_path = json_path.with_suffix(".pdf")
        if not pdf_path.exists():
            continue
        meta = json.loads(json_path.read_text(encoding="utf-8"))
        out.append((pdf_path, meta))
    return out


def load_one(lake_root: Path, landing_root: Path, date: str) -> int:
    """1日ぶんを処理し、landing.ir_disclosure_kessan_text /
    landing.ir_disclosure_kessan_facts へ書く。戻り値は処理したPDF件数。"""
    pdfs = _kessan_pdf_paths(lake_root, date)

    docids: list[str] = []
    texts: list[str] = []
    facts: list[facts_stage.FactRow] = []
    for pdf_path, meta in pdfs:
        docid = pdf_path.stem
        title = meta.get("jpx_title") or meta.get("tdnet_title") or ""
        text = pdf_stage.extract_text(pdf_path)
        docids.append(docid)
        texts.append(text)
        facts.append(facts_stage.run(text, docid, title))

    text_tbl = pa.table({
        "file_date": pa.array([date] * len(docids), type=pa.string()),
        "docid": pa.array(docids, type=pa.string()),
        "text": pa.array(texts, type=pa.string()),
    })
    _atomic_write_parquet(text_tbl, landing_root / "ir_disclosure_kessan_text" / f"file_date={date}")

    loaded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    facts_data: dict[str, list[str | None]] = {
        col: [getattr(r, col) for r in facts] for col in facts_stage.FACT_COLUMNS
    }
    facts_data["file_date"] = [date] * len(facts)
    facts_data["_loaded_at"] = [loaded_at] * len(facts)
    facts_tbl = pa.table({col: pa.array(vals, type=pa.string()) for col, vals in facts_data.items()})
    _atomic_write_parquet(facts_tbl, landing_root / "ir_disclosure_kessan_facts" / f"file_date={date}")

    return len(pdfs)


_Logger = logging.Logger | logging.LoggerAdapter[logging.Logger]


def _load_dates(dates: list[str], lake_root: Path, landing_root: Path, logger: _Logger) -> dict[str, int]:
    total = 0
    for date in dates:
        rows = load_one(lake_root, landing_root, date)
        total += rows
        logger.info(f"  {date}: {rows} 件")
    return {"dates": len(dates), "rows": total}


@task
def load_ir_disclosure_kessan_recent() -> dict[str, int]:
    """landing/ir_disclosure_kessan_text, landing/ir_disclosure_kessan_facts の
    うち、直近LOOKBACK_DAYS日分(保証枠)を最新化する。dbt buildより前に実行する。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    today = datetime.datetime.now(JST).date()
    dates = recent_dates_to_load(lake_root, landing_root, today, LOOKBACK_DAYS)
    head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
    logger.info(f"ir_disclosure_kessan 取り込み対象(直近{LOOKBACK_DAYS}日): {len(dates)} 日 [{head}]")
    return _load_dates(dates, lake_root, landing_root, logger)


@task
def load_ir_disclosure_kessan_backlog() -> dict[str, int]:
    """landing/ir_disclosure_kessan_text, landing/ir_disclosure_kessan_facts の
    うち、直近LOOKBACK_DAYS日より前のバックログを最新化する。dbt build/レポート/
    Slack通知より後に実行する。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    today = datetime.datetime.now(JST).date()
    dates = backlog_dates_to_load(lake_root, landing_root, today, LOOKBACK_DAYS)
    head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
    logger.info(f"ir_disclosure_kessan 取り込み対象(バックログ): {len(dates)} 日 [{head}]")
    return _load_dates(dates, lake_root, landing_root, logger)
