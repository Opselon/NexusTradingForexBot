"""Storage Policy Engine (Disk, Log & Runtime Hygiene Lead, 2026-09-09)
======================================================================

ONE owner for removable disk state across the client lifecycle. No component
invents its own cleanup behavior anymore: the updater, engine boot, the live
maintenance cycle and the CLI all call into this module.

Scope discipline (the whole point of this module — see
docs/agent_handoffs/2026-09-09_NSE_storage_hygiene_findings.txt):

  PROTECTED (never touched, enforced by test):
    * trading/research databases (audit.db, news.db, candle_intel.db,
      marketplace.db, settings DBs) and their journals/sidecars
    * model artifacts (artifacts/models, artifacts/model_generation)
    * hygiene archives + quarantine (artifacts/archive, quarantine stores)
    * update records (installed-release.json, update-state.json, history)
    * user configuration (config/, data/)
  REMOVABLE (allowlisted, this module only):
    * severity log trees (info/warning/error/critical) — age + byte cap +
      gzip compression
    * updater package cache (update_home/cache) — keep N newest verified
      packages + stale *.part residue
    * crash leftovers (.update-stage-*, .preserve-*) and old full-app
      backups (.previous-*, keep newest 1)
    * diagnostics zips (keep N newest)
    * *.tmp / *.part residue older than the residue age anywhere under a
      caller-provided root

Every destructive function returns a structured result (what would be /
was removed, bytes) so callers can log, alert and display honestly.
All deletes are per-file unlink or per-dir rmtree of the allowlisted shapes
only — never a whole-tree wipe of anything the caller did not explicitly
pass in.
"""

from __future__ import annotations

import gzip
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_DIAGNOSTICS_KEEP",
    "DEFAULT_KEEP_PREVIOUS_BACKUPS",
    "DEFAULT_KEEP_UPDATE_PACKAGES",
    "DEFAULT_RESIDUE_MIN_AGE_SEC",
    "StorageSettings",
    "compress_old_logs",
    "enforce_byte_budget",
    "measure_usage",
    "prune_update_cache",
    "sweep_crash_leftovers",
    "sweep_diagnostics",
    "sweep_residue_files",
]


#: Updater package cache: newest N packages are kept (the currently staged
#: one counts toward N). Older packages are removable — a fresh download
#: always replaces them byte-for-byte (SHA-256 verified at download time).
DEFAULT_KEEP_UPDATE_PACKAGES = 2

#: Full-app backup trees (.previous-<ts>) kept for rollback. Exactly one
#: prior tree is needed to roll back (RollbackEngine picks the newest);
#: every older copy is dead weight on the customer disk.
DEFAULT_KEEP_PREVIOUS_BACKUPS = 1

#: Diagnostics zips retained (user-triggered exports; small, but bounded).
DEFAULT_DIAGNOSTICS_KEEP = 10

#: *.tmp / *.part residue younger than this is NEVER touched (a download or
#: extraction may be actively using it). One hour is conservative.
DEFAULT_RESIDUE_MIN_AGE_SEC = 3600.0

#: Crash-leftover directory prefixes (update_engine/backup_migrate.py shapes).
_STAGE_PREFIX = ".update-stage-"
_PRESERVE_PREFIX = ".preserve-"
_PREVIOUS_RE = re.compile(r"^\.previous(-[A-Za-z0-9]+)?$")

#: Log files that only the storage policy may remove (compressed form included).
_LOG_SUFFIXES = ("*.log", "*.log.gz")


