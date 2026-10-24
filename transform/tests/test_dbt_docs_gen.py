"""dbt_docs_gen.py のテスト。subprocess / boto3 は差し替える。"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from flows import dbt_docs_gen

logger = logging.getLogger("test")


# --- run_docs_generate ----------------------------------------------------------


def test_run_docs_generate_returns_true_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], capture_output: bool, text: bool, check: bool) -> Any:
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="Catalog written to ...\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert dbt_docs_gen.run_docs_generate(logger) is True


def test_run_docs_generate_returns_false_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], capture_output: bool, text: bool, check: bool) -> Any:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="Compilation Error\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert dbt_docs_gen.run_docs_generate(logger) is False


# --- upload_docs_to_s3 -----------------------------------------------------------


def test_upload_docs_returns_none_when_bucket_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    assert dbt_docs_gen.upload_docs_to_s3(logger) is None


def test_upload_docs_puts_existing_files_and_returns_website_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "index.html").write_text("<html></html>")
    (target_dir / "manifest.json").write_text("{}")
    # catalog.json / run_results.json / semantic_manifest.json は作らない(欠けていてもよい)

    monkeypatch.setenv("S3_BUCKET_NAME", "ikuty-finance")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setattr(dbt_docs_gen, "DBT_PROJECT_DIR", str(tmp_path))

    calls: list[dict[str, Any]] = []

    class FakeS3:
        def put_object(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    monkeypatch.setattr("boto3.client", lambda service: FakeS3())

    url = dbt_docs_gen.upload_docs_to_s3(logger)

    assert url == "http://ikuty-finance.s3-website-ap-northeast-1.amazonaws.com/finance-dwh/docs/index.html"
    assert len(calls) == 2
    keys = {c["Key"] for c in calls}
    assert keys == {"finance-dwh/docs/index.html", "finance-dwh/docs/manifest.json"}
    for c in calls:
        assert c["Bucket"] == "ikuty-finance"


def test_upload_docs_returns_none_and_logs_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_BUCKET_NAME", "ikuty-finance")
    monkeypatch.setattr(dbt_docs_gen, "DBT_PROJECT_DIR", str(tmp_path))

    def boom(service: str) -> None:
        raise RuntimeError("no creds")

    monkeypatch.setattr("boto3.client", boom)
    assert dbt_docs_gen.upload_docs_to_s3(logger) is None


# --- main --------------------------------------------------------------------------


def test_main_exits_nonzero_when_docs_generate_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dbt_docs_gen, "run_docs_generate", lambda logger: False)
    monkeypatch.setattr(dbt_docs_gen, "upload_docs_to_s3", lambda logger: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        dbt_docs_gen.main()
    assert exc_info.value.code == 1


def test_main_does_not_exit_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dbt_docs_gen, "run_docs_generate", lambda logger: True)
    monkeypatch.setattr(dbt_docs_gen, "upload_docs_to_s3", lambda logger: "http://example/index.html")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    dbt_docs_gen.main()  # 例外(SystemExit含む)を投げないこと
