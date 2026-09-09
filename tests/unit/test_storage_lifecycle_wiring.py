"""StorageGuard lifecycle wiring tests (2026-09-09).

Pins the integration seams:
  * AppConfig.storage section parses from YAML and reaches StorageGuardSettings
  * the engine-boot startup sweep removes updater residue and is failure-isolated
  * base.yaml ships safe defaults (enabled, keep-1 backup, capped logs)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from nexus_scalp.application.live.maintenance import MaintenanceCycle
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.configuration.storage_config import StorageConfig
from nexus_scalp.storage.runtime import StorageGuard, StorageGuardSettings


def test_maintenance_cycle_runs_storage_guard_cycle(tmp_path: Path) -> None:
    """StorageGuard.cycle is wired into the runtime maintenance cycle.

    Pins the 2026-09-09 wiring gap: the storage-hygiene pass shipped
    StorageGuard.cycle() but no production caller — a long-lived engine
    process therefore never checkpointed WAL or re-swept the updater cache
    between restarts. The maintenance cycle must compose the guard lazily
    from cfg.storage and run its throttled cycle off the tick path.
    """

    class _Cfg:
        storage = StorageConfig(enabled=True, keep_update_packages=1)

    class _OM:
        config = _Cfg()
        _storage_guard: StorageGuard | None = None
        _storage_cycle_interval_sec: float = 600.0
        _last_storage_cycle_time: float = 0.0
        # attributes the OTHER (skipped) stages touch must exist too
        _last_audit_purge_time: float = 0.0
        _audit_purge_interval_sec: float = 1e18
        _last_parity_export_time: float = 0.0
        _last_parity_snapshot_time: float = 0.0
        _parity_export_interval_sec: float = 1e18
        _parity_snapshot_interval_sec: float = 1e18
        _last_operational_digest_time: float = 0.0
        _operational_digest_interval_sec: float = 1e18
        _last_hygiene_time: float = 0.0
        _hygiene_scheduler = None
        _last_incident_time: float = 0.0
        _incident_interval_sec: float = 1e18
        _last_daily_summary_time: float = 0.0
        _daily_summary_interval_sec: float = 1e18
        _history_sync_started: bool = False
        _intelligence_worker_started: bool = False
        _research_worker_started: bool = False
        _factory_worker_started: bool = False
        _training_worker_started: bool = False
        _shadow_worker_started: bool = False
        _news_enabled: bool = False
        _news_worker_started: bool = False

    om = _OM()
    ws = tmp_path / "ws"
    (ws / "logs" / "info").mkdir(parents=True)
    (ws / "artifacts").mkdir()
    (ws / "update" / "cache").mkdir(parents=True)
    for i in range(3):
        p = ws / "update" / "cache" / f"pkg-{i}.zip"
        p.write_bytes(b"z" * 1024)
        os.utime(p, (int(time.time() - (4 - i) * 3600),) * 2)
    stale = ws / "logs" / "info" / "2026-08-01.log"
    stale.write_text("x" * 2048, encoding="utf-8")
    os.utime(stale, (int(time.time() - 86400 * 60),) * 2)

    maintenance = MaintenanceCycle(om)  # type: ignore[arg-type]

    def _fake_cwd() -> Path:
        return ws

    # Path.cwd() is resolved INSIDE the storage stage; point it at the
    # synthetic workspace for the duration of the cycle.
    with patch.object(Path, "cwd", staticmethod(_fake_cwd)):
        asyncio.run(maintenance.run_cycle(now_t=time.time()))

    guard = om._storage_guard
    assert guard is not None, "storage guard must be composed from cfg.storage"
    assert guard.settings.enabled is True
    assert guard.settings.keep_update_packages == 1
    # the cycle actually RAN (not just composed): cache pruned to keep=1
    kept = sorted(p.name for p in (ws / "update" / "cache").iterdir())
    assert kept == ["pkg-2.zip"], f"cache not pruned by the wired cycle: {kept}"
    # throttle state advanced
    assert om._last_storage_cycle_time > 0


def test_maintenance_storage_stage_is_failure_isolated(tmp_path: Path) -> None:
    """A storage-guard fault must never break the maintenance cycle."""

    class _Boom:
        def cycle(self) -> dict[str, Any]:
            raise OSError("simulated storage fault")

    class _OM:
        config = None
        _storage_guard: Any = _Boom()
        _storage_cycle_interval_sec: float = 600.0
        _last_storage_cycle_time: float = 0.0
        _last_audit_purge_time: float = 0.0
        _audit_purge_interval_sec: float = 1e18
        _last_parity_export_time: float = 0.0
        _last_parity_snapshot_time: float = 0.0
        _parity_export_interval_sec: float = 1e18
        _parity_snapshot_interval_sec: float = 1e18
        _last_operational_digest_time: float = 0.0
        _operational_digest_interval_sec: float = 1e18
        _last_hygiene_time: float = 0.0
        _hygiene_scheduler = None
        _last_incident_time: float = 0.0
        _incident_interval_sec: float = 1e18
        _last_daily_summary_time: float = 0.0
        _daily_summary_interval_sec: float = 1e18
        _history_sync_started: bool = False
        _intelligence_worker_started: bool = False
        _research_worker_started: bool = False
        _factory_worker_started: bool = False
        _training_worker_started: bool = False
        _shadow_worker_started: bool = False
        _news_enabled: bool = False
        _news_worker_started: bool = False

    om = _OM()
    maintenance = MaintenanceCycle(om)  # type: ignore[arg-type]
    asyncio.run(maintenance.run_cycle(now_t=time.time()))  # must not raise
    assert om._last_storage_cycle_time > 0


def test_base_yaml_storage_section_parses() -> None:
    cfg = AppConfig.load_from_yaml(Path("configs/base.yaml"))
    assert cfg.storage is not None
    assert cfg.storage.enabled is True
    assert cfg.storage.max_total_mb_per_severity == 500
    assert cfg.storage.keep_previous_backups == 1
    assert cfg.storage.residue_min_age_sec == 3600.0
    # settings round-trip
    s = StorageGuardSettings.from_mapping(cfg.storage.model_dump())
    assert s.max_total_mb_per_severity == 500
    assert s.enabled is True


def test_missing_storage_section_defaults_to_none() -> None:
    cfg = AppConfig()  # no YAML at all
    assert cfg.storage is None


def test_startup_sweep_frees_updater_residue(tmp_path: Path, caplog) -> None:
    ws = tmp_path / "ws"
    ur = tmp_path / "ur"
    ws.mkdir()
    ur.mkdir()
    now = time.time()
    # crash leftovers + stale parts in the workspace
    (ws / ".update-stage-1").mkdir()
    (ws / ".update-stage-1" / "junk.bin").write_bytes(b"x" * 1024)
    stale = ws / "deep" / "left.part"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x" * 2048)
    os.utime(stale, (now - 7200, now - 7200))
    # a protected file that must survive
    keep = ws / "artifacts" / "audit.db"
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"x" * 4096)

    guard = StorageGuard(
        workspace=ws,
        user_root=ur,
        settings=StorageGuardSettings(),
    )
    with caplog.at_level(logging.INFO):
        report = guard.startup_sweep()
    freed = int(report["crash_leftovers"]["bytes_freed"]) + int(report["residue"]["bytes_freed"])
    assert freed == 1024 + 2048
    assert not (ws / ".update-stage-1").exists()
    assert not stale.exists()
    assert keep.exists()  # protected data untouched


def test_startup_sweep_survives_io_errors(tmp_path: Path) -> None:
    ws = tmp_path / "missing-workspace"
    guard = StorageGuard(workspace=ws, user_root=tmp_path / "u")
    report = guard.startup_sweep()  # must not raise on nonexistent roots
    assert report["crash_leftovers"]["removed_dirs"] == 0
    assert report["residue"]["removed"] == 0


def test_storage_guard_first_cycle_is_due_on_low_uptime_host(tmp_path: Path) -> None:
    """The first cycle must run even when host uptime < the cycle interval.

    Regression (CI run 1032): StorageGuard used a 0.0 sentinel against
    time.monotonic(); on a freshly booted machine monotonic() < 600s so the
    FIRST wired cycle was silently skipped ("throttled") and nothing was
    swept until the host had been up for 10 minutes.
    """
    guard = StorageGuard(workspace=tmp_path / "ws", user_root=tmp_path / "u")
    # simulate a freshly rebooted host: monotonic small relative to interval
    assert guard.is_due(now=1.0) is True, "first cycle must be due immediately"
    report = guard.cycle()  # non-forced
    assert "skipped" not in report, f"first cycle must not be throttled: {report}"
    followup = guard.cycle()
    assert followup.get("skipped") == "throttled"
