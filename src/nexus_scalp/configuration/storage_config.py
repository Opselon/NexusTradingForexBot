"""Config section for the storage-hygiene policy (2026-09-09 pass).

Mirrors the `storage:` YAML section; consumed by
``nexus_scalp.storage.runtime.StorageGuardSettings.from_mapping`` via
engine boot and the live maintenance cycle.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class StorageConfig(BaseModel):
    """Disk / log hygiene knobs (client cannot fill its disk anymore).

    All removal is ALLOWLIST-based (severity log trees, updater cache,
    crash leftovers, old backups, diagnostics zips, stale *.tmp/*.part).
    Trading/research databases, models, config, archives and quarantine
    are never touched — pinned by tests/unit/test_storage_policy.py.
    """

    enabled: bool = True
    #: crash-leftover + residue sweep at engine boot / update run
    startup_sweep: bool = True
    #: gzip severity log files older than this (days); 0 = never compress
    compress_after_days: int = Field(default=2, ge=0, le=365)
    #: per-severity-directory byte budget (MB); 0 = unlimited (age still applies)
    max_total_mb_per_severity: int = Field(default=500, ge=0)
    #: whole-log-tree byte budget (MB, all severities summed); 0 = unlimited
    max_total_logs_mb: int = Field(default=2000, ge=0)
    #: soft quota for the managed removable roots (MB); alert-only, 0 = off
    quota_mb: int = Field(default=5000, ge=0)
    #: updater package cache: newest N verified packages kept
    keep_update_packages: int = Field(default=2, ge=1, le=20)
    #: full-app backup trees (.previous-*) kept for rollback
    keep_previous_backups: int = Field(default=1, ge=1, le=10)
    #: diagnostics zips retained
    diagnostics_keep: int = Field(default=10, ge=0, le=1000)
    #: *.tmp/*.part residue younger than this (seconds) is never touched
    residue_min_age_sec: float = Field(default=3600.0, ge=0.0)
