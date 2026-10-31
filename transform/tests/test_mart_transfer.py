"""mart_transfer.py のテスト。subprocess（rsync / ssh）は差し替える。"""

from __future__ import annotations

import datetime as dt
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

import duckdb
import pytest

from flows import mart_transfer
from flows.mart_transfer import TransferConfig

logger = logging.getLogger("test")


def _write_parquet(path: Path, rows: int) -> None:
    con = duckdb.connect(":memory:")
    con.execute(f"COPY (SELECT i AS x FROM range({rows}) t(i)) TO '{path}' (FORMAT PARQUET)")
    con.close()


@pytest.fixture
def mart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "mart"
    root.mkdir()
    monkeypatch.setattr(mart_transfer, "MART_ROOT", root)
    return root


@pytest.fixture
def cfg() -> TransferConfig:
    return TransferConfig(
        host="ikuty.com",
        user="martsync",
        port=22,
        ssh_key="/ssh/id_ed25519",
        known_hosts="/ssh/known_hosts",
        max_build_age_hours=6,
    )


# --- TransferConfig -------------------------------------------------------------


def test_config_from_env_is_none_when_host_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MART_TRANSFER_HOST", raising=False)
    assert TransferConfig.from_env() is None


def test_ssh_command_pins_host_key_and_disables_prompts(cfg: TransferConfig) -> None:
    cmd = cfg.ssh_command()
    assert "StrictHostKeyChecking=yes" in cmd
    assert "BatchMode=yes" in cmd
    assert "UserKnownHostsFile=/ssh/known_hosts" in cmd


# --- ビルド結果マーカー -----------------------------------------------------------


def test_marker_ok_allows_transfer(mart: Path) -> None:
    mart_transfer.write_build_marker(True, mart)
    assert mart_transfer.check_build_marker(mart, 6) is None


def test_marker_missing_blocks_transfer(mart: Path) -> None:
    assert "無い" in (mart_transfer.check_build_marker(mart, 6) or "")


def test_marker_failed_build_blocks_transfer(mart: Path) -> None:
    mart_transfer.write_build_marker(False, mart)
    assert "失敗" in (mart_transfer.check_build_marker(mart, 6) or "")


def test_marker_stale_blocks_transfer(mart: Path) -> None:
    mart_transfer.write_build_marker(True, mart)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(hours=7)
    assert "古い" in (mart_transfer.check_build_marker(mart, 6, now=later) or "")


def test_marker_corrupt_blocks_transfer(mart: Path) -> None:
    (mart / mart_transfer.BUILD_MARKER_NAME).write_text("{not json")
    assert mart_transfer.check_build_marker(mart, 6) is not None


# --- manifest -------------------------------------------------------------------


def test_build_manifest_has_rows_size_and_sha256(mart: Path) -> None:
    _write_parquet(mart / "a.parquet", 5)
    manifest: dict[str, Any] = mart_transfer.build_manifest([mart / "a.parquet"], "20261031T010203Z")
    (entry,) = manifest["files"]
    assert entry["name"] == "a.parquet"
    assert entry["rows"] == 5
    assert entry["size"] == (mart / "a.parquet").stat().st_size
    assert entry["sha256"] == mart_transfer.sha256_of(mart / "a.parquet")


def test_build_manifest_rejects_empty_parquet(mart: Path) -> None:
    _write_parquet(mart / "empty.parquet", 0)
    with pytest.raises(ValueError, match="0 行"):
        mart_transfer.build_manifest([mart / "empty.parquet"], "20261031T010203Z")


def test_build_manifest_rejects_corrupt_parquet(mart: Path) -> None:
    (mart / "bad.parquet").write_bytes(b"not parquet")
    with pytest.raises(ValueError, match="読めない"):
        mart_transfer.build_manifest([mart / "bad.parquet"], "20261031T010203Z")


def test_build_manifest_rejects_no_files() -> None:
    with pytest.raises(ValueError):
        mart_transfer.build_manifest([], "20261031T010203Z")


# --- rsync / activate -----------------------------------------------------------