@dataclass(frozen=True)
class StorageSettings:
    """Tunable knobs (mirrored one-to-one in the `storage:` YAML section)."""

    #: gzip log files older than this many days (0 = never compress).
    compress_after_days: int = 2
    #: Per-severity-directory byte budget (oldest files removed first).
    #: 0 = unlimited (age retention still applies via observability.logging).
    max_total_mb_per_severity: int = 500
    #: Updater package cache size policy.
    keep_update_packages: int = DEFAULT_KEEP_UPDATE_PACKAGES
    #: Full-app backup trees retained.
    keep_previous_backups: int = DEFAULT_KEEP_PREVIOUS_BACKUPS
    #: Diagnostics zips retained.
    diagnostics_keep: int = DEFAULT_DIAGNOSTICS_KEEP
    #: Stale *.tmp/*.part residue minimum age (seconds).
    residue_min_age_sec: float = DEFAULT_RESIDUE_MIN_AGE_SEC

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> StorageSettings:
        d = data or {}
        return cls(
            compress_after_days=max(0, int(d.get("compress_after_days", 2))),
            max_total_mb_per_severity=max(0, int(d.get("max_total_mb_per_severity", 500))),
            keep_update_packages=max(
                1, int(d.get("keep_update_packages", DEFAULT_KEEP_UPDATE_PACKAGES))
            ),
            keep_previous_backups=max(
                1, int(d.get("keep_previous_backups", DEFAULT_KEEP_PREVIOUS_BACKUPS))
            ),
            diagnostics_keep=max(0, int(d.get("diagnostics_keep", DEFAULT_DIAGNOSTICS_KEEP))),
            residue_min_age_sec=max(
                0.0, float(d.get("residue_min_age_sec", DEFAULT_RESIDUE_MIN_AGE_SEC))
            ),
        )


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------


def _dir_size_bytes(path: Path) -> int:
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


def measure_usage(roots: dict[str, Path]) -> dict[str, int]:
    """Bytes per labeled root (logs, update_cache, backups, ...). Read-only."""
    return {name: _dir_size_bytes(Path(p)) for name, p in roots.items()}


# ---------------------------------------------------------------------------
# log compression + byte budget (severity tree only)
# ---------------------------------------------------------------------------


def compress_old_logs(
    severity_dir: Path,
    *,
    compress_after_days: int,
    now: float | None = None,
) -> dict[str, Any]:
    """gzip ``.log`` files older than ``compress_after_days`` (0 = no-op).

    Per-file atomic-ish sequence: gzip to ``<name>.gz`` -> verify the archive
    reads back -> only then unlink the original. The ACTIVE file (any file
    modified within the window) is never touched. Unknown subdirs ignored.
    """
    result: dict[str, Any] = {"compressed": 0, "bytes_saved": 0, "skipped": 0, "errors": []}
    if compress_after_days <= 0:
        return result
    now = now if now is not None else time.time()
    cutoff = now - 86400.0 * compress_after_days
    if not severity_dir.is_dir():
        return result
    for log in sorted(severity_dir.rglob("*.log")):
        try:
            if log.stat().st_mtime >= cutoff:
                result["skipped"] += 1
                continue
            gz_path = log.with_suffix(log.suffix + ".gz")
            if gz_path.exists():
                result["skipped"] += 1  # already compressed (prior interrupted run)
                continue
            raw = log.read_bytes()
            with open(gz_path, "wb") as gz:
                with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0) as writer:
                    writer.write(raw)
            # verify archive integrity BEFORE destroying the original
            with gzip.open(gz_path, "rb") as reader:
                if reader.read() != raw:
                    gz_path.unlink(missing_ok=True)
                    result["errors"].append(f"verify-failed: {log.name}")
                    continue
            saved = len(raw) - gz_path.stat().st_size
            log.unlink()
            result["compressed"] += 1
            result["bytes_saved"] += max(0, saved)
        except OSError as exc:
            result["errors"].append(f"{type(exc).__name__}: {exc}")
    return result


