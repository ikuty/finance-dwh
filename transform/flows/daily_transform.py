"""finance-dwh の日次変換フロー。

Prefect の ephemeral モードで単発実行する（常駐サーバ・ワーカーは持たない）:
    python -m flows.daily_transform

流れ: Postgres 起動待ち → landing 取り込み → dbt build → 実行レポート生成 →
S3 アップロード → Slack 通知。dbt が失敗してもレポート生成・S3・Slack までは実行し、
最後に非ゼロ終了する。
"""

from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass

from prefect import flow, get_run_logger, task

from flows.load_edinet import load_edinet_csv_facts
from flows.notify import send_slack_notification, upload_report_to_s3
from report.run_report import DbtOutcome, generate_report

DBT_PROJECT_DIR = os.environ.get("DBT_PROJECT_DIR", "/app/dbt")
DBT_PROFILES_DIR = os.environ.get("DBT_PROFILES_DIR", DBT_PROJECT_DIR)
DBT_TARGET = os.environ.get("DBT_TARGET", "prod")
DB_HOST = os.environ.get("DBT_HOST", "postgres")
DB_PORT = int(os.environ.get("DBT_PORT", "5432"))

# dbt の最終行: "Done. PASS=26 WARN=0 ERROR=0 SKIP=0 NO-OP=0 TOTAL=26"
_SUMMARY_RE = re.compile(r"PASS=(\d+)\s+WARN=(\d+)\s+ERROR=(\d+)\s+SKIP=(\d+)")


def parse_dbt_summary(output: str) -> tuple[int, int, int, int]:
    """dbt の出力から (PASS, WARN, ERROR, SKIP) を取り出す。見つからなければ全て 0。"""
    match = _SUMMARY_RE.search(output)
    if match is None:
        return (0, 0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)))


@dataclass
class DbtBuildResult:
    returncode: int
    passed: int
    warned: int
    errored: int
    skipped: int
    duration_s: float
    tail: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@task
def wait_for_postgres(timeout_s: float = 60.0, interval_s: float = 2.0) -> None:
    """Postgres が TCP 接続を受け付けるまで待つ。"""
    logger = get_run_logger()
    deadline = time.monotonic() + timeout_s
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((DB_HOST, DB_PORT), timeout=3):
                logger.info(f"postgres {DB_HOST}:{DB_PORT} 応答あり")
                return
        except OSError as e:
            last_error = e
            time.sleep(interval_s)
    raise RuntimeError(f"postgres {DB_HOST}:{DB_PORT} に {timeout_s:.0f}s 以内に接続できなかった: {last_error}")


@task
def dbt_build() -> DbtBuildResult:
    """dbt build を実行し、結果を解析して返す（例外は投げない）。"""
    logger = get_run_logger()
    cmd = [
        "dbt",
        "build",
        "--project-dir",
        DBT_PROJECT_DIR,
        "--profiles-dir",
        DBT_PROFILES_DIR,
        "--target",
        DBT_TARGET,
    ]
    logger.info("実行: " + " ".join(cmd))
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    duration = time.monotonic() - started

    output = proc.stdout + proc.stderr
    lines = output.splitlines()
    for line in lines:
        logger.info(line)

    passed, warned, errored, skipped = parse_dbt_summary(output)

    return DbtBuildResult(
        returncode=proc.returncode,
        passed=passed,
        warned=warned,
        errored=errored,
        skipped=skipped,
        duration_s=duration,
        tail="\n".join(lines[-20:]),
    )


def build_summary_text(result: DbtBuildResult) -> str:
    status = "✅ 成功" if result.ok else "❌ 失敗"
    return (
        f"{status} / finance-dwh 日次変換 / "
        f"PASS={result.passed} WARN={result.warned} ERROR={result.errored} SKIP={result.skipped} / "
        f"{result.duration_s:.1f}s"
    )


def _outcome(result: DbtBuildResult) -> DbtOutcome:
    return DbtOutcome(
        ok=result.ok,
        passed=result.passed,
        warned=result.warned,
        errored=result.errored,
        skipped=result.skipped,
        duration_s=result.duration_s,
    )


@task
def build_report(result: DbtBuildResult) -> tuple[str, str]:
    """実行レポート HTML と Slack 用サマリを作る。DB 接続不可等でも落とさない。"""
    logger = get_run_logger()
    try:
        return generate_report(_outcome(result))
    except Exception as e:  # noqa: BLE001
        logger.error(f"実行レポートの生成に失敗しました: {e}")
        return "", build_summary_text(result)


@task
def publish_and_notify(html: str, summary: str) -> str:
    """レポートを S3 へ上げ、URL があればサマリに付けて Slack へ通知する。返り値は最終サマリ。"""
    logger = get_run_logger()
    std_logger = logging.getLogger("finance-dwh.notify")

    report_url = upload_report_to_s3(html, std_logger) if html else None
    if report_url:
        summary = summary + "\n\n📊 実行レポート: " + report_url
        logger.info(f"実行レポートをアップロードしました: {report_url}")

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        send_slack_notification(webhook, summary, std_logger)
        logger.info("Slack 通知を送信しました")
    else:
        logger.info("SLACK_WEBHOOK_URL 未設定のため Slack 通知はスキップ")
    return summary


@flow(name="finance-dwh-daily-transform")
def daily_transform() -> str:
    logger = get_run_logger()
    wait_for_postgres()

    loaded = load_edinet_csv_facts()
    logger.info(f"landing 取り込み: {loaded['dates']} 日 / {loaded['rows']} 行")

    result = dbt_build()

    html, summary = build_report(result)
    summary = publish_and_notify(html, summary)

    logger.info(summary)
    if not result.ok:
        raise RuntimeError(summary + "\n\n" + result.tail)
    return summary


if __name__ == "__main__":
    print(daily_transform())
