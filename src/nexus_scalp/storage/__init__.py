"""Storage package — production disk/log hygiene policy (2026-09-09).

The Disk, Log & Runtime Hygiene Lead module. ``policy`` is the pure,
testable sweep/quota layer; ``runtime`` is the lifecycle conductor
(startup sweep + throttled maintenance cycle). Nothing in this package
touches trading/research databases, models, config or the hygiene
archive — allowlist enforced by tests/unit/test_storage_policy.py.
"""

from nexus_scalp.storage.policy import (
    DEFAULT_DIAGNOSTICS_KEEP,
    DEFAULT_KEEP_PREVIOUS_BACKUPS,
    DEFAULT_KEEP_UPDATE_PACKAGES,
    DEFAULT_RESIDUE_MIN_AGE_SEC,
    StorageSettings,
    compress_old_logs,
    enforce_byte_budget,
    measure_usage,
    prune_update_cache,
    sweep_crash_leftovers,
    sweep_diagnostics,
    sweep_residue_files,
)
from nexus_scalp.storage.runtime import StorageGuard, StorageGuardSettings

__all__ = [
    "DEFAULT_DIAGNOSTICS_KEEP",
    "DEFAULT_KEEP_PREVIOUS_BACKUPS",
    "DEFAULT_KEEP_UPDATE_PACKAGES",
    "DEFAULT_RESIDUE_MIN_AGE_SEC",
    "StorageGuard",
    "StorageGuardSettings",
    "StorageSettings",
    "compress_old_logs",
    "enforce_byte_budget",
    "measure_usage",
    "prune_update_cache",
    "sweep_crash_leftovers",
    "sweep_diagnostics",
    "sweep_residue_files",
]
