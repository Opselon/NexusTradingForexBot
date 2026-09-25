"""StorageGuard — runtime conductor of the storage policy (2026-09-09).

Bridges :mod:`nexus_scalp.storage.policy` into the client lifecycle:

* ``startup_sweep``  — crash leftovers + residue, once per engine boot and
  once per update run (both AFTER the updater's own logic, so a completed
  install's own cleanup is never raced).
* ``cycle``          — throttled pass from the live maintenance thread:
  log compression + byte budget + WAL checkpoint on managed DBs + updater
  cache prune + diagnostics cap, each stage individually failure-isolated.

The guard NEVER deletes anything on the tick path (cycle runs via
``asyncio.to_thread`` from MaintenanceCycle, same as the DB hygiene worker)
and never touches protected data — the allowlist lives in policy.py and is
test-pinned. Quota awareness: ``quota_snapshot`` returns measured usage vs
the configured limit so callers (Telegram digest, web status) can surface a
honest alert; nothing here silently deletes trading/research state to "make
room".
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus_scalp.storage.policy import (
    compress_old_logs,
    enforce_byte_budget,
    prune_update_cache,
    sweep_crash_leftovers,
    sweep_diagnostics,
    sweep_residue_files,
)

logger = logging.getLogger(__name__)

#: Databases eligible for a WAL checkpoint (TRUNCATE) — the SAME allowlist the
#: hygiene worker manages (hygiene/worker_runner.py MANAGED_DATABASES). The
#: checkpoint releases WAL/free-page bloat; it NEVER deletes rows.
_CHECKPOINT_DBS: tuple[str, ...] = (
    "artifacts/audit.db",
    "artifacts/news.db",
    "artifacts/candle_intel.db",
)

#: Default throttle: the maintenance cycle asks every ~10 min; the guard runs
#: the expensive pass at most this often.
_CYCLE_INTERVAL_SEC = 600.0


@dataclass
class StorageGuardSettings:
    """Runtime knobs for the guard (``storage:`` YAML section)."""

    enabled: bool = True
    startup_sweep: bool = True
    compress_after_days: int = 2
    max_total_mb_per_severity: int = 500
    max_total_logs_mb: int = 2000
    quota_mb: int = 5000
    keep_update_packages: int = 2
    keep_previous_backups: int = 1
    diagnostics_keep: int = 10
    residue_min_age_sec: float = 3600.0

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> StorageGuardSettings:
        d = data or {}
        return cls(
            enabled=bool(d.get("enabled", True)),
            startup_sweep=bool(d.get("startup_sweep", True)),
            compress_after_days=max(0, int(d.get("compress_after_days", 2))),
            max_total_mb_per_severity=max(0, int(d.get("max_total_mb_per_severity", 500))),
            max_total_logs_mb=max(0, int(d.get("max_total_logs_mb", 2000))),
            quota_mb=max(0, int(d.get("quota_mb", 5000))),
            keep_update_packages=max(1, int(d.get("keep_update_packages", 2))),
            keep_previous_backups=max(1, int(d.get("keep_previous_backups", 1))),
            diagnostics_keep=max(0, int(d.get("diagnostics_keep", 10))),
            residue_min_age_sec=max(0.0, float(d.get("residue_min_age_sec", 3600.0))),
        )


class StorageGuard:
    """Owns cadence + failure isolation for one client installation."""

    def __init__(
        self,
        *,
        workspace: Path | str,
        user_root: Path | str,
        settings: StorageGuardSettings | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.user_root = Path(user_root)
        self.settings = settings or StorageGuardSettings()
        #: None = never ran (the FIRST cycle is always due — do NOT compare a
        #: 0.0 sentinel against time.monotonic(): on a freshly booted machine
        #: monotonic < interval and the first pass would be silently skipped).
        self._last_cycle: float | None = None
        self._startup_done = False

    # ------------------------------------------------------------------
    # path map (allowlisted removable roots ONLY)
    # ------------------------------------------------------------------
    @property
    def logs_root(self) -> Path:
        return self.workspace / "logs"

    @property
    def update_home(self) -> Path:
        return self.user_root / "update"

    @property
    def diagnostics_dir(self) -> Path:
        return self.user_root / "diagnostics"

    def usage_roots(self) -> dict[str, Path]:
        return {
            "logs": self.logs_root,
            "update_cache": self.update_home / "cache",
            "update_backups": self.update_home / "backups",
            "diagnostics": self.diagnostics_dir,
            "databases": self.workspace / "artifacts",
        }

    # ------------------------------------------------------------------
    # startup sweep (engine boot / update run)
    # ------------------------------------------------------------------
    def startup_sweep(self) -> dict[str, Any]:
        """Crash leftovers + stale residue only. Fast, no compression.

        Safe to run concurrently with another boot: every part removes only
        the allowlisted shapes and records what it did.
        """
        report: dict[str, Any] = {"startup_sweep_enabled": self.settings.startup_sweep}
        if not self.settings.enabled or not self.settings.startup_sweep:
            return report
        report["crash_leftovers"] = sweep_crash_leftovers(
            self.workspace,
            keep_previous_backups=self.settings.keep_previous_backups,
        )
        report["residue"] = sweep_residue_files(
            self.workspace,
            min_age_sec=self.settings.residue_min_age_sec,
        )
        report["update_cache"] = prune_update_cache(
            self.update_home / "cache",
            keep_packages=self.settings.keep_update_packages,
        )
        self._startup_done = True
        return report

    # ------------------------------------------------------------------
    # throttled runtime cycle (maintenance thread)
    # ------------------------------------------------------------------
    def is_due(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        if self._last_cycle is None:
            return True
        return (now - self._last_cycle) >= _CYCLE_INTERVAL_SEC

    def cycle(self, *, force: bool = False) -> dict[str, Any]:
        """One maintenance pass. Call off the tick path (to_thread)."""
        report: dict[str, Any] = {"enabled": self.settings.enabled}
        now_mono = time.monotonic()
        if not self.settings.enabled:
            return report
        if not force and not self.is_due(now_mono):
            report["skipped"] = "throttled"
            return report
        self._last_cycle = now_mono
        started = time.time()

        report["logs"] = self._log_pass()
        report["wal_checkpoint"] = self._wal_checkpoint_pass()
        report["update_cache"] = prune_update_cache(
            self.update_home / "cache",
            keep_packages=self.settings.keep_update_packages,
        )
        report["diagnostics"] = sweep_diagnostics(
            self.diagnostics_dir, keep=self.settings.diagnostics_keep
        )
        report["crash_leftovers"] = sweep_crash_leftovers(
            self.workspace,
            keep_previous_backups=self.settings.keep_previous_backups,
        )
        report["duration_sec"] = round(time.time() - started, 3)
        return report

    # ------------------------------------------------------------------
    # stages
    # ------------------------------------------------------------------
    def _log_pass(self) -> dict[str, Any]:
        """Compression + per-severity budget over the severity tree."""
        out: dict[str, Any] = {
            "compressed": 0,
            "bytes_saved": 0,
            "budget_removed": 0,
            "budget_freed": 0,
            "errors": [],
        }
        if not self.logs_root.is_dir():
            return out
        for severity in ("info", "warning", "error", "critical"):
            severity_dir = self.logs_root / severity
            if not severity_dir.is_dir():
                continue
            comp = compress_old_logs(
                severity_dir, compress_after_days=self.settings.compress_after_days
            )
            out["compressed"] += int(comp.get("compressed", 0))
            out["bytes_saved"] += int(comp.get("bytes_saved", 0))
            out["errors"].extend(str(e) for e in comp.get("errors", []))
            budget = enforce_byte_budget(
                severity_dir,
                max_total_mb=self.settings.max_total_mb_per_severity,
            )
            out["budget_removed"] += int(budget.get("removed", 0))
            out["budget_freed"] += int(budget.get("bytes_freed", 0))
            out["errors"].extend(str(e) for e in budget.get("errors", []))
        total = enforce_byte_budget(
            self.logs_root,
            max_total_mb=self.settings.max_total_logs_mb,
        )
        out["budget_removed"] += int(total.get("removed", 0))
        out["budget_freed"] += int(total.get("bytes_freed", 0))
        out["errors"].extend(str(e) for e in total.get("errors", []))
        return out

    def _wal_checkpoint_pass(self) -> dict[str, Any]:
        """PRAGMA wal_checkpoint(TRUNCATE) on managed DBs (never deletes rows).

        Truncating the WAL file is safe for concurrent readers (they rebuild
        from the WAL if a checkpoint lapses) and bounds the -wal sidecar that
        otherwise stays large after write storms. Refused for in-memory or
        missing DBs, and for any file whose 16-byte header is not a valid
        SQLite magic (a blind sqlite3.connect on a corrupt/foreign file can
        make the library roll back and DELETE the sidecar — that would be
        destructive). Full VACUUM is deliberately NOT automatic: it rewrites
        the whole DB file and is an operator action (`nexus db`).
        """
        out: dict[str, Any] = {"checked": 0, "truncated": 0, "errors": []}
        for rel in _CHECKPOINT_DBS:
            db_path = self.workspace / rel
            try:
                if not db_path.exists() or db_path.stat().st_size < 16:
                    continue
                with open(db_path, "rb") as fh:
                    if fh.read(16) != b"SQLite format 3\x00":
                        out["errors"].append(
                            f"{rel}: not a SQLite file (header mismatch) — skipped"
                        )
                        continue
                out["checked"] += 1
                conn = sqlite3.connect(str(db_path), timeout=2.0)
                try:
                    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
                    if row is not None and int(row[0]) == 0:
                        out["truncated"] += 1
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                out["errors"].append(f"{rel}: {exc}")
            except OSError as exc:
                out["errors"].append(f"{rel}: {exc}")
        return out

    # ------------------------------------------------------------------
    # quota awareness (measure + alert surface; never deletes protected data)
    # ------------------------------------------------------------------
    def quota_snapshot(self) -> dict[str, Any]:
        usage = {name: _safe_dir_size(p) for name, p in self.usage_roots().items()}
        total_mb = sum(usage.values()) / (1024.0 * 1024.0)
        quota_mb = self.settings.quota_mb
        return {
            "usage_mb": {k: round(v / (1024.0 * 1024.0), 1) for k, v in usage.items()},
            "total_mb": round(total_mb, 1),
            "quota_mb": quota_mb,
            "over_quota": bool(quota_mb > 0 and total_mb > quota_mb),
        }


def _safe_dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total
