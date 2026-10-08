"""dbtのテスト結果からmeta.notify_slack=trueのテストを検出し、該当行を
添えてSlack通知するためのデータを集める（human-in-the-loop、2026-10-09導入）。

通知対象にしたいテストは、そのテスト自身の`config()`に
`meta={'notify_slack': true}`を付けるだけでよい(課題1のtag方式deny-listと
同じ考え方。「通知すべきか」をテスト本体から切り離した別のリストで管理すると、
将来テスト追加時の記入漏れに気づけないため。dbt/tests/assert_mart__edinet__
financial_indicators_eps_bps_plausible.sqlが最初の採用例)。

dbt build実行後に生成される target/manifest.json（各テストのmeta）と
target/run_results.json（今回の実行結果。dbt 1.8+ではcompiled_codeに
ref解決済みのSQLそのものが含まれる）を突き合わせ、status='warn'かつ
meta.notify_slack=trueのテストだけ、そのcompiled_codeを再実行して該当行を
取得する。compiled_codeはdbtの永続カタログ（DUCKDB_PATH）のview名
（"finance_dwh"."mart"."..."等）を参照するため、read_parquet直読みの他flowとは
異なりこのカタログファイルへ接続する必要がある。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

_SAMPLE_ROW_LIMIT = 10


@dataclass
class ViolatingTest:
    test_name: str
    failures: int
    sample_rows: list[dict[str, object]] = field(default_factory=list)


def find_notify_worthy_violations(
    project_dir: str, duckdb_path: str, logger: logging.Logger
) -> list[ViolatingTest]:
    """meta.notify_slack=trueかつ今回status='warn'(0件超)だったテストの
    違反行を集める。manifest.json/run_results.jsonが無い・壊れている等は
    空リストを返す（通知系はジョブ全体を落とさない）。
    """
    target_dir = Path(project_dir) / "target"
    try:
        manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
        run_results = json.loads((target_dir / "run_results.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - 通知系はジョブを落とさない
        logger.error(f"dbtのmanifest/run_results読み込みに失敗しました: {e}")
        return []

    notify_worthy_ids = {
        uid
        for uid, node in manifest.get("nodes", {}).items()
        if node.get("resource_type") == "test" and node.get("config", {}).get("meta", {}).get("notify_slack")
    }

    violations: list[ViolatingTest] = []
    for result in run_results.get("results", []):
        uid = result.get("unique_id")
        failures = result.get("failures") or 0
        if uid not in notify_worthy_ids or result.get("status") != "warn" or failures == 0:
            continue

        test_name = str(uid).rsplit(".", maxsplit=1)[-1]
        compiled_code = result.get("compiled_code")
        sample_rows: list[dict[str, object]] = []
        if not compiled_code:
            logger.error(f"{test_name}: compiled_codeが無く該当行を取得できません")
        else:
            try:
                con = duckdb.connect(duckdb_path, read_only=True)
                try:
                    cur = con.execute(compiled_code)
                    cols = [c[0] for c in cur.description]
                    sample_rows = [
                        dict(zip(cols, row, strict=True)) for row in cur.fetchall()[:_SAMPLE_ROW_LIMIT]
                    ]
                finally:
                    con.close()
            except Exception as e:  # noqa: BLE001
                logger.error(f"{test_name}: 該当行の再取得に失敗しました: {e}")

        violations.append(ViolatingTest(test_name=test_name, failures=failures, sample_rows=sample_rows))
    return violations


def format_violation_message(violation: ViolatingTest) -> str:
    lines = [f"⚠️ {violation.test_name}: {violation.failures}件"]
    for row in violation.sample_rows:
        lines.append("  " + ", ".join(f"{k}={v}" for k, v in row.items()))
    shown = len(violation.sample_rows)
    if violation.failures > shown:
        lines.append(f"  …他{violation.failures - shown}件")
    return "\n".join(lines)
