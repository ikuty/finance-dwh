"""landing への ir-disclosure-dl REIT(投資法人)決算短信iXBRLの日付単位取り込み。

load_ir_disclosure_kessan.pyと同じ全体設計(docs/raw_landing_design.md参照、保証枠/
バックログの分離・アトミック書き込み)だが、対象書類の判別とデータ形状が異なる:
  - 対象書類の判別: メタデータJSON(ir-disclosure-dlが着地させる{docid}.json)の
    'ixbrl_url'キーの有無で判定する(load_ir_disclosure_kessan.pyの_is_kessan_
    tanshin_jsonとは逆に、ixbrl_urlが無いものは対象外=事業会社向け、ある=REIT)。
  - データ形状: 事業会社向けのFactRow(1書類=1行、全列固定)ではなく、EDINET CSVと
    同じ「1書類=N行(1要素=1行)」の縦持ち形式(ir_disclosure_reit_ixbrl.parse_facts
    参照)。REITのiXBRLは要素数・contextRef(当期/前期/予想等)の組み合わせが
    書類ごとに変わりうるため、固定列のスキーマでは表現しづらいため。

書き出し先: landing/ir_disclosure_reit_ixbrl_facts/file_date={date}/part.parquet
（1列=1要素のファクト単位、cleansed__edinet__factsと同じ縦持ち設計）。

period_end(書類単位、全ファクト行に複製): REITの決算短信タイトル(例:「2026年07月期
決算短信（REIT）」)は、事業会社のタイトル(「YYYY年M月期第N四半期決算短信」等)と同じ
「YYYY年M月期」パターンを使うため、ir_disclosure_kessan_facts.parse_fiscal_year_end/
compute_period_endをそのまま再利用できる。REITには四半期決算短信が無く(確認済み、
services/ir-disclosure-dl/docs/reit_support_design.md@finance-lake参照)、タイトルの
「期」がそのまま開示対象期間そのものを指すため、period_type="annual"固定でオフセット
0ヶ月として計算する(四半期オフセットの分岐は不要)。EDINET側のintermediate__edinet__
reit_financial_facts(Phase 3)が、この列でEDINETのperiod_endと結合する。
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

import ir_disclosure_kessan_facts as kessan_facts_stage
import ir_disclosure_reit_ixbrl as ixbrl_stage

LAKE_ROOT = os.environ.get("LAKE_ROOT", "/lake")
LANDING_ROOT = os.environ.get("LANDING_ROOT", "/data/landing")
LOOKBACK_DAYS = int(os.environ.get("LANDING_LOOKBACK_DAYS", "7"))

JST = datetime.timezone(datetime.timedelta(hours=9))


def _is_reit_ixbrl_json(path: Path) -> bool:
    """REIT(投資法人)の決算短信メタデータか判定する。'ixbrl_url'が設定されている
    ものがREIT(load_ir_disclosure_kessan._is_kessan_tanshin_jsonの判定と対になる、
    詳細はそちら参照)。"""
    try:
        meta: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return meta.get("disclosure_kind") == "kessan_tanshin" and bool(meta.get("ixbrl_url"))


def _is_valid_part(path: Path) -> bool:
    """part.parquetが存在し、かつ空でないか(load_jpx_stq.pyと同じ理由:
    電源断でrenameだけ完了し中身が定着しなかった0バイトファイル対策)。"""
    return path.exists() and path.stat().st_size > 0


def disk_dates(lake_root: Path) -> set[str]:
    """レイクにir-disclosure-dlのREIT決算短信iXBRLメタデータJSONが存在する日。"""
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
                if any(_is_reit_ixbrl_json(p) for p in day.glob("*/*.json")):
                    out.add(f"{year.name}-{month.name}-{day.name}")
    return out


def landing_logged_dates(landing_root: Path) -> set[str]:
    base = landing_root / "ir_disclosure_reit_ixbrl_facts"
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


def _reit_ixbrl_paths(lake_root: Path, date: str) -> list[tuple[Path, dict[str, str]]]:
    """対象日のREIT iXBRLファイルパスと、対応するメタデータJSONの中身を返す。
    iXBRLのファイル名はPDFと異なる(JPX側の別の内部文書ID、ixbrl_urlの末尾)ため、
    pdf_pathのwith_suffix(".json")と同じ要領では求められない。"""
    out: list[tuple[Path, dict[str, str]]] = []
    for json_path in sorted(_day_dir(lake_root, date).glob("*/*.json")):
        if not _is_reit_ixbrl_json(json_path):
            continue
        meta = json.loads(json_path.read_text(encoding="utf-8"))
        ixbrl_url = meta.get("ixbrl_url")
        if not ixbrl_url:
            continue
        ixbrl_path = json_path.parent / ixbrl_url.rsplit("/", 1)[-1]
        if not ixbrl_path.exists():
            continue
        out.append((ixbrl_path, meta))
    return out


def load_one(lake_root: Path, landing_root: Path, date: str) -> int:
    """1日ぶんを処理し、landing.ir_disclosure_reit_ixbrl_factsへ書く。
    戻り値は処理したiXBRLファイル件数(ファクト行数ではない)。"""
    paths = _reit_ixbrl_paths(lake_root, date)

    file_dates: list[str] = []
    docids: list[str] = []
    edinet_codes: list[str | None] = []
    sec_codes: list[str | None] = []
    period_ends: list[str | None] = []
    element_ids: list[str] = []
    context_ids: list[str] = []
    unit_refs: list[str | None] = []
    value_nums: list[str | None] = []

    for ixbrl_path, meta in paths:
        # iXBRLファイル名自体はJPX側のXBRL文書ID(PDFのdocidとは別の番号体系)なので、
        # そのままdocidとして使う(ir_disclosure_kessan_tanshinのdocidとは異なる識別子
        # 空間になるが、REIT側はedinet_code×period単位で結合する設計のため問題ない)。
        docid = ixbrl_path.with_suffix("").name
        title = meta.get("jpx_title") or ""
        fye = kessan_facts_stage.parse_fiscal_year_end(title)
        period_end = kessan_facts_stage.compute_period_end(fye[0], fye[1], "annual") if fye else None
        html = ixbrl_path.read_text(encoding="utf-8", errors="ignore")
        facts = ixbrl_stage.parse_facts(html)
        for fact in facts:
            file_dates.append(date)
            docids.append(docid)
            edinet_codes.append(meta.get("edinet_code"))
            sec_codes.append(meta.get("sec_code"))
            period_ends.append(period_end)
            element_ids.append(fact.element_id)
            context_ids.append(fact.context_id)
            unit_refs.append(fact.unit_ref)
            value_nums.append(None if fact.value_num is None else repr(fact.value_num))

    tbl = pa.table({
        "file_date": pa.array(file_dates, type=pa.string()),
        "docid": pa.array(docids, type=pa.string()),
        "edinet_code": pa.array(edinet_codes, type=pa.string()),
        "sec_code": pa.array(sec_codes, type=pa.string()),
        "period_end": pa.array(period_ends, type=pa.string()),
        "element_id": pa.array(element_ids, type=pa.string()),
        "context_id": pa.array(context_ids, type=pa.string()),
        "unit_ref": pa.array(unit_refs, type=pa.string()),
        "value_num": pa.array(value_nums, type=pa.string()),
    })
    _atomic_write_parquet(tbl, landing_root / "ir_disclosure_reit_ixbrl_facts" / f"file_date={date}")

    return len(paths)


_Logger = logging.Logger | logging.LoggerAdapter[logging.Logger]


def _load_dates(dates: list[str], lake_root: Path, landing_root: Path, logger: _Logger) -> dict[str, int]:
    total = 0
    for date in dates:
        rows = load_one(lake_root, landing_root, date)
        total += rows
        logger.info(f"  {date}: {rows} 件")
    return {"dates": len(dates), "rows": total}


@task
def load_ir_disclosure_reit_recent() -> dict[str, int]:
    """landing/ir_disclosure_reit_ixbrl_factsのうち、直近LOOKBACK_DAYS日分
    (保証枠)を最新化する。dbt buildより前に実行する。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    today = datetime.datetime.now(JST).date()
    dates = recent_dates_to_load(lake_root, landing_root, today, LOOKBACK_DAYS)
    head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
    logger.info(f"ir_disclosure_reit_ixbrl 取り込み対象(直近{LOOKBACK_DAYS}日): {len(dates)} 日 [{head}]")
    return _load_dates(dates, lake_root, landing_root, logger)


@task
def load_ir_disclosure_reit_backlog() -> dict[str, int]:
    """landing/ir_disclosure_reit_ixbrl_factsのうち、直近LOOKBACK_DAYS日より前の
    バックログを最新化する。dbt build/レポート/Slack通知より後に実行する。"""
    logger = get_run_logger()
    lake_root = Path(LAKE_ROOT)
    landing_root = Path(LANDING_ROOT)
    today = datetime.datetime.now(JST).date()
    dates = backlog_dates_to_load(lake_root, landing_root, today, LOOKBACK_DAYS)
    head = ", ".join(dates[:3]) + (" ..." if len(dates) > 3 else "")
    logger.info(f"ir_disclosure_reit_ixbrl 取り込み対象(バックログ): {len(dates)} 日 [{head}]")
    return _load_dates(dates, lake_root, landing_root, logger)
