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
        'select count(*) from "raw"."edinet_csv_facts"': (41902,),
        'select count(*) from "raw"."edinet_document_index"': (335,),
        'select count(*) from "raw"."jpx_file_catalog"': (0,),
        'select count(*) from "cleansed"."cleansed_edinet__documents"': (222,),
        'select count(*) from "cleansed"."cleansed_edinet__facts"': (40740,),
        'select count(*) from "cleansed"."cleansed_jpx__files"': (0,),
        'select count(*) from "mart"."mart_company"': (123,),
        'select count(*) from "mart"."mart_financial_facts"': psycopg2.Error("does not exist"),
        "max(file_date), count(distinct edinet_code)": ("2026-08-13", 123),
        "max(period_date), count(*)": (None, 0),
    }
    data = collect_report_data(FakeConn(FakeCursor(responses)))

    counts = {(s, t): n for s, t, n in data.layer_counts}
    assert counts[("raw", "edinet_csv_facts")] == 41902
    assert counts[("mart", "mart_financial_facts")] is None  # 存在しない → None
    assert data.edinet_latest_date == "2026-08-13"
    assert data.edinet_company_count == 123
    assert data.jpx_latest_date is None
    assert data.jpx_file_count == 0


def _sample_data() -> ReportData:
    return ReportData(
        generated_at=datetime.datetime(2026, 9, 11, 4, 1, 45, tzinfo=JST),
        layer_counts=[
            ("raw", "edinet_csv_facts", 41902),
            ("mart", "mart_financial_facts", 4175),
            ("mart", "mart_company", 123),
        ],
        edinet_latest_date="2026-08-13",
        edinet_company_count=123,
        jpx_latest_date=None,
        jpx_file_count=0,
    )


def test_render_html_success_contains_counts_and_house_style() -> None:
    html = render_html(_sample_data(), _outcome(ok=True))
    assert "background: #fff" in html
    assert "✅ 成功" in html
    assert "41,902" in html
    assert "mart_financial_facts" in html
    assert "2026-08-13" in html


def test_render_html_failure_marks_ng() -> None:
    html = render_html(_sample_data(), _outcome(ok=False))
    assert "❌ 失敗" in html
    assert "ERROR=3" in html


def test_summary_text_is_short_and_has_key_numbers() -> None:
    text = summary_text(_sample_data(), _outcome(ok=True))
    assert text.startswith("✅ 成功")
    assert "123社" in text
    assert "4,175行" in text
