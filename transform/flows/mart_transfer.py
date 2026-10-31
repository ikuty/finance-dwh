"""dbt build 完了後、mart の Parquet を ikuty.com（VPS）へ転送する。

サービスチェーン上は finance-dwh-transform の後、dbt-docs-gen の前に置く専用サービス
（compose の mart-transfer サービス、systemd の finance-dwh-mart-transfer）。VPS 上の
MCP サーバ（finance-poc/mcp-mart）は「常に最新の mart」を前提に動くため、転送の
目的は「VPS 側の `current` を、検証済みの完全な mart 一式へ原子的に切り替える」こと。

流れ（いずれかが失敗したら VPS の current は一切変わらない）:
  1. daily_transform が書いたビルド結果マーカー（MART_ROOT/_BUILD_RESULT.json）を確認。
     dbt build が失敗した日・マーカーが古い日（transform が走っていない）は送らない
  2. MART_ROOT/*.parquet を DuckDB で読み、空・破損が無いことを確認、sha256 付きの
     MANIFEST.json を作る
  3. rsync で VPS の incoming/<run_id>/ へ送る（SSH 鍵認証、ホスト鍵固定）
  4. VPS 側の `activate <run_id>` を呼ぶ（manifest と sha256 を再検証 → releases/ へ移動
     → current symlink を原子的に切替。vps/activate_mart.sh）

Prefect は使わない（dbt_docs_gen.py と同じ判断: ステップが少なく、ephemeral サーバの
起動コストに見合わない）。

設定（compose の environment 経由。MART_TRANSFER_HOST 未設定なら何もせず終了する）:
    MART_TRANSFER_HOST            ikuty.com（必須）
    MART_TRANSFER_USER            VPS 側の受信専用ユーザー（既定 martsync）
    MART_TRANSFER_PORT            SSH ポート（既定 22）
    MART_TRANSFER_SSH_KEY         秘密鍵のパス（既定 /ssh/id_ed25519）
    MART_TRANSFER_KNOWN_HOSTS     known_hosts のパス（既定 /ssh/known_hosts）
    MART_TRANSFER_MAX_BUILD_AGE_HOURS  ビルド結果マーカーの許容経過時間（既定 6）

Usage:
    python -m flows.mart_transfer
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from flows.notify import send_slack_notification

MART_ROOT = Path(os.environ.get("MART_ROOT", "/data/mart"))
BUILD_MARKER_NAME = "_BUILD_RESULT.json"
MANIFEST_NAME = "MANIFEST.json"
RSYNC_ATTEMPTS = 3
RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z$")


@dataclass(frozen=True)
class TransferConfig:
    host: str
    user: str
    port: int
    ssh_key: str
    known_hosts: str
    max_build_age_hours: float

    @classmethod
    def from_env(cls) -> TransferConfig | None:
        host = os.environ.get("MART_TRANSFER_HOST", "")
        if not host:
            return None
        return cls(
            host=host,
            user=os.environ.get("MART_TRANSFER_USER", "martsync"),
            port=int(os.environ.get("MART_TRANSFER_PORT", "22")),
            ssh_key=os.environ.get("MART_TRANSFER_SSH_KEY", "/ssh/id_ed25519"),
            known_hosts=os.environ.get("MART_TRANSFER_KNOWN_HOSTS", "/ssh/known_hosts"),
            max_build_age_hours=float(os.environ.get("MART_TRANSFER_MAX_BUILD_AGE_HOURS", "6")),
        )

    @property
    def remote(self) -> str:
        return f"{self.user}@{self.host}"

    def ssh_command(self) -> list[str]:
        # BatchMode: パスワード入力待ちで固まらない。StrictHostKeyChecking=yes +
        # 固定の known_hosts: 初回接続での自動受け入れ（中間者攻撃の余地）を作らない。
        return [
            "ssh",
            "-i", self.ssh_key,
            "-p", str(self.port),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", "ConnectTimeout=20",
        ]  # fmt: skip


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("mart-transfer")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger  # 同一プロセス内で複数回呼ばれても二重登録しない（テスト等）

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


# --- ビルド結果マーカー（daily_transform が書き、ここで読む） -----------------------


def write_build_marker(ok: bool, mart_root: Path | None = None) -> None:
    """dbt build の成否を MART_ROOT に記録する（daily_transform から呼ぶ）。

    mart は external materialization のため、dbt build が一部失敗しても成功分の
    Parquet は更新され、失敗分は古いファイルが残る。ファイルの存在だけでは「今日の
    完全な mart」かどうか判定できないので、成否を明示的に残す。
    """
    root = mart_root or MART_ROOT
    root.mkdir(parents=True, exist_ok=True)
    marker = {"ok": ok, "finished_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    tmp = root / (BUILD_MARKER_NAME + ".tmp")
    tmp.write_text(json.dumps(marker))
    tmp.replace(root / BUILD_MARKER_NAME)


def check_build_marker(
    mart_root: Path, max_age_hours: float, now: dt.datetime | None = None
) -> str | None:
    """転送してよければ None、見送る場合はその理由を返す。"""
    path = mart_root / BUILD_MARKER_NAME
    if not path.exists():
        return f"{BUILD_MARKER_NAME} が無い（今日の dbt build が完了していない）"
    try:
        marker = json.loads(path.read_text())
        finished_at = dt.datetime.fromisoformat(marker["finished_at"])
        ok = bool(marker["ok"])
    except (ValueError, KeyError, TypeError) as e:
        return f"{BUILD_MARKER_NAME} を読めない: {e}"
    if not ok:
        return "直近の dbt build が失敗している"
    age = (now or dt.datetime.now(dt.UTC)) - finished_at
    if age > dt.timedelta(hours=max_age_hours):
        return f"ビルド結果が古い（{age.total_seconds() / 3600:.1f} 時間前、上限 {max_age_hours} 時間）"
    return None


# --- 検証・manifest -------------------------------------------------------------


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def count_rows(path: Path) -> int:
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        row = con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def build_manifest(files: list[Path], run_id: str) -> dict[str, object]:
    """全ファイルを読んで検証し manifest を作る。空・破損があれば ValueError。"""
    if not files:
        raise ValueError(f"{MART_ROOT} に *.parquet が無い")
    entries = []
    for path in files:
        try:
            rows = count_rows(path)
        except Exception as e:  # noqa: BLE001 - DuckDB は多様な例外を投げる
            raise ValueError(f"{path.name}: Parquet として読めない: {e}") from e
        if rows == 0:
            raise ValueError(f"{path.name}: 0 行（空の mart は送らない）")
        entries.append(
            {"name": path.name, "size": path.stat().st_size, "sha256": sha256_of(path), "rows": rows}
        )
    return {
        "run_id": run_id,
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "files": entries,
    }


# --- 転送 ---------------------------------------------------------------------


def _run(cmd: list[str], logger: logging.Logger) -> bool:
    logger.info("実行: " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    for line in (proc.stdout + proc.stderr).splitlines():
        logger.info(line)
    return proc.returncode == 0


def rsync_to_incoming(
    cfg: TransferConfig, sources: list[Path], run_id: str, logger: logging.Logger
) -> bool:
    """incoming/<run_id>/ へ送る。回線断に備え --partial で最大 RSYNC_ATTEMPTS 回再試行。

    宛先は VPS 側の受信ゲート（vps/mart_gate.sh、rrsync -wo incoming）が incoming/ を
    ルートとして解釈するため、相対パス `<run_id>/` を指定する。
    """
    cmd = [
        "rsync",
        "-t",  # mtime を保つ（-a の権限・所有者は VPS 側で不要）
        "--partial",
        "--timeout=120",
        "-e", " ".join(cfg.ssh_command()),
        *[str(p) for p in sources],
        f"{cfg.remote}:{run_id}/",
    ]  # fmt: skip
    for attempt in range(1, RSYNC_ATTEMPTS + 1):
        if _run(cmd, logger):
            return True
        logger.warning(f"rsync 失敗（{attempt}/{RSYNC_ATTEMPTS}）")
        if attempt < RSYNC_ATTEMPTS:
            time.sleep(10 * attempt)
    return False


def activate_remote(cfg: TransferConfig, run_id: str, logger: logging.Logger) -> bool:
    if not RUN_ID_PATTERN.match(run_id):
        raise ValueError(f"不正な run_id: {run_id}")
    return _run([*cfg.ssh_command(), cfg.remote, f"activate {run_id}"], logger)


def transfer(cfg: TransferConfig, logger: logging.Logger) -> tuple[bool, str]:
    """(成否, 要約) を返す。"""
    reason = check_build_marker(MART_ROOT, cfg.max_build_age_hours)
    if reason:
        return False, f"転送を見送り: {reason}"

    run_id = dt.datetime.now(dt.UTC).strftime(RUN_ID_FORMAT)
    files = sorted(MART_ROOT.glob("*.parquet"))
    try:
        manifest = build_manifest(files, run_id)
    except ValueError as e:
        return False, f"転送を見送り（mart の検証に失敗）: {e}"

    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
        if not rsync_to_incoming(cfg, [*files, manifest_path], run_id, logger):
            return False, "rsync に失敗（VPS の current は変更されていない）"

    if not activate_remote(cfg, run_id, logger):
        return False, "VPS 側の有効化に失敗（manifest 検証エラー等。VPS の current は変更されていない）"

    total_mb = sum(f.stat().st_size for f in files) / 1e6
    return True, f"{len(files)} ファイル / {total_mb:.0f}MB / run_id={run_id}"


def main() -> None:
    logger = setup_logger()
    cfg = TransferConfig.from_env()
    if cfg is None:
        logger.info("MART_TRANSFER_HOST 未設定のため転送をスキップ")
        return

    started = time.monotonic()
    try:
        ok, detail = transfer(cfg, logger)
    except Exception as e:  # noqa: BLE001 - 失敗は通知して非ゼロ終了
        ok, detail = False, f"想定外のエラー: {e}"
    duration = time.monotonic() - started

    summary = f"{'✅ 成功' if ok else '❌ 失敗'} / mart 転送({cfg.host}) / {detail} / {duration:.0f}s"
    logger.info(summary)
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        send_slack_notification(webhook, summary, logger)

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
