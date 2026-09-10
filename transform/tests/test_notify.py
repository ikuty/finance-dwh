"""notify.py のテスト。boto3 / urllib は差し替える。"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from flows import notify

logger = logging.getLogger("test")


def test_upload_report_returns_none_when_bucket_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    assert notify.upload_report_to_s3("<html>", logger) is None


def test_upload_report_puts_object_and_returns_website_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_BUCKET_NAME", "ikuty-finance")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    calls: list[dict[str, Any]] = []

    class FakeS3:
        def put_object(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    monkeypatch.setattr("boto3.client", lambda service: FakeS3())

    url = notify.upload_report_to_s3("<html>body</html>", logger)
    assert url == "http://ikuty-finance.s3-website-ap-northeast-1.amazonaws.com/finance-dwh/run_report.html"
    assert calls[0]["Bucket"] == "ikuty-finance"
    assert calls[0]["Key"] == "finance-dwh/run_report.html"
    assert calls[0]["Body"] == b"<html>body</html>"
    assert calls[0]["ContentType"] == "text/html; charset=utf-8"


def test_upload_report_returns_none_and_logs_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_BUCKET_NAME", "ikuty-finance")

    def boom(service: str) -> None:
        raise RuntimeError("no creds")

    monkeypatch.setattr("boto3.client", boom)
    assert notify.upload_report_to_s3("<html>", logger) is None


def test_send_slack_notification_posts_expected_json(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class FakeResp:
        def __enter__(self) -> "FakeResp":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b"ok"

    def fake_urlopen(req: Any, timeout: float = 0) -> FakeResp:
        seen["url"] = req.full_url
        seen["headers"] = req.headers
        seen["data"] = req.data
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    notify.send_slack_notification("https://hooks.slack.test/abc", "こんにちは", logger)

    assert seen["url"] == "https://hooks.slack.test/abc"
    assert seen["headers"]["Content-type"] == "application/json"
    assert json.loads(seen["data"]) == {
        "text": "こんにちは",
        "unfurl_links": False,
        "unfurl_media": False,
    }


def test_send_slack_notification_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req: Any, timeout: float = 0) -> None:
        raise OSError("network down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    # 例外を投げないこと
    notify.send_slack_notification("https://hooks.slack.test/abc", "x", logger)
