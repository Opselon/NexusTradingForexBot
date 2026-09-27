"""Tests for the opt-in, throttled lifecycle purge stage."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nexus_scalp.application.live.maintenance import MaintenanceCycle
from nexus_scalp.database.lifecycle import PURGE_ENABLED_SETTING_KEY, PURGE_INTERVAL_SETTING_KEY
from nexus_scalp.settings.service import SettingsDatabase


def _cycle(tmp_path: Path, manager: Any) -> MaintenanceCycle:
    settings = tmp_path / "settings.db"
    db = SettingsDatabase(db_path=settings)
    db.set(PURGE_ENABLED_SETTING_KEY, True)
    db.set(PURGE_INTERVAL_SETTING_KEY, 10.0)
    db.close()
    om = SimpleNamespace(
        _database_lifecycle_manager=manager,
        _last_database_purge_time=0.0,
    )
    cycle = MaintenanceCycle(om)
    # The settings path is selected by NEXUS_SETTINGS_DB in production.
    return cycle


def test_scheduler_disabled_by_default(monkeypatch: Any, tmp_path: Path) -> None:
    """No settings row means destructive purge is never invoked."""
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "settings.db"))
    calls: list[int] = []
    manager = SimpleNamespace(run_purge=lambda: calls.append(1))
    cycle = MaintenanceCycle(SimpleNamespace(_database_lifecycle_manager=manager))
    asyncio.run(cycle._run_database_purge(now_t=100.0))
    assert calls == []


def test_scheduler_runs_once_per_interval(monkeypatch: Any, tmp_path: Path) -> None:
    """A due purge runs once, then waits for the configured cadence."""
    settings_path = tmp_path / "settings.db"
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(settings_path))
    db = SettingsDatabase(db_path=settings_path)
    db.set(PURGE_ENABLED_SETTING_KEY, True)
    db.set(PURGE_INTERVAL_SETTING_KEY, 10.0)
    db.close()
    calls: list[int] = []
    manager = SimpleNamespace(run_purge=lambda: calls.append(1))
    cycle = MaintenanceCycle(SimpleNamespace(_database_lifecycle_manager=manager))
    asyncio.run(cycle._run_database_purge(now_t=10.0))
    asyncio.run(cycle._run_database_purge(now_t=19.0))
    asyncio.run(cycle._run_database_purge(now_t=20.0))
    assert len(calls) == 2


def test_scheduler_swallows_purge_error(monkeypatch: Any, tmp_path: Path) -> None:
    """A failed purge cannot escape the maintenance stage."""
    settings_path = tmp_path / "settings.db"
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(settings_path))
    db = SettingsDatabase(db_path=settings_path)
    db.set(PURGE_ENABLED_SETTING_KEY, True)
    db.set(PURGE_INTERVAL_SETTING_KEY, 1.0)
    db.close()

    def fail() -> None:
        raise RuntimeError("purge failed")

    cycle = MaintenanceCycle(
        SimpleNamespace(_database_lifecycle_manager=SimpleNamespace(run_purge=fail))
    )
    asyncio.run(cycle._run_database_purge(now_t=2.0))
    assert cycle._last_database_purge_time == 2.0
