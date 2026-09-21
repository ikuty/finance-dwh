"""daily_transform.py の純粋関数（dbt サマリ解析・サマリ文言）＋ ensure_data_dirs のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from flows.daily_transform import DbtBuildResult, build_summary_text, ensure_data_dirs, parse_dbt_summary


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


def test_ensure_data_dirs_creates_landing_cleansed_mart_and_duckdb_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    landing = tmp_path / "d1" / "landing"
    cleansed = tmp_path / "d2" / "cleansed"
    mart = tmp_path / "d4" / "mart"
    intermediate = tmp_path / "d5" / "intermediate"
    duckdb_path = tmp_path / "d3" / "sub" / "finance_dwh.duckdb"
    monkeypatch.setenv("LANDING_ROOT", str(landing))
    monkeypatch.setenv("CLEANSED_ROOT", str(cleansed))
    monkeypatch.setenv("INTERMEDIATE_ROOT", str(intermediate))
    monkeypatch.setenv("MART_ROOT", str(mart))
    monkeypatch.setenv("DUCKDB_PATH", str(duckdb_path))

    ensure_data_dirs()

    assert landing.is_dir()
    assert cleansed.is_dir()
    assert intermediate.is_dir()
    assert mart.is_dir()
    assert duckdb_path.parent.is_dir()
    assert not duckdb_path.exists()  # ファイル自体は作らない、親ディレクトリだけ
