"""実行レポートの S3 アップロードと Slack 通知。

レイク(finance-lake)の fetch_documents.py と同じ実装・同じ設計方針:
- 失敗はログに記録するのみで例外は上げない（ジョブ全体の成否に影響させない）
- Slack は unfurl_links/unfurl_media を無効化（URL の大きなプレビューでテキストが
  埋もれるのを避ける。finance-lake で実機確認済み）
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

DEFAULT_S3_REGION = "ap-northeast-1"
S3_REPORT_KEY = "finance-dwh/run_report.html"


def upload_report_to_s3(html: str, logger: logging.Logger) -> str | None:
    """レポート HTML を S3 へ置き、公開 URL（静的サイトホスティング経由）を返す。
    `S3_BUCKET_NAME` が未設定、またはアップロード失敗時は None。"""
    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        return None

    region = os.environ.get("AWS_DEFAULT_REGION", DEFAULT_S3_REGION)
    try:
        import boto3

        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=bucket,
            Key=S3_REPORT_KEY,
            Body=html.encode("utf-8"),
            ContentType="text/html; charset=utf-8",
        )
        return f"http://{bucket}.s3-website-{region}.amazonaws.com/{S3_REPORT_KEY}"
    except Exception as e:  # noqa: BLE001 - 通知系はジョブを落とさない
        logger.error(f"実行レポートの S3 アップロードに失敗しました: {e}")
        return None


def send_slack_notification(webhook_url: str, message: str, logger: logging.Logger) -> None:
    """Slack Incoming Webhook にテキストを投げる。失敗はログのみ。"""
    try:
        payload = json.dumps(
            {"text": message, "unfurl_links": False, "unfurl_media": False}
        ).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 - 固定の Webhook URL
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            resp.read()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Slack 通知の送信に失敗しました: {e}")
