"""finance-dwh の日次変換の実行結果を HTML レポートにする（DuckDB / Parquet 版）。

レイク(finance-lake)側の backfill_report.py と体裁をそろえる（モノスペース・小さめ
フォント・`background:#fff`・素の <table>）。フローから `generate_report()` を呼ぶ。

内容:
  - 実行メタ情報（生成時刻 JST、dbt の PASS/WARN/ERROR/SKIP、所要秒、成否）
  - cleansed の Parquet ファイルの行数
  - 最新データ（EDINET の最新 file_date・会社数）

DuckDB はサーバを持たず、cleansed の Parquet ファイルを都度 `read_parquet()` で
直接読む（接続はプロセス内、常駐なし）。
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import duckdb

JST = datetime.timezone(datetime.timedelta(hours=9), name="JST")

CLEANSED_ROOT = os.environ.get("CLEANSED_ROOT", "/data/cleansed")

# 行数を出す Parquet ファイル（表示順）。存在しなければ件数欄は "-"。
COUNTED_RELATIONS: list[tuple[str, str]] = [
    ("cleansed", "edinet_documents"),
    ("cleansed", "edinet_facts"),
    ("cleansed", "jpx_stq_prices"),
    ("cleansed", "jpx_monthly_ohlc"),
    ("cleansed", "mufg_stock_splits"),
    ("cleansed", "mufg_stock_consolidations"),
    ("cleansed", "mufg_company_name_changes"),
]


class DbConn(Protocol):
    """duckdb.DuckDBPyConnection のうち本モジュールが使う部分（テストで差し替え可能に）。"""

    def execute(self, sql: str) -> "DbConn": ...
    def fetchone(self) -> tuple[object, ...] | None: ...


@dataclass
class DbtOutcome:
    ok: bool
    passed: int
    warned: int
    errored: int
    skipped: int
    duration_s: float


@dataclass
class ReportData:
    generated_at: datetime.datetime
    layer_counts: list[tuple[str, str, int | None]]
    edinet_latest_date: str | None
    edinet_company_count: int | None
    jpx_latest_date: str | None = None
    jpx_code_count: int | None = None
    jpx_monthly_code_count: int | None = None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _count(con: DbConn, path: Path) -> int | None:
    """1 ファイルの行数。存在しない/読めなければ None。"""
    if not path.exists():
        return None
    try:
        row = con.execute(f"select count(*) from read_parquet('{path.as_posix()}')").fetchone()
    except duckdb.Error:
        return None
    return None if row is None else _as_int(row[0])


def _scalar_pair(con: DbConn, sql: str) -> tuple[str | None, int | None]:
    try:
        row = con.execute(sql).fetchone()
    except duckdb.Error:
        return (None, None)
    if row is None or len(row) < 2:
        return (None, None)
    first = None if row[0] is None else str(row[0])
    return (first, _as_int(row[1]))


def collect_report_data(con: DbConn, cleansed_root: Path) -> ReportData:
    layer_counts = [(schema, name, _count(con, cleansed_root / f"{name}.parquet")) for schema, name in COUNTED_RELATIONS]

    docs_path = cleansed_root / "edinet_documents.parquet"
    edinet_latest, edinet_companies = (None, None)
    if docs_path.exists():
        edinet_latest, edinet_companies = _scalar_pair(
            con,
            "select max(file_date), count(distinct edinet_code) "
            f"from read_parquet('{docs_path.as_posix()}')",
        )

    jpx_path = cleansed_root / "jpx_stq_prices.parquet"
    jpx_latest, jpx_codes = (None, None)
    if jpx_path.exists():
        jpx_latest, jpx_codes = _scalar_pair(
            con,
            "select max(file_date), count(distinct code) "
            f"from read_parquet('{jpx_path.as_posix()}')",
        )

    monthly_path = cleansed_root / "jpx_monthly_ohlc.parquet"
    jpx_monthly_codes = None
    if monthly_path.exists():
        try:
            row = con.execute(f"select count(distinct code) from read_parquet('{monthly_path.as_posix()}')").fetchone()
        except duckdb.Error:
            row = None
        jpx_monthly_codes = None if row is None else _as_int(row[0])

    return ReportData(
        generated_at=datetime.datetime.now(JST),
        layer_counts=layer_counts,
        edinet_latest_date=edinet_latest,
        edinet_company_count=edinet_companies,
        jpx_latest_date=jpx_latest,
        jpx_code_count=jpx_codes,
        jpx_monthly_code_count=jpx_monthly_codes,
    )


def _fmt_count(n: int | None) -> str:
    return "-" if n is None else f"{n:,}"


def _dbt_phrase(dbt: DbtOutcome) -> str:
    """dbt の結果を短い文言に。モデル未定義の間は件数を出さない。"""
    if dbt.passed == dbt.warned == dbt.errored == dbt.skipped == 0:
        return "モデル未定義"
    return f"PASS={dbt.passed} WARN={dbt.warned} ERROR={dbt.errored} SKIP={dbt.skipped}"


def render_html(data: ReportData, dbt: DbtOutcome) -> str:
    status_class = "ok" if dbt.ok else "ng"
    status_text = "✅ 成功" if dbt.ok else "❌ 失敗"

    layer_rows = "\n".join(
        f'<tr><td>{schema}</td><td>{name}</td><td class="num">{_fmt_count(n)}</td></tr>'
        for schema, name, n in data.layer_counts
    )

    latest_rows = "\n".join(
        [
            f'<tr><td>EDINET 最新 file_date</td><td class="num">{data.edinet_latest_date or "-"}</td></tr>',
            f'<tr><td>EDINET 会社数</td><td class="num">{_fmt_count(data.edinet_company_count)}</td></tr>',
            f'<tr><td>JPX 最新 file_date</td><td class="num">{data.jpx_latest_date or "-"}</td></tr>',
            f'<tr><td>JPX 銘柄数</td><td class="num">{_fmt_count(data.jpx_code_count)}</td></tr>',
            f'<tr><td>JPX 月次OHLC 銘柄数</td><td class="num">{_fmt_count(data.jpx_monthly_code_count)}</td></tr>',
        ]
    )

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>finance-dwh 実行レポート</title>
<style>
  body {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px;
          margin: 12px; color: #222; background: #fff; }}
  h1 {{ font-size: 13px; font-weight: normal; margin: 0 0 8px; }}
  h2 {{ font-size: 12px; font-weight: bold; margin: 12px 0 4px; }}
  .meta {{ margin-bottom: 4px; line-height: 1.5; }}
  .ok {{ color: #1a7f1a; }}
  .ng {{ color: #b00; }}
  table {{ border-collapse: collapse; }}
  th, td {{ border: 1px solid #ccc; padding: 2px 8px; text-align: left; }}
  th {{ background: #f0f0f0; font-weight: normal; }}
  td.num {{ text-align: right; }}
</style>
</head>
<body>
<h1>finance-dwh 実行レポート</h1>
<div class="meta">
  生成: {data.generated_at.strftime("%Y-%m-%d %H:%M:%S %Z")}<br>
  dbt: <span class="{status_class}">{status_text}</span>
  &nbsp;{_dbt_phrase(dbt)}
  &nbsp;/ {dbt.duration_s:.1f}s
</div>
<h2>層別行数</h2>
<table>
<tr><th>スキーマ</th><th>ファイル</th><th>行数</th></tr>
{layer_rows}
</table>
<h2>最新データ</h2>
<table>
<tr><th>項目</th><th>値</th></tr>
{latest_rows}
</table>
</body>
</html>
"""


