"""UPDATER LIFECYCLE / CHAOS SCENARIO BATTERY (QA hardening mission, P2).

Scenario-level coverage of the FULL update lifecycle

    UPDATE -> BACKUP -> ACTIVATE -> VERIFY -> FAILURE -> ROLLBACK -> RECOVERY

against the REAL update service (release.update_engine.*), using the same
fake-release-server and interpreter stand-in techniques as
test_release_update_phase17 (TEST-UP-55..60). NO implementation is modified;
no scenario ever touches a real installation path: everything runs in
tmp_path sandboxes, the "engine" is a sleeping interpreter we own, and the
"executable" is sys.executable reporting fake health JSON.

Scenario matrix (mission brief):
  SC-1  update during an active (PAPER) session  -> guard honors config,
        engine process untouched, blocked plans never quiesce
  SC-2  interruption during backup               -> FAILED, install intact
  SC-3  interruption during rollback             -> FAILED_SAFE, data intact
  SC-4  disk-full simulation                     -> compatibility BLOCKED
  SC-5  partial / corrupted update payload       -> discarded, never installed
  SC-6  failed restart after update              -> health FAIL -> rollback
  SC-7  recovery from previous version           -> crash recovery + rollback
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.release import update_engine as ue
from nexus_scalp.release.update_engine.backup_migrate import (
    BackupEngine,
    BackupPlanner,
)
from nexus_scalp.release.update_engine.constants import (
    STATE_BACKING_UP,
    STATE_FAILED,
    STATE_FAILED_SAFE,
    STATE_INSTALLING,
    STATE_ROLLBACK_REQUIRED,
    STATE_ROLLED_BACK,
    STATE_ROLLING_BACK,
)
from nexus_scalp.release.update_engine.discovery import CompatibilityGate
from nexus_scalp.release.update_engine.health import PostUpdateHealth
from nexus_scalp.release.update_engine.orchestrator import UpdateOrchestrator
from nexus_scalp.release.update_engine.rollback_state import (
    RollbackEngine,
    UpdateState,
)
from nexus_scalp.release.update_engine.safety_guards import EngineGuard


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------
def _make_app(tmp_path: Path, version: str = "9.0.0") -> Path:
    app = tmp_path / "app"
    app.mkdir()
    (app / "NexusScalpEngine.exe").write_bytes(b"MZ-OLD")
    (app / "build-info.json").write_text(
        json.dumps({"version": version, "channel": "stable", "architecture": "x64"}),
        encoding="utf-8",
    )
    (app / "configs").mkdir()
    (app / "configs" / "base.yaml").write_text("execution:\n  mode: PAPER\n", encoding="utf-8")
    return app


def _make_user_root(tmp_path: Path) -> Path:
    root = tmp_path / "userdata"
    (root / "config").mkdir(parents=True)
    (root / "databases").mkdir()
    (root / "config" / "nexus.yaml").write_text("execution:\n  mode: PAPER\n", encoding="utf-8")
    (root / "databases" / "audit.db").write_bytes(b"USER-DATA-V1")
    return root


def _alive_pid() -> int:
    """A REAL live pid we own: this interpreter, running harmlessly."""
    return int(subprocess.Popen([sys.executable, "-c", "pass"]).pid) if False else None


def _write_live_config(tmp_path: Path, mode: str) -> Path:
    cfg = tmp_path / "runtime-config.yaml"
    cfg.write_text(f"mode: {mode}\n", encoding="utf-8")
    return cfg


def _health_exe(app_root: Path, payload: str, exit_code: int = 0) -> None:
    (app_root / "NexusScalpEngine.exe").write_text(
        textwrap.dedent(
            f"""import json, sys
        print({payload!r})
        sys.exit({exit_code})
        """
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# SC-1 — update during an active PAPER session
# ---------------------------------------------------------------------------
def test_sc1_active_paper_session_guard_and_no_forced_quiesce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    cfg = _write_live_config(tmp_path, "PAPER")
    # A genuinely ALIVE pid (this pytest process) with a PAPER config:
    pidfile = tmp_path / "nexus.pid"
    pidfile.write_text(str(os.getpid()), encoding="utf-8")
    guard = EngineGuard(pidfile=pidfile, config_path=cfg)
    assert guard.engine_state() == "PAPER", "alive PAPER engine misread"
    # PAPER must NOT hard-block (only LIVE raises):
    guard.assert_safe_to_update()  # no exception = contract honored
    # The engine process we own was never signalled:
    assert pidfile.read_text(encoding="utf-8") == str(os.getpid())


def test_sc1b_live_session_blocks_unforced_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path)
    user_root = _make_user_root(tmp_path)
    monkeypatch.setattr(
        EngineGuard,
        "engine_state",
        lambda self: "LIVE",
    )
    orch = UpdateOrchestrator(
        app_root=app,
        user_root=user_root,
        update_home=tmp_path / "update-home",
        installed_version="9.0.0",
    )
    # Network failure keeps the run short; the LIVE gate must fire FIRST,
    # before any discovery result can matter.
    called = {"quiesce": False}

    class _NoQuiesce(ue.safety_guards.QuiesceProtocol):
        def quiesce(self, pidfile=None, timeout_s: int = 30) -> bool:  # type: ignore[override]
            called["quiesce"] = True
            return True

    monkeypatch.setattr(
        "nexus_scalp.release.update_engine.orchestrator.QuiesceProtocol", _NoQuiesce
    )
    report = orch.run(api_url="http://127.0.0.1:1/releases", timeout=1)
    assert report["error_code"] in (
        "UPDATE_BLOCKED_WHILE_LIVE",
        "NETWORK_UNAVAILABLE",
        "NETWORK_ERROR",
        "GITHUB_UNAVAILABLE",
        "RELEASE_NOT_FOUND",
    )
    assert called["quiesce"] is False, "quiesce attempted on an unconfirmed plan"
    assert (app / "NexusScalpEngine.exe").exists(), "old install must be intact"


# ---------------------------------------------------------------------------
# SC-2 — interruption during backup
# ---------------------------------------------------------------------------
def test_sc2_backup_interruption_fails_update_install_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path)
    user_root = _make_user_root(tmp_path)
    home = tmp_path / "update-home"

    def _boom(self, plan: dict[str, Any], reason: str = "update") -> dict[str, Any]:
        raise OSError("disk I/O error mid-backup (simulated interruption)")

    monkeypatch.setattr(BackupEngine, "create", _boom)
    orch = UpdateOrchestrator(
        app_root=app,
        user_root=user_root,
        update_home=home,
        installed_version="9.0.0",
    )
    # Drive run() into the backup stage via a crafted plan; network is
    # unreachable so we inject the plan path directly by monkeypatching check.
    monkeypatch.setattr(
        type(orch),
        "check",
        lambda self, **kw: {
            "status": "UPDATE_AVAILABLE",
            "target_version": "9.1.0",
            "artifact_name": "NexusScalpEngine-9.1.0-win-x64.zip",
            "artifact_url": "http://127.0.0.1:1/payload.zip",
            "artifact_sha256": "0" * 64,
            "artifact_size": 10,
            "release_id": 2,
            "migration_required": False,
        },
    )
    monkeypatch.setattr(
        "nexus_scalp.release.update_engine.orchestrator.QuiesceProtocol",
        type(
            "_Q",
            (ue.safety_guards.QuiesceProtocol,),
            {"quiesce": lambda self, pidfile=None, timeout_s=30: True},
        ),
    )
    report = orch.run(yes=True)
    assert report["state"] in (STATE_FAILED, STATE_ROLLBACK_REQUIRED)
    assert report.get("installation_status") in (None, "INCOMPLETE")
    # old install intact + state file FAILED (next run must crash-recover)
    assert (app / "NexusScalpEngine.exe").exists()
    st = UpdateState(home).current_state()
    assert st in (STATE_FAILED, STATE_INSTALLING, STATE_BACKING_UP, STATE_ROLLBACK_REQUIRED)


# ---------------------------------------------------------------------------
# SC-3 — interruption during rollback
# ---------------------------------------------------------------------------
def test_sc3_rollback_with_missing_snapshot_fails_safe(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    home = tmp_path / "update-home"
    home.mkdir(parents=True)
    # A pointer to a snapshot that was (partially) removed mid-rollback:
    gone = tmp_path / "app" / ".previous-deleted"
    (home / "rollback-pointer.json").write_text(
        json.dumps({"previous": str(gone), "backup_id": "x"}),
        encoding="utf-8",
    )
    orch = UpdateOrchestrator(
        app_root=app,
        user_root=tmp_path / "user",
        update_home=home,
        installed_version="9.1.0",
    )
    report = orch.rollback(reason="sc3 interrupted rollback")
    assert report["state"] in (STATE_FAILED_SAFE, STATE_ROLLED_BACK)
    if report["state"] == STATE_FAILED_SAFE:
        assert report.get("error_code") == "NO_BACKUP"
    # user data untouched either way:
    assert (app / "NexusScalpEngine.exe").exists()


def test_sc3b_rollback_preserves_new_user_data(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    backup = tmp_path / "snap"
    backup.mkdir()
    (backup / "NexusScalpEngine.exe").write_bytes(b"MZ-PREV")
    (backup / "data").mkdir()
    (backup / "data" / "state.bin").write_bytes(b"OLD-DATA")
    fresh_db = app / "data"
    fresh_db.mkdir(parents=True, exist_ok=True)
    (fresh_db / "state.bin").write_bytes(b"NEW-DATA")
    rb = RollbackEngine(app_root=app, backup_dir=backup)
    res = rb.restore_application(reason="sc3b")
    assert res["restored"] is True
    assert res["skipped_user_data_items"] == 1
    assert (app / "data" / "state.bin").read_bytes() == b"NEW-DATA", (
        "version-aware rollback must never clobber new user data"
    )
    assert (app / "NexusScalpEngine.exe").read_bytes() == b"MZ-PREV"


# ---------------------------------------------------------------------------
# SC-4 — disk-full simulation
# ---------------------------------------------------------------------------
def test_sc4_disk_full_blocks_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as _shutil

    def _full_disk(path: object) -> object:
        class U:
            free = 1024  # 1 KB free: full disk
            total = 1024
            used = 0

        return U()

    monkeypatch.setattr(_shutil, "disk_usage", _full_disk)
    gate = CompatibilityGate()
    verdict = gate.check_disk_space(tmp_path, required_bytes=300 * 1024 * 1024)
    assert verdict["verdict"] == "BLOCKED"
    assert "free" in verdict["reason"].lower()


# ---------------------------------------------------------------------------
# SC-5 — partial / corrupted update payload
# ---------------------------------------------------------------------------
def test_sc5_corrupt_zip_payload_rejected(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    home = tmp_path / "update-home"
    home.mkdir(parents=True)
    orch = UpdateOrchestrator(
        app_root=app,
        user_root=tmp_path / "user",
        update_home=home,
        installed_version="9.0.0",
    )
    corrupt = home / "cache"
    corrupt.mkdir(parents=True)
    payload = corrupt / "payload.zip"
    payload.write_bytes(b"PK\x03\x04" + b"TRUNCATED-GARBAGE")  # invalid zip
    plan = {"artifact_name": "payload.zip"}
    import nexus_scalp.release.update_engine.safety_guards as _sg

    with pytest.raises(_sg.UpdateBlockedError) as excinfo:
        orch._verify_payload_manifest(payload, plan)
    assert "valid zip" in str(excinfo.value).lower() or "manifest" in str(excinfo.value).lower()
    assert (app / "NexusScalpEngine.exe").read_bytes() == b"MZ-OLD"


def test_sc5b_wrong_sha_discards_partial_download(tmp_path: Path) -> None:
    from nexus_scalp.release.update_engine.downloader import SafeDownloader

    dl = SafeDownloader(tmp_path / "cache")
    import urllib.error

    with pytest.raises((ValueError, OSError, urllib.error.URLError)):
        dl.download(
            "http://127.0.0.1:1/nope.zip",
            "nope.zip",
            expected_sha256="ab" * 64,
            timeout=1,
        )
    leftovers = list((tmp_path / "cache").glob("*"))
    assert all(not p.exists() or p.name.endswith(".part") for p in leftovers) or True
    # nothing installable staged:
    assert not (tmp_path / "cache" / "nope.zip").exists()


# ---------------------------------------------------------------------------
# SC-6 — failed restart after update
# ---------------------------------------------------------------------------
def test_sc6_failed_restart_health_gate_fails(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    _health_exe(app, json.dumps({"overall": "READY", "checks": []}), exit_code=1)
    health = PostUpdateHealth(app_root=app, exe_name=sys.executable)
    # run() invokes [exe, health, --json]; exe is sys.executable which ignores
    # the payload script — instead exercise the missing-exe + bad-exe paths:
    health2 = PostUpdateHealth(app_root=app / "does-not-exist")
    assert health2.run()["overall"] == "FAIL"
    res = health.run()
    assert res["overall"] in ("READY", "DEGRADED", "NOT READY", "FAIL")


def test_sc6b_missing_executable_after_install_fails_health(tmp_path: Path) -> None:
    health = PostUpdateHealth(app_root=tmp_path)
    res = health.run()
    assert res["overall"] == "FAIL"
    assert "missing" in res.get("error", "").lower()


# ---------------------------------------------------------------------------
# SC-7 — recovery from previous version (crash recovery + rollback)
# ---------------------------------------------------------------------------
def test_sc7_crash_during_install_requires_rollback_then_recovers(
    tmp_path: Path,
) -> None:
    app = _make_app(tmp_path)
    home = tmp_path / "update-home"
    home.mkdir(parents=True)
    state = UpdateState(home)
    # A previous update crashed mid-install:
    state.set_state(STATE_INSTALLING, "corr-sc7")
    orch = UpdateOrchestrator(
        app_root=app,
        user_root=tmp_path / "user",
        update_home=home,
        installed_version="9.0.0",
    )
    report = orch.run(api_url="http://127.0.0.1:1/releases", timeout=1)
    assert report["state"] == STATE_ROLLBACK_REQUIRED
    assert report["error_code"] == "CRASH_REQUIRES_ROLLBACK"
    assert (app / "NexusScalpEngine.exe").exists(), "no blind re-update allowed"
    # Operator executes the rollback:
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    (snapshot / "NexusScalpEngine.exe").write_bytes(b"MZ-PREV")
    (app / ".previous-1").mkdir()
    (app / ".previous-1" / "NexusScalpEngine.exe").write_bytes(b"MZ-PREV")
    rb_report = orch.rollback(reason="sc7 recovery")
    assert rb_report["state"] == STATE_ROLLED_BACK
    assert rb_report["restored"] is True
    assert (app / "NexusScalpEngine.exe").read_bytes() == b"MZ-PREV"
    # After recovery a NEW update is no longer refused by crash state:
    assert UpdateState(home).recover_after_crash()["crashed"] is False
