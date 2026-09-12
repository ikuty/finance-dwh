"""run_report.py のテスト（DuckDB / Parquet 版）。DuckDB は組み込みなので実物の
Parquet フィクスチャを使う（モック不要）。"""

from __future__ import annotations

import datetime
from pathlib import Path

import duckdb

from report.run_report import (
    DbtOutcome,
    ReportData,
    collect_report_data,
    generate_report,
    render_html,
    summary_text,
)

JST = datetime.timezone(datetime.timedelta(hours=9), name="JST")


def write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        if rows:
            cols = list(rows[0].keys())
            values = ", ".join(
                "(" + ", ".join(repr(r[c]) for c in cols) + ")" for r in rows
            )
            con.execute(
                f"COPY (SELECT * FROM (VALUES {values}) AS t({', '.join(cols)})) "
                f"TO '{path.as_posix()}' (FORMAT PARQUET)"
            )
        else:
            con.execute(
                f"COPY (SELECT NULL::date AS file_date, NULL::varchar AS edinet_code WHERE FALSE) "
                f"TO '{path.as_posix()}' (FORMAT PARQUET)"
            )
    finally:
        con.close()


def _outcome(ok: bool = True) -> DbtOutcome:
    return DbtOutcome(ok=ok, passed=12, warned=0, errored=0 if ok else 3, skipped=0, duration_s=0.2)


def test_collect_report_data_counts_and_missing_file(tmp_path: Path) -> None:
    write_parquet(
        tmp_path / "edinet_documents.parquet",
        [
            {"file_date": "2026-09-10", "edinet_code": "E00011"},
            {"file_date": "2026-09-09", "edinet_code": "E00022"},
            {"file_date": "2026-09-10", "edinet_code": "E00033"},
        ],
    )
    # edinet_facts.parquet は書かない（欠損 → None）

    con = duckdb.connect()
    try:
        data = collect_report_data(con, tmp_path)
    finally:
        con.close()

    counts = {(s, n): v for s, n, v in data.layer_counts}
    assert counts[("cleansed", "edinet_documents")] == 3
    assert counts[("cleansed", "edinet_facts")] is None
    assert counts[("cleansed", "jpx_stq_prices")] is None
    assert counts[("cleansed", "jpx_monthly_ohlc")] is None
    assert data.edinet_latest_date == "2026-09-10"
    assert data.edinet_company_count == 3
    assert data.jpx_latest_date is None
    assert data.jpx_code_count is None
    assert data.jpx_monthly_code_count is None


def test_collect_report_data_counts_jpx_when_present(tmp_path: Path) -> None:
    write_parquet(
        tmp_path / "jpx_stq_prices.parquet",
        [
            {"file_date": "2026-09-10", "code": "1301"},
            {"file_date": "2026-09-09", "code": "1332"},
            {"file_date": "2026-09-10", "code": "1301"},
        ],
    )

    con = duckdb.connect()
    try:
        data = collect_report_data(con, tmp_path)
    finally:
        con.close()

    counts = {(s, n): v for s, n, v in data.layer_counts}
    assert counts[("cleansed", "jpx_stq_prices")] == 3
    assert data.jpx_latest_date == "2026-09-10"
    assert data.jpx_code_count == 2


def test_collect_report_data_counts_jpx_monthly_ohlc_when_present(tmp_path: Path) -> None:
    write_parquet(
        tmp_path / "jpx_monthly_ohlc.parquet",
        [
            {"file_date": "2025-09-01", "code": "13010"},
            {"file_date": "2025-09-01", "code": "13320"},
            {"file_date": "2025-09-02", "code": "13010"},
        ],
    )

    con = duckdb.connect()
    try:
        data = collect_report_data(con, tmp_path)
    finally:
        con.close()

    counts = {(s, n): v for s, n, v in data.layer_counts}
    assert counts[("cleansed", "jpx_monthly_ohlc")] == 3
    assert data.jpx_monthly_code_count == 2


def _sample_data() -> ReportData:
    return ReportData(
        generated_at=datetime.datetime(2026, 9, 11, 4, 1, 45, tzinfo=JST),
        layer_counts=[
            ("cleansed", "edinet_documents", 93),
            ("cleansed", "edinet_facts", 10470),
            ("cleansed", "jpx_stq_prices", 4444),
            ("cleansed", "jpx_monthly_ohlc", 83195),
        ],
        edinet_latest_date="2026-09-10",
        edinet_company_count=93,
        jpx_latest_date="2026-09-10",
        jpx_code_count=4444,
        jpx_monthly_code_count=4423,
    )


def test_render_html_success_contains_counts_and_house_style() -> None:
    html = render_html(_sample_data(), _outcome(ok=True))
    assert "background: #fff" in html
    assert "✅ 成功" in html
    assert "10,470" in html
    assert "edinet_facts" in html
    assert "jpx_stq_prices" in html
    assert "jpx_monthly_ohlc" in html
    assert "2026-09-10" in html


def test_render_html_failure_marks_ng() -> None:
    html = render_html(_sample_data(), _outcome(ok=False))
    assert "❌ 失敗" in html
    assert "ERROR=3" in html


def test_summary_text_is_short_and_has_key_numbers() -> None:
    text = summary_text(_sample_data(), _outcome(ok=True))
    assert text.startswith("✅ 成功")
    assert "93社" in text
    assert "10,470行" in text
    assert "4,444行" in text
    assert "4,444銘柄" in text
    assert "83,195行" in text
    assert "4,423銘柄" in text


def test_summary_text_when_no_dbt_models() -> None:
    zero = DbtOutcome(ok=True, passed=0, warned=0, errored=0, skipped=0, duration_s=0.5)
    text = summary_text(_sample_data(), zero)
    assert "モデル未定義" in text
    assert "PASS=" not in text


def test_generate_report_end_to_end(tmp_path: Path) -> None:
    write_parquet(
        tmp_path / "edinet_documents.parquet",
        [{"file_date": "2026-09-10", "edinet_code": "E00011"}],
    )
    write_parquet(
        tmp_path / "edinet_facts.parquet",
        [{"edinet_code": "E00011", "value_num": 1000}, {"edinet_code": "E00011", "value_num": 2000}],
    )
    write_parquet(
        tmp_path / "jpx_stq_prices.parquet",
        [{"file_date": "2026-09-10", "code": "1301"}],
    )
    write_parquet(
        tmp_path / "jpx_monthly_ohlc.parquet",
        [{"file_date": "2025-09-01", "code": "13010"}, {"file_date": "2025-09-01", "code": "13320"}],
    )

    html, summary = generate_report(_outcome(ok=True), cleansed_root=str(tmp_path))

    assert "finance-dwh 実行レポート" in html
    assert summary.startswith("✅ 成功")
    assert "1社" in summary
    assert "2行" in summary
    assert "1銘柄" in summary
    assert "2銘柄" in summary
