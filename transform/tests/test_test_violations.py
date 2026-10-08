"""test_violations.py のテスト。duckdb接続は差し替える。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from flows.test_violations import ViolatingTest, find_notify_worthy_violations, format_violation_message

logger = logging.getLogger("test")


def _write_manifest(tmp_path: Path, nodes: dict[str, Any]) -> None:
    target = tmp_path / "target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(json.dumps({"nodes": nodes}), encoding="utf-8")


def _write_run_results(tmp_path: Path, results: list[dict[str, Any]]) -> None:
    target = tmp_path / "target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "run_results.json").write_text(json.dumps({"results": results}), encoding="utf-8")


def _test_node(notify_slack: bool = True) -> dict[str, Any]:
    return {"resource_type": "test", "config": {"meta": {"notify_slack": notify_slack}}}


def test_returns_empty_when_manifest_missing(tmp_path: Path) -> None:
    assert find_notify_worthy_violations(str(tmp_path), "unused.duckdb", logger) == []


def test_ignores_tests_without_notify_slack_meta(tmp_path: Path, monkeypatch: Any) -> None:
    _write_manifest(tmp_path, {"test.p.other_test": _test_node(notify_slack=False)})
    _write_run_results(
        tmp_path,
        [{"unique_id": "test.p.other_test", "status": "warn", "failures": 3, "compiled_code": "select 1"}],
    )
    assert find_notify_worthy_violations(str(tmp_path), "unused.duckdb", logger) == []


def test_ignores_passing_or_zero_failure_results(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        {
            "test.p.a": _test_node(),
            "test.p.b": _test_node(),
        },
    )
    _write_run_results(
        tmp_path,
        [
            {"unique_id": "test.p.a", "status": "pass", "failures": 0, "compiled_code": "select 1"},
            {"unique_id": "test.p.b", "status": "warn", "failures": 0, "compiled_code": "select 1"},
        ],
    )
    assert find_notify_worthy_violations(str(tmp_path), "unused.duckdb", logger) == []


def test_collects_violation_with_sample_rows(tmp_path: Path, monkeypatch: Any) -> None:
    _write_manifest(tmp_path, {"test.finance_dwh.assert_foo": _test_node()})
    _write_run_results(
        tmp_path,
        [
            {
                "unique_id": "test.finance_dwh.assert_foo",
                "status": "warn",
                "failures": 2,
                "compiled_code": "select edinet_code, eps from mart",
            }
        ],
    )

    class FakeCursor:
        description = [("edinet_code",), ("eps",)]

        def fetchall(self) -> list[tuple[Any, ...]]:
            return [("E00001", 123.0), ("E00002", 456.0)]

    class FakeConnection:
        def execute(self, sql: str) -> FakeCursor:
            assert sql == "select edinet_code, eps from mart"
            return FakeCursor()

        def close(self) -> None:
            pass

    monkeypatch.setattr("duckdb.connect", lambda path, read_only=False: FakeConnection())

    violations = find_notify_worthy_violations(str(tmp_path), "fake.duckdb", logger)
    assert violations == [
        ViolatingTest(
            test_name="assert_foo",
            failures=2,
            sample_rows=[{"edinet_code": "E00001", "eps": 123.0}, {"edinet_code": "E00002", "eps": 456.0}],
        )
    ]


def test_handles_duckdb_failure_without_raising(tmp_path: Path, monkeypatch: Any) -> None:
    _write_manifest(tmp_path, {"test.finance_dwh.assert_foo": _test_node()})
    _write_run_results(
        tmp_path,
        [
            {
                "unique_id": "test.finance_dwh.assert_foo",
                "status": "warn",
                "failures": 1,
                "compiled_code": "select 1",
            }
        ],
    )

    def boom(path: str, read_only: bool = False) -> None:
        raise RuntimeError("db locked")

    monkeypatch.setattr("duckdb.connect", boom)

    violations = find_notify_worthy_violations(str(tmp_path), "fake.duckdb", logger)
    assert violations == [ViolatingTest(test_name="assert_foo", failures=1, sample_rows=[])]


def test_format_violation_message_includes_remaining_count() -> None:
    violation = ViolatingTest(
        test_name="assert_foo",
        failures=3,
        sample_rows=[{"edinet_code": "E00001", "eps": 123.0}],
    )
    message = format_violation_message(violation)
    assert "assert_foo: 3件" in message
    assert "edinet_code=E00001, eps=123.0" in message
    assert "他2件" in message
