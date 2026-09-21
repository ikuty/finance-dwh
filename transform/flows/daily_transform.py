"""finance-dwh の日次変換フロー。

Prefect の ephemeral モードで単発実行する（常駐サーバ・ワーカーは持たない）:
    python -m flows.daily_transform

流れ: landing 取り込み(EDINET/JPX形式C・直近分のみ、mufg-corporate-actions) →
dbt build → 実行レポート生成 → S3 アップロード → Slack 通知 → landing 取り込み
(JPX形式C・バックログ、JPX形式B・過去分バックフィル)。mufg-corporate-actionsは
週次・軽量なためrecent/backlog分割対象外で常にdbt buildより前に置く。
DuckDB は組み込み型（サーバなし）のため、
Postgres 版にあった起動待ちは無い。dbt が失敗してもレポート生成・S3・Slack
までは実行し、最後に非ゼロ終了する。

**「バックログ・過去分バックフィル系のタスクは、dbt build/レポート/Slack通知
より後に置く」という設計原則**（2026-09-14〜15、実機障害を踏まえて確立）。
Mac Miniの Tapoスケジュール電源は固定2時間枠でシステムの`shutdown`より先に
物理的に電源を落とすため、`finance-lake-shutdown.service`の「実行中のジョブを
待ってからシャットダウン」という設計は機能しない（そもそも起動されない）。
処理量が不定・大きくなりうるタスク(バックログ・バックフィル)をdbt build等より
前に置くと、それが長引いた回はdbt build/レポート/Slack通知が一度も実行されずに
電源が落ちる。実際に2日連続でこの障害が実機発生した:
  - 2026-09-13: JPX形式B(過去分バックフィル)が長引き、Slack通知が飛ばず。
    → 形式Bを最後に回して解消(2026-09-14)。
  - 2026-09-14夜間: レイク層のPDFバックフィル直後でJPX形式Cのバックログが
    数百日分に膨れ上がり(1日あたりPDF解析に約2分半)、同じ理由でSlack通知が
    飛ばず。→ JPX形式Cを「直近LANDING_LOOKBACK_DAYS日(保証枠、サイズ小・
    dbt buildより前)」と「それより前のバックログ(サイズ不定・dbt build等より
    後)」の2タスクに分割して解消(2026-09-15、詳細はload_jpx_stq.py参照)。
  - 2026-09-15: 同じ理由でedinet-dlも先回りして分割。レイク層で2022年分
    （365日）を一括バックフィルした直後で、DWH側は2022年分を1日も取り込んで
    いなかったため、対策前のまま翌日実行すればJPX形式Cと同型の障害が起きる
    ことが判明した。EDINETもload_edinet_csv_facts_recent(保証枠)と
    load_edinet_csv_facts_backlog(バックログ)の2タスクに分割した
    (詳細はload_edinet.py参照)。
どちらの場合も、電源枠が尽きて処理が中断されてもlandingのアトミック書き込みに
より安全に次回実行へ持ち越せる。日次の本質的な処理(EDINET・JPX形式Cの直近分・
dbt build・レポート・Slack通知)は毎回確実に完了する。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from prefect import flow, get_run_logger, task

from flows.load_edinet import load_edinet_csv_facts_backlog, load_edinet_csv_facts_recent
from flows.load_jpx_monthly_ohlc import load_jpx_monthly_ohlc_facts
from flows.load_jpx_stq import load_jpx_stq_prices_backlog, load_jpx_stq_prices_recent
from flows.load_mufg_corporate_actions import load_mufg_corporate_actions
from flows.notify import send_slack_notification, upload_report_to_s3
from report.run_report import DbtOutcome, generate_report

DBT_PROJECT_DIR = os.environ.get("DBT_PROJECT_DIR", "/app/dbt")
DBT_PROFILES_DIR = os.environ.get("DBT_PROFILES_DIR", DBT_PROJECT_DIR)
DBT_TARGET = os.environ.get("DBT_TARGET", "prod")

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
def ensure_data_dirs() -> None:
    """landing・cleansed・intermediate・mart・.duckdb カタログの出力先ディレクトリを用意する。

    dbt の external materialization（Parquet 書き出し）は親ディレクトリを
    自動作成しないため、無いと `IO Error: Cannot open file` で落ちる。
    """
    for env_var, default in (
        ("LANDING_ROOT", "/data/landing"),
        ("CLEANSED_ROOT", "/data/cleansed"),
        ("INTERMEDIATE_ROOT", "/data/intermediate"),
        ("MART_ROOT", "/data/mart"),
    ):
        Path(os.environ.get(env_var, default)).mkdir(parents=True, exist_ok=True)
    duckdb_path = Path(os.environ.get("DUCKDB_PATH", "/data/finance_dwh.duckdb"))
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)


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
    ensure_data_dirs()

    loaded = load_edinet_csv_facts_recent()
    logger.info(f"landing 取り込み(EDINET・直近分): {loaded['dates']} 日 / {loaded['rows']} 行")

    jpx_loaded = load_jpx_stq_prices_recent()
    logger.info(f"landing 取り込み(JPX形式C・直近分): {jpx_loaded['dates']} 日 / {jpx_loaded['rows']} 銘柄")

    # mufg-corporate-actionsは週次・3ファイルのみで処理量が常に小さいため、
    # recent/backlog分割は不要（load_mufg_corporate_actions.py参照）。
    mufg_loaded = load_mufg_corporate_actions()
    logger.info(
        f"landing 取り込み(mufg-corporate-actions): {mufg_loaded['dates']} 日 / "
        f"分割{mufg_loaded.get('mufg_stock_splits', 0)}件 / "
        f"併合{mufg_loaded.get('mufg_stock_consolidations', 0)}件 / "
        f"商号変更{mufg_loaded.get('mufg_company_name_changes', 0)}件"
    )

    result = dbt_build()

    html, summary = build_report(result)
    summary = publish_and_notify(html, summary)

    # バックログ・過去分バックフィル系は日次の本質的な処理より後に回す(理由は
    # モジュールdocstring参照)。ここで電源枠が尽きて中断されても、landingは
    # アトミック書き込みのため安全に次回実行へ持ち越される。
    edinet_backlog_loaded = load_edinet_csv_facts_backlog()
    logger.info(
        f"landing 取り込み(EDINET・バックログ): {edinet_backlog_loaded['dates']} 日 / "
        f"{edinet_backlog_loaded['rows']} 行"
    )

    jpx_backlog_loaded = load_jpx_stq_prices_backlog()
    logger.info(
        f"landing 取り込み(JPX形式C・バックログ): {jpx_backlog_loaded['dates']} 日 / "
        f"{jpx_backlog_loaded['rows']} 銘柄"
    )

    jpx_monthly_loaded = load_jpx_monthly_ohlc_facts()
    logger.info(
        f"landing 取り込み(JPX形式B): {jpx_monthly_loaded['months']} ヶ月 / "
        f"{jpx_monthly_loaded['days']} 日 / {jpx_monthly_loaded['rows']} 行"
    )

    logger.info(summary)
    if not result.ok:
        raise RuntimeError(summary + "\n\n" + result.tail)
    return summary


if __name__ == "__main__":
    print(daily_transform())
