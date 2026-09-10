"""finance-dwh の日次変換の実行結果を HTML レポートにする。

レイク(finance-lake)側の backfill_report.py と体裁をそろえる（モノスペース・小さめ
フォント・`background:#fff`・素の <table>）。フローから `generate_report()` を呼ぶ。

内容:
  - 実行メタ情報（生成時刻 JST、dbt の PASS/WARN/ERROR/SKIP、所要秒、成否）
  - raw の外部テーブルの行数（= file_fdw 経由でレイクを読めているかの確認）
  - 最新データ（EDINET の最新 file_date・会社数、JPX の最新 period・ファイル数）

cleansed / mart は未設計（利用用途が固まってから）なので、いまは raw のみ集計する。
`raw__edinet_csv_facts` の count(*) は含めない: file_fdw が 8 万超の gzip を毎回
フルスキャンするため実測で約 12 分かかる（docs/fdw_raw_layer_design.md）。CSV 明細の
行数が要るときは手動 psql で数える。
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from typing import Protocol

import psycopg2

JST = datetime.timezone(datetime.timedelta(hours=9), name="JST")

# 行数を出すテーブル（表示順）。存在しなければ件数欄は "-"。
# raw__edinet_csv_facts は含めない（count(*) が約 12 分。上の docstring 参照）。
COUNTED_RELATIONS: list[tuple[str, str]] = [
    ("raw", "raw__edinet_document_index"),
    ("raw", "raw__jpx_file_catalog"),
]


class DbCursor(Protocol):
    """psycopg2 の cursor のうち本モジュールが使う部分（テストで差し替え可能に）。"""

    def execute(self, sql: str) -> object: ...
    def fetchone(self) -> tuple[object, ...] | None: ...
    def __enter__(self) -> "DbCursor": ...
    def __exit__(self, *exc: object) -> None: ...


class DbConn(Protocol):
    autocommit: bool

    def cursor(self) -> DbCursor: ...


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
    jpx_latest_date: str | None
    jpx_file_count: int | None


def _dsn_from_env() -> str:
    return (
        f"host={os.environ.get('DBT_HOST', 'postgres')} "
        f"port={os.environ.get('DBT_PORT', '5432')} "
        f"dbname={os.environ.get('DBT_DBNAME', 'finance_dwh')} "
        f"user={os.environ.get('DBT_USER', 'finance')} "
        f"password={os.environ.get('DBT_PASSWORD', '')}"
    )


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _count(cur: DbCursor, schema: str, table: str) -> int | None:
    """1 テーブルの行数。テーブルが無ければ None。"""
    try:
        cur.execute(f'select count(*) from "{schema}"."{table}"')
        row = cur.fetchone()
    except psycopg2.Error:
        return None
    return None if row is None else _as_int(row[0])


def _scalar_pair(cur: DbCursor, sql: str) -> tuple[str | None, int | None]:
    try:
        cur.execute(sql)
        row = cur.fetchone()
    except psycopg2.Error:
        return (None, None)
    if row is None or len(row) < 2:
        return (None, None)
    first = None if row[0] is None else str(row[0])
    return (first, _as_int(row[1]))


def collect_report_data(conn: DbConn) -> ReportData:
    conn.autocommit = True
    with conn.cursor() as cur:
        layer_counts = [(s, t, _count(cur, s, t)) for s, t in COUNTED_RELATIONS]
        edinet_latest, edinet_companies = _scalar_pair(
            cur,
            "select max(file_date), count(distinct edinet_code) "
            "from \"raw\".\"raw__edinet_document_index\" where sec_code <> ''",
        )
        jpx_latest, jpx_files = _scalar_pair(
            cur,
            'select max(period), count(*) from "raw"."raw__jpx_file_catalog"',
        )
    return ReportData(
        generated_at=datetime.datetime.now(JST),
        layer_counts=layer_counts,
        edinet_latest_date=edinet_latest,
        edinet_company_count=edinet_companies,
        jpx_latest_date=jpx_latest,
        jpx_file_count=jpx_files,
    )


def _fmt_count(n: int | None) -> str:
    return "-" if n is None else f"{n:,}"


def _dbt_phrase(dbt: DbtOutcome) -> str:
    """dbt の結果を短い文言に。モデル未定義（raw のみ）の間は件数を出さない。"""
    if dbt.passed == dbt.warned == dbt.errored == dbt.skipped == 0:
        return "モデル未定義（raw のみ）"
    return f"PASS={dbt.passed} WARN={dbt.warned} ERROR={dbt.errored} SKIP={dbt.skipped}"


def render_html(data: ReportData, dbt: DbtOutcome) -> str:
    status_class = "ok" if dbt.ok else "ng"
    status_text = "✅ 成功" if dbt.ok else "❌ 失敗"

    layer_rows = "\n".join(
        f'<tr><td>{schema}</td><td>{table}</td><td class="num">{_fmt_count(n)}</td></tr>'
        for schema, table, n in data.layer_counts
    )

    latest_rows = "\n".join(
        [
            f'<tr><td>EDINET 最新 file_date</td><td class="num">{data.edinet_latest_date or "-"}</td></tr>',
            f'<tr><td>EDINET 会社数</td><td class="num">{_fmt_count(data.edinet_company_count)}</td></tr>',
            f'<tr><td>JPX 最新 period_date</td><td class="num">{data.jpx_latest_date or "-"}</td></tr>',
            f'<tr><td>JPX ファイル数</td><td class="num">{_fmt_count(data.jpx_file_count)}</td></tr>',
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
<tr><th>スキーマ</th><th>テーブル</th><th>行数</th></tr>
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
    docs = next((n for _s, t, n in data.layer_counts if t == "raw__edinet_document_index"), None)
    company_n = _fmt_count(data.edinet_company_count)
    jpx_n = _fmt_count(data.jpx_file_count)
    return (
        f"{status} / finance-dwh 日次 / dbt {_dbt_phrase(dbt)} / "
        f"raw: EDINET書類{_fmt_count(docs)}件・{company_n}社, JPX {jpx_n}ファイル"
    )


def generate_report(dbt: DbtOutcome, *, dsn: str | None = None) -> tuple[str, str]:
    """レポート HTML と Slack 用の短いサマリ文字列を返す。"""
    conn = psycopg2.connect(dsn or _dsn_from_env())
    try:
        # psycopg2 の connection は DbConn を構造的に満たすが、cursor() の
        # オーバーロード定義のため mypy が判定できない。実行時は問題ない。
        data = collect_report_data(conn)  # type: ignore[arg-type]
    finally:
        conn.close()
    return render_html(data, dbt), summary_text(data, dbt)
