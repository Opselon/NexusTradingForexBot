"""StorageGuard lifecycle wiring tests (2026-09-09).

Pins the integration seams:
  * AppConfig.storage section parses from YAML and reaches StorageGuardSettings
  * the engine-boot startup sweep removes updater residue and is failure-isolated
  * base.yaml ships safe defaults (enabled, keep-1 backup, capped logs)
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.storage.runtime import StorageGuard, StorageGuardSettings


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
