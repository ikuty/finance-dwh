"""daily_transform.py の純粋関数（dbt サマリ解析・サマリ文言）のテスト。"""

from __future__ import annotations

from flows.daily_transform import DbtBuildResult, build_summary_text, parse_dbt_summary


def test_parse_dbt_summary_reads_counts() -> None:
    out = "13:28:08  Done. PASS=26 WARN=0 ERROR=0 SKIP=0 NO-OP=0 TOTAL=26\n"
    assert parse_dbt_summary(out) == (26, 0, 0, 0)


def test_parse_dbt_summary_with_failures() -> None:
    out = "... Done. PASS=20 WARN=1 ERROR=3 SKIP=2 TOTAL=26"
    assert parse_dbt_summary(out) == (20, 1, 3, 2)


def test_parse_dbt_summary_missing_line_returns_zeros() -> None:
    assert parse_dbt_summary("crashed before summary") == (0, 0, 0, 0)


def test_build_summary_text_success() -> None:
    r = DbtBuildResult(returncode=0, passed=26, warned=0, errored=0, skipped=0, duration_s=1.4, tail="")
    text = build_summary_text(r)
    assert text.startswith("✅ 成功")
    assert "PASS=26" in text and "ERROR=0" in text


def test_build_summary_text_failure() -> None:
    r = DbtBuildResult(returncode=1, passed=20, warned=0, errored=3, skipped=2, duration_s=9.9, tail="")
    assert build_summary_text(r).startswith("❌ 失敗")