def summary_text(data: ReportData, dbt: DbtOutcome) -> str:
    status = "✅ 成功" if dbt.ok else "❌ 失敗"
    facts = next((n for _s, name, n in data.layer_counts if name == "edinet_facts"), None)
    company_n = _fmt_count(data.edinet_company_count)
    jpx_rows = next((n for _s, name, n in data.layer_counts if name == "jpx_stq_prices"), None)
    jpx_code_n = _fmt_count(data.jpx_code_count)
    jpx_monthly_rows = next((n for _s, name, n in data.layer_counts if name == "jpx_monthly_ohlc"), None)
    jpx_monthly_code_n = _fmt_count(data.jpx_monthly_code_count)
    return (
        f"{status} / finance-dwh 日次 / dbt {_dbt_phrase(dbt)} / "
        f"cleansed: EDINET明細{_fmt_count(facts)}行・{company_n}社 / "
        f"JPX相場{_fmt_count(jpx_rows)}行・{jpx_code_n}銘柄 / "
        f"JPX月次OHLC{_fmt_count(jpx_monthly_rows)}行・{jpx_monthly_code_n}銘柄"
    )


def generate_report(dbt: DbtOutcome, *, cleansed_root: str | None = None) -> tuple[str, str]:
    """レポート HTML と Slack 用の短いサマリ文字列を返す。"""
    root = Path(cleansed_root or CLEANSED_ROOT)
    con = duckdb.connect()
    try:
        data = collect_report_data(con, root)
    finally:
        con.close()
    return render_html(data, dbt), summary_text(data, dbt)
