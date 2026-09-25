"""Nexus Scalp Engine — Release Engineering package.

Packaging / installation / health / diagnostics for the NexusTradingForexBot
distribution. This package is separate from the trading engine by design:

* It never imports or modifies trading logic.
* It may be imported by the packaged CLI, the installer helpers, the release
  build scripts and the smoke tests.
* It is additive: nothing in the trading hot path imports it.

Submodules:
    metadata    — canonical version + full build metadata (single source).
    paths       — AppData/ProgramData separated user-data layout + artifacts.
    environment — host detection (OS, CPU, Python, RAM, disk, GPU, MT5, ...).
    evaluate    — deterministic PASS/WARNING/BLOCKED/UNKNOWN preflight.
    health      — HealthEngine: subsystem checks + READY/DEGRADED/NOT_READY.
    repair      — RepairEngine: non-destructive derived-state repairs.
    diagnostics — sanitized diagnostics archive export.
    update      — UpdateEngine: safe update/rollback plan.
    packaging   — manifest + SHA-256 checksums + SBOM generation/verification.
    verify      — release self-check (launch, version, assets, DB, no-LIVE).
    cli_shim    — `nexus` command bootstrap (installed as console script).
"""

from __future__ import annotations

from .metadata import PRODUCT_NAME, get_version_info
from .paths import (
    get_data_root,
    get_logs_dir,
    get_user_config_path,
    user_data_initialized,
)

__all__ = [
    "PRODUCT_NAME",
    "get_data_root",
    "get_logs_dir",
    "get_user_config_path",
    "get_version_info",
    "user_data_initialized",
]
