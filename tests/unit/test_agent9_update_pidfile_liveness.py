"""AGENT-9 security audit regressions (2026-09-11) — updater pidfile/LIVE-gate contract.

FINDING F2 (TASK-SEC-AUDIT-SUPPLYCHAIN wave 2): the update orchestrator resolved
its default EngineGuard pidfile at ``user_root / "nexus.pid"`` while the engine
daemon WRITES its pidfile at ``release.paths.get_data_root() / "nexus.pid"``
(one ``/data`` segment apart in every layout).  Consequence: EngineGuard never
found a live engine, ``engine_state()`` returned STOPPED, and the
UPDATE_BLOCKED_WHILE_LIVE gate (update spec sections 13/14) was dead code —
``nexus update`` against a LIVE engine proceeded without --force.

The tests pin BOTH sides of the contract:
  * the updater's default pidfile equals the engine writer's canonical path;
  * a genuinely alive pid recorded by the ENGINE-side path blocks an unforced
    update when the config says LIVE;
  * explicit user-supplied pidfile paths still win (back-compat).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nexus_scalp.release import paths as rpaths
from nexus_scalp.release.update_engine import orchestrator as ue
from nexus_scalp.release.update_engine.orchestrator import (
    STATUS_UPDATE_AVAILABLE,
    UpdateOrchestrator,
)
from nexus_scalp.release.update_engine.safety_guards import (
    EngineGuard,
    UpdateBlockedError,
)


def _make_app(tmp_path: Path, version: str = "9.0.0") -> Path:
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    (app / "NexusScalpEngine.exe").write_bytes(b"MZ-OLD")
    (app / "build-info.json").write_text(
        '{"version": "9.0.0", "channel": "stable", "architecture": "x64"}',
        encoding="utf-8",
    )
    return app


def _make_user_root(tmp_path: Path) -> Path:
    user = tmp_path / "user"
    user.mkdir(exist_ok=True)
    return user


# ---------------------------------------------------------------------------
# F2-A: the pidfile DEFAULTS must agree (reader == writer)
# ---------------------------------------------------------------------------
def test_f2a_updater_default_pidfile_matches_engine_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an explicit pidfile the updater must look where the engine writes."""
    data_root = tmp_path / "engine-data"
    data_root.mkdir()
    monkeypatch.setattr(rpaths, "get_data_root", lambda: data_root)

    orch = UpdateOrchestrator(
        app_root=_make_app(tmp_path),
        user_root=_make_user_root(tmp_path),
        update_home=tmp_path / "update-home",
        installed_version="9.0.0",
    )
    assert orch.pidfile == data_root / "nexus.pid", (
        "updater default pidfile diverged from the engine writer's location"
    )
    # Cross-check against the canonical engine helper itself:
    assert orch.pidfile == rpaths.get_data_root() / "nexus.pid"


def test_f2b_explicit_pidfile_still_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit pidfile arguments keep precedence (back-compat for tests/ops)."""
    monkeypatch.setattr(rpaths, "get_data_root", lambda: tmp_path / "unused")
    explicit = tmp_path / "custom.pid"
    explicit.write_text("1", encoding="utf-8")
    orch = UpdateOrchestrator(
        app_root=_make_app(tmp_path),
        user_root=_make_user_root(tmp_path),
        update_home=tmp_path / "update-home",
        installed_version="9.0.0",
        pidfile=explicit,
    )
    assert orch.pidfile == explicit


# ---------------------------------------------------------------------------
# F2-C: an ALIVE LIVE engine recorded at the ENGINE-side path must block
# ---------------------------------------------------------------------------
def test_f2c_live_engine_at_engine_pidfile_blocks_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alive pid + LIVE user config => UPDATE_BLOCKED_WHILE_LIVE (no --force)."""
    data_root = tmp_path / "engine-data"
    data_root.mkdir()
    user_config = tmp_path / "nexus.yaml"
    user_config.write_text("execution:\n  mode: LIVE\n", encoding="utf-8")
    monkeypatch.setattr(rpaths, "get_data_root", lambda: data_root)
    monkeypatch.setattr(rpaths, "get_user_config_path", lambda: user_config)
    monkeypatch.setattr(ue, "_current_app_root", lambda: _make_app(tmp_path))

    # A genuinely ALIVE pid (this pytest process) recorded where the ENGINE writes:
    (data_root / "nexus.pid").write_text(str(os.getpid()), encoding="utf-8")

    orch = UpdateOrchestrator(
        app_root=_make_app(tmp_path),
        user_root=_make_user_root(tmp_path),
        update_home=tmp_path / "update-home",
        installed_version="9.0.0",
    )
    assert orch.pidfile == data_root / "nexus.pid"

    # Discovery is network-blocked but must run AFTER the LIVE gate; to prove the
    # gate itself, drive EngineGuard exactly as run() does:
    guard = EngineGuard(pidfile=orch.pidfile, config_path=orch._engine_config_path())
    assert guard.engine_state() == "LIVE", "alive engine pid + LIVE user config must read as LIVE"
    with pytest.raises(UpdateBlockedError):
        guard.assert_safe_to_update()


def test_f2d_stale_pid_never_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead pid at the engine path must NOT block (no false LIVE detection)."""
    data_root = tmp_path / "engine-data"
    data_root.mkdir()
    monkeypatch.setattr(rpaths, "get_data_root", lambda: data_root)
    monkeypatch.setattr(ue, "_current_app_root", lambda: _make_app(tmp_path))

    (data_root / "nexus.pid").write_text("999999999", encoding="utf-8")
    orch = UpdateOrchestrator(
        app_root=_make_app(tmp_path),
        user_root=_make_user_root(tmp_path),
        update_home=tmp_path / "update-home",
        installed_version="9.0.0",
    )
    guard = EngineGuard(pidfile=orch.pidfile, config_path=orch._engine_config_path())
    assert guard.engine_state() != "LIVE"
