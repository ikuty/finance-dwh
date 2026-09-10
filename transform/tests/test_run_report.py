"""run_report.py のテスト。DB アクセスは Fake で差し替える。"""

from __future__ import annotations

import datetime

import psycopg2

from report.run_report import (
    DbtOutcome,
    ReportData,
    collect_report_data,
    render_html,
    summary_text,
)

JST = datetime.timezone(datetime.timedelta(hours=9), name="JST")


class FakeCursor:
    def __init__(self, responses: dict[str, object]) -> None:
        self._responses = responses
        self._result: object = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str) -> None:
        # 最長一致のキーを採用する（同じ SQL に複数キーが部分一致しうるため）。
        matches = sorted((k for k in self._responses if k in sql), key=len, reverse=True)
        if not matches:
            self._result = None
            return
        value = self._responses[matches[0]]
        if isinstance(value, Exception):
            raise value
        self._result = value

    def fetchone(self) -> tuple[object, ...] | None:
        result = self._result
        assert result is None or isinstance(result, tuple)
        return result


class FakeConn:
    def __init__(self, cur: FakeCursor) -> None:
        self._cur = cur
        self.autocommit = False

    def cursor(self) -> FakeCursor:
        return self._cur


def _outcome(ok: bool = True) -> DbtOutcome:
    return DbtOutcome(ok=ok, passed=26, warned=0, errored=0 if ok else 3, skipped=0, duration_s=1.4)


def test_collect_report_data_counts_and_missing_relation() -> None:
    responses: dict[str, object] = {
        'select count(*) from "cleansed"."cleansed__edinet__documents"': (222,),
        'select count(*) from "cleansed"."cleansed__edinet__facts"': (9_500_000,),
        'select count(*) from "raw"."raw__jpx_file_catalog"': psycopg2.Error("does not exist"),
        "max(file_date), count(distinct edinet_code)": ("2026-09-08", 123),
        "max(period), count(*)": (None, 0),
    }
    data = collect_report_data(FakeConn(FakeCursor(responses)))

    counts = {(s, t): n for s, t, n in data.layer_counts}
    assert set(counts) == {
        ("cleansed", "cleansed__edinet__documents"),
        ("cleansed", "cleansed__edinet__facts"),
        ("raw", "raw__jpx_file_catalog"),
    }
    assert counts[("cleansed", "cleansed__edinet__facts")] == 9_500_000
    assert counts[("raw", "raw__jpx_file_catalog")] is None  # 存在しない → None
    assert data.edinet_latest_date == "2026-09-08"
    assert data.edinet_company_count == 123
    assert data.jpx_latest_date is None
    assert data.jpx_file_count == 0


def _sample_data() -> ReportData:
    return ReportData(
        generated_at=datetime.datetime(2026, 9, 11, 4, 1, 45, tzinfo=JST),
        layer_counts=[
            ("cleansed", "cleansed__edinet__documents", 222),
            ("cleansed", "cleansed__edinet__facts", 9_500_000),
            ("raw", "raw__jpx_file_catalog", 9961),
        ],
        edinet_latest_date="2026-09-08",
        edinet_company_count=123,
        jpx_latest_date=None,
        jpx_file_count=9961,
    )


def test_render_html_success_contains_counts_and_house_style() -> None:
    html = render_html(_sample_data(), _outcome(ok=True))
    assert "background: #fff" in html
    assert "✅ 成功" in html
    assert "9,500,000" in html
    assert "cleansed__edinet__facts" in html
    assert "raw__edinet_csv_facts" not in html  # FDW フルスキャン回避で含めない
    assert "2026-09-08" in html


def test_render_html_failure_marks_ng() -> None:
    html = render_html(_sample_data(), _outcome(ok=False))
    assert "❌ 失敗" in html
    assert "ERROR=3" in html


def test_summary_text_is_short_and_has_key_numbers() -> None:
    text = summary_text(_sample_data(), _outcome(ok=True))
    assert text.startswith("✅ 成功")
    assert "123社" in text
    assert "9,500,000行" in text


def test_summary_text_when_no_dbt_models() -> None:
    zero = DbtOutcome(ok=True, passed=0, warned=0, errored=0, skipped=0, duration_s=0.5)
    text = summary_text(_sample_data(), zero)
    assert "モデル未定義（raw のみ）" in text
    assert "PASS=" not in text