def _fake_run(calls: list[list[str]], returncodes: list[int]) -> Any:
    def fake(cmd: list[str], capture_output: bool, text: bool, check: bool) -> Any:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=returncodes.pop(0), stdout="", stderr="")

    return fake


def test_rsync_targets_incoming_run_dir_relative(
    monkeypatch: pytest.MonkeyPatch, cfg: TransferConfig, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, [0]))
    assert mart_transfer.rsync_to_incoming(cfg, [tmp_path / "a.parquet"], "20261031T010203Z", logger)
    assert calls[0][-1] == "martsync@ikuty.com:20261031T010203Z/"
    assert "--partial" in calls[0]


def test_rsync_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, cfg: TransferConfig, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, [1, 0]))
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    assert mart_transfer.rsync_to_incoming(cfg, [tmp_path / "a.parquet"], "20261031T010203Z", logger)
    assert len(calls) == 2


def test_rsync_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch, cfg: TransferConfig, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, [1] * mart_transfer.RSYNC_ATTEMPTS))
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    assert not mart_transfer.rsync_to_incoming(cfg, [tmp_path / "a.parquet"], "20261031T010203Z", logger)
    assert len(calls) == mart_transfer.RSYNC_ATTEMPTS


def test_activate_rejects_malformed_run_id(cfg: TransferConfig) -> None:
    with pytest.raises(ValueError):
        mart_transfer.activate_remote(cfg, "x; rm -rf /", logger)


def test_activate_sends_activate_command(monkeypatch: pytest.MonkeyPatch, cfg: TransferConfig) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, [0]))
    assert mart_transfer.activate_remote(cfg, "20261031T010203Z", logger)
    assert calls[0][-2:] == ["martsync@ikuty.com", "activate 20261031T010203Z"]


# --- transfer（全体） -----------------------------------------------------------


def test_transfer_skips_when_build_failed_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, mart: Path, cfg: TransferConfig
) -> None:
    _write_parquet(mart / "a.parquet", 3)
    mart_transfer.write_build_marker(False, mart)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, []))
    ok, detail = mart_transfer.transfer(cfg, logger)
    assert not ok and "見送り" in detail
    assert calls == []


def test_transfer_does_not_activate_when_rsync_fails(
    monkeypatch: pytest.MonkeyPatch, mart: Path, cfg: TransferConfig
) -> None:
    _write_parquet(mart / "a.parquet", 3)
    mart_transfer.write_build_marker(True, mart)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, [1] * mart_transfer.RSYNC_ATTEMPTS))
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    ok, _ = mart_transfer.transfer(cfg, logger)
    assert not ok
    assert not any("activate" in " ".join(c) for c in calls)


def test_transfer_success_sends_parquet_and_manifest_then_activates(
    monkeypatch: pytest.MonkeyPatch, mart: Path, cfg: TransferConfig
) -> None:
    _write_parquet(mart / "a.parquet", 3)
    _write_parquet(mart / "b.parquet", 4)
    mart_transfer.write_build_marker(True, mart)
    calls: list[list[str]] = []
    sent: dict[str, Any] = {}

    def fake(cmd: list[str], capture_output: bool, text: bool, check: bool) -> Any:
        calls.append(cmd)
        if cmd[0] == "rsync":
            manifest = next(Path(a) for a in cmd if a.endswith(mart_transfer.MANIFEST_NAME))
            sent["manifest"] = json.loads(manifest.read_text())
            sent["sources"] = sorted(Path(a).name for a in cmd[:-1] if a.endswith((".parquet", ".json")))
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake)
    ok, detail = mart_transfer.transfer(cfg, logger)
    assert ok, detail
    assert sent["sources"] == ["MANIFEST.json", "a.parquet", "b.parquet"]
    assert [f["name"] for f in sent["manifest"]["files"]] == ["a.parquet", "b.parquet"]
    assert calls[0][0] == "rsync" and calls[1][-1].startswith("activate ")
    # マーカーファイル自体は転送対象に含めない（*.parquet のみ）
    assert all(mart_transfer.BUILD_MARKER_NAME not in " ".join(c) for c in calls)