def enforce_byte_budget(
    severity_dir: Path,
    *,
    max_total_mb: int,
    protected: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Oldest-first removal inside ONE severity dir until it fits the budget.

    ``protected`` (the active file path, if any) is never removed. Removal
    order: oldest mtime first, ``.log`` before ``.log.gz`` at equal age so
    compressed evidence survives longest. Budget <= 0 disables the pass.
    """
    result: dict[str, Any] = {"removed": 0, "bytes_freed": 0, "budget_mb": max_total_mb}
    if max_total_mb <= 0 or not severity_dir.is_dir():
        return result
    budget = max_total_mb * 1024 * 1024
    now = now if now is not None else time.time()
    entries: list[tuple[float, int, Path]] = []
    for pattern in _LOG_SUFFIXES:
        for p in severity_dir.rglob(pattern):
            try:
                if not p.is_file():
                    continue
                st = p.stat()
                # .log.gz sorts before .log at equal mtime: 0 < 1
                kind = 0 if p.name.endswith(".gz") else 1
                entries.append((st.st_mtime, kind, p))
            except OSError:
                continue
    total = 0
    for _mtime, _kind, path in entries:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    if total <= budget:
        return result
    protected_resolved = protected.resolve() if protected is not None else None
    for _mtime, _kind, path in sorted(entries, key=lambda e: (e[0], e[1])):
        if total <= budget:
            break
        try:
            if protected_resolved is not None and path.resolve() == protected_resolved:
                continue
            # Never remove a file written within the last 60s even when over
            # budget: an active writer may still hold it open (Windows lock).
            if now - path.stat().st_mtime < 60.0:
                continue
            size = path.stat().st_size
            path.unlink()
            total -= size
            result["removed"] += 1
            result["bytes_freed"] += size
        except OSError as exc:
            result.setdefault("errors", []).append(f"{type(exc).__name__}: {exc}")
    return result


# ---------------------------------------------------------------------------
# updater-side cleanup (cache / stage / preserve / previous)
# ---------------------------------------------------------------------------


def prune_update_cache(
    cache_dir: Path,
    *,
    keep_packages: int,
) -> dict[str, Any]:
    """Keep the ``keep_packages`` newest completed packages in the cache dir.

    A completed package is any regular file that is not ``*.part`` (SafeDownloader
    renames ``<name>.part`` -> ``<name>`` only after SHA-256 verification, so every
    non-part file is a verified artifact). Stale ``*.part`` residue older than one
    hour is always removable (a live download re-creates it with HTTP Range).
    """
    result: dict[str, Any] = {"removed_packages": 0, "removed_parts": 0, "bytes_freed": 0}
    if not cache_dir.is_dir():
        return result
    now = time.time()
    packages: list[tuple[float, Path]] = []
    try:
        children = list(cache_dir.iterdir())
    except OSError:
        return result
    for child in children:
        try:
            if not child.is_file():
                continue
            if child.name.endswith(".part"):
                if now - child.stat().st_mtime > DEFAULT_RESIDUE_MIN_AGE_SEC:
                    size = child.stat().st_size
                    child.unlink(missing_ok=True)
                    result["removed_parts"] += 1
                    result["bytes_freed"] += size
                continue
            packages.append((child.stat().st_mtime, child))
        except OSError:
            continue
    for _mtime, path in sorted(packages, key=lambda e: e[0])[
        : max(0, len(packages) - keep_packages)
    ]:
        try:
            size = path.stat().st_size
            path.unlink()
            result["removed_packages"] += 1
            result["bytes_freed"] += size
        except OSError as exc:
            result.setdefault("errors", []).append(f"{type(exc).__name__}: {exc}")
    return result


def _rmtree_recorded(path: Path, result: dict[str, Any]) -> None:
    size = _dir_size_bytes(path)
    shutil.rmtree(path, ignore_errors=True)
    result["removed_dirs"] += 1
    result["bytes_freed"] += size


def _dir_mtime_fallback(path: Path) -> float:
    """Newest mtime among a tree's files (some filesystems don't bubble dir
    mtimes up on rename; rollback ordering must still be deterministic)."""
    newest = 0.0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    m = p.stat().st_mtime
                    newest = max(newest, m)
            except OSError:
                continue
    except OSError:
        return 0.0
    return newest


def sweep_crash_leftovers(app_root: Path, *, keep_previous_backups: int) -> dict[str, Any]:
    """Remove crash-leftover staging/preserve dirs and old full-app backups.

    * ``.update-stage-*`` / ``.preserve-*``: removable unconditionally — they
      only exist mid-install or after a hard crash; a successful install
      deletes them itself (backup_migrate.py:292-303).
    * ``.previous-<ts>`` / ``.previous`` full-app backups: keep the newest
      ``keep_previous_backups`` (rollback target), remove older ones.
      Freshness is measured by the newest FILE inside each tree (the dir
      mtime is unreliable: shutil.move/creation does not bubble content
      times up); this matches RollbackEngine's newest-first fallback.
    """
    result: dict[str, Any] = {"removed_dirs": 0, "bytes_freed": 0, "kept_backups": 0}
    if not app_root.is_dir():
        return result
    try:
        children = list(app_root.iterdir())
    except OSError:
        return result
    backups: list[tuple[float, Path]] = []
    for child in children:
        try:
            if not child.is_dir():
                continue
            name = child.name
            if name.startswith((_STAGE_PREFIX, _PRESERVE_PREFIX)):
                _rmtree_recorded(child, result)
            elif _PREVIOUS_RE.match(name):
                backups.append((_dir_mtime_fallback(child), child))
        except OSError:
            continue
    ordered = sorted(backups, key=lambda e: e[0], reverse=True)
    result["kept_backups"] = min(len(ordered), keep_previous_backups)
    for _mtime, path in ordered[keep_previous_backups:]:
        _rmtree_recorded(path, result)
    return result


def sweep_diagnostics(diagnostics_dir: Path, *, keep: int) -> dict[str, Any]:
    """Keep the newest ``keep`` diagnostics zips; remove older ones."""
    result: dict[str, Any] = {"removed": 0, "bytes_freed": 0}
    if keep <= 0 or not diagnostics_dir.is_dir():
        return result
    zips: list[tuple[float, Path]] = []
    for p in diagnostics_dir.glob("nexus-diagnostics-*.zip"):
        try:
            if p.is_file():
                zips.append((p.stat().st_mtime, p))
        except OSError:
            continue
    for _mtime, path in sorted(zips, key=lambda e: e[0])[: max(0, len(zips) - keep)]:
        try:
            size = path.stat().st_size
            path.unlink()
            result["removed"] += 1
            result["bytes_freed"] += size
        except OSError as exc:
            result.setdefault("errors", []).append(f"{type(exc).__name__}: {exc}")
    return result


def sweep_residue_files(root: Path, *, min_age_sec: float) -> dict[str, Any]:
    """Remove ``*.tmp`` / ``*.part`` files older than ``min_age_sec``.

    Bounded to depth 3 under ``root`` (never walks the whole disk) and to the
    residue suffixes only. A file younger than the age window is assumed to
    belong to a live download/extraction and is never touched.
    """
    result: dict[str, Any] = {"removed": 0, "bytes_freed": 0}
    if not root.is_dir():
        return result
    now = time.time()
    seen = 0
    try:
        iterator = root.rglob("*")
        for p in iterator:
            seen += 1
            if seen > 5000:  # bounded scan; a runaway tree must not hang boot
                result["truncated"] = True
                break
            try:
                if not p.is_file():
                    continue
                if not (p.name.endswith(".tmp") or p.name.endswith(".part")):
                    continue
                if now - p.stat().st_mtime < min_age_sec:
                    continue
                size = p.stat().st_size
                p.unlink()
                result["removed"] += 1
                result["bytes_freed"] += size
            except OSError:
                continue
    except OSError:
        return result
    return result


def _prune_empty_month_dirs(severity_dir: Path) -> dict[str, Any]:
    """Remove empty YYYY/MM leaf dirs left behind by retention (cosmetic)."""
    removed = 0
    for month_dir in sorted(severity_dir.glob("*/*"), reverse=True):
        try:
            if month_dir.is_dir() and not any(month_dir.iterdir()):
                month_dir.rmdir()
                removed += 1
        except OSError:
            continue
    for year_dir in sorted(severity_dir.glob("*"), reverse=True):
        try:
            if year_dir.is_dir() and not any(year_dir.iterdir()):
                year_dir.rmdir()
                removed += 1
        except OSError:
            continue
    return {"removed_empty_dirs": removed}


@dataclass
class SweepReport:
    """Aggregated result of one storage sweep (honest, per-action numbers)."""

    parts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, name: str, part: dict[str, Any]) -> None:
        self.parts[name] = part

    @property
    def total_bytes_freed(self) -> int:
        return int(sum(int(p.get("bytes_freed", 0)) for p in self.parts.values()))

    def to_dict(self) -> dict[str, Any]:
        return {"bytes_freed": self.total_bytes_freed, "parts": self.parts}
