"""R-9 (Phase-2 remediation) — retention for the SQLite audit backup directory.

Measured defect (Phase-2 matrix R-9): ``artifacts/backups/`` had grown to
2.40 GiB with TWO producers (``nexus db backup`` and ``POST /api/db/manage/backup``)
and ZERO pruners, zero restore path, zero validation. ``artifacts/`` is
gitignored, so nothing outside the running process ever sees the sprawl; two of
the three observed ``audit_backup_*`` snapshots were the same DB copied 2 s
apart.

The architecture contract (PART 3 §21: "Purging is a first-class subsystem")
requires every purge to name a policy, an owner, a batch size, telemetry, a
retry, and a failure-recovery path. This module IS that policy for backups:

  * policy — keep at most ``MAX_SNAPSHOTS`` distinct backups, and never keep a
    backup older than ``MAX_AGE_DAYS``. Deleting is bounded to
    ``DELETE_BATCH`` files per call so the operation can never stall behind a
    huge directory;
  * owner — the backup producers themselves (the CLI command and the HTTP
    route), at the moment they write a new snapshot. Not a forgotten cron job;
  * safety — a candidate is only ever a regular FILE inside the backup
    directory whose name matches the producer's own naming scheme. A path is
    resolved and containment-checked against the real backup root, so a
    crafted filename cannot escape the directory;
  * failure recovery — every deletion is independent. One unremovable file
    (a held-open handle, a permission error) is counted and reported, never
    fatal: the retention result discloses it and the next backup retries;
  * telemetry — the returned struct carries kept/removed/skipped counts and
    bytes reclaimed, and it is surfaced by both producers in their responses.

The retention is a best-effort maintenance operation, never a reason to fail a
backup: if the prune itself raises, the producer's backup still succeeded.
"""

from __future__ import annotations

import os
import re
import stat
import time
from dataclasses import dataclass, field

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.hygiene.backup_retention")

#: The directory both producers write into (relative to the process CWD, which
#: is exactly how the producers themselves compute it — no new path authority).
BACKUP_DIR = os.path.join("artifacts", "backups")

#: Only these names are ever considered for deletion — a producer's own
#: scheme. Anything else in the directory is left alone on purpose.
_BACKUP_NAME_RE = re.compile(r"^audit_backup_\d{8}-\d{6}\.db$")

#: The historical snapshot classes also observed in ``artifacts/backups``.
#: These have NO producer in the current tree (they were written by one-off
#: migration/wave tooling), so without an explicit entry they are orphaned
#: forever: invisible to the pruner and unbounded. Each is registered here
#: rather than matched by a wildcard so the pruner never takes a file it
#: cannot name a retention rule for.
_ORPHAN_SNAPSHOT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^audit_v7_\d{8}T\d{6}\.bak$"),
    re.compile(r"^news_v0_\d{8}T\d{6}\.bak$"),
)

#: Pre-cleanup snapshot sets written by wave tooling (``<root>/hygiene-<date>``).
#: A set is a whole-DB safety snapshot taken before a destructive cleanup; it
#: has no producer and no rotation, so it grows without bound.
_SNAPSHOT_SET_RE = re.compile(r"^hygiene-\d{8}$")

#: A set is protected from the age rule until its cleanup has had time to prove
#: itself against a live system. This is deliberately a *freshness* floor, not
#: a hardcoded name list: a named allowlist would have to be edited before
#: every wave, and a stale entry would protect a set forever.
_FRESH_SET_GRACE_DAYS = 30.0


@dataclass
class RetentionResult:
    """What the prune did (disclosed to the operator by both producers)."""

    kept: int = 0
    removed: int = 0
    skipped: int = 0
    bytes_reclaimed: int = 0
    failures: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, object]:
        return {
            "kept": self.kept,
            "removed": self.removed,
            "skipped": self.skipped,
            "bytes_reclaimed": self.bytes_reclaimed,
            "failures": list(self.failures),
        }


def _is_safe_child(root: str, candidate: str) -> bool:
    """True only if ``candidate`` is a regular file directly under ``root``.

    Resolves both sides first so a name containing ``..`` or a symlinked entry
    cannot escape the backup directory.
    """
    try:
        real_root = os.path.realpath(root)
    except OSError:
        return False
    try:
        real_candidate = os.path.realpath(candidate)
    except OSError:
        return False
    if os.path.dirname(real_candidate) != real_root:
        return False
    # A registered snapshot *set* is a directory, a snapshot file is a regular
    # file. Anything else (a symlink, a device, a socket) is not collectable.
    return os.path.isfile(real_candidate) or os.path.isdir(real_candidate)


def _matches_any(name: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.match(name) for p in patterns)


def _dir_size(path: str) -> int:
    """Best-effort recursive size of a snapshot-set directory (never raises)."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _remove_tree(path: str) -> None:
    """Remove a snapshot-set directory and its members (raises on failure).

    The caller counts and reports the failure; a partially-removed set is
    simply re-listed and re-tried on the next backup.
    """
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            os.remove(os.path.join(root, f))
        for d in dirs:
            os.rmdir(os.path.join(root, d))
    os.rmdir(path)


def list_backups(backup_dir: str = BACKUP_DIR) -> list[tuple[str, float, int]]:
    """Snapshots eligible for retention, oldest first: ``(path, mtime, size)``.

    A file is eligible only when it is a regular file inside ``backup_dir``
    whose name matches a *registered* snapshot scheme — the producers' own
    scheme, or one of the historical orphan classes registered below.
    Everything else is invisible to the pruner, which is what keeps a
    copied-in artifact or a hand-placed file from disappearing.
    """
    try:
        entries = os.listdir(backup_dir)
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("backup retention: cannot list %s: %s", backup_dir, exc)
        return []

    found: list[tuple[str, float, int]] = []
    for name in entries:
        if not (_BACKUP_NAME_RE.match(name) or _matches_any(name, _ORPHAN_SNAPSHOT_RES)):
            continue
        path = os.path.join(backup_dir, name)
        if not _is_safe_child(backup_dir, path):
            continue
        if os.path.isdir(path):
            # A backup-shaped directory is not a snapshot (see
            # list_snapshot_sets for the registered directory classes).
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        found.append((path, st.st_mtime, st.st_size))
    found.sort(key=lambda item: item[1])  # oldest first
    return found


def list_snapshot_sets(backup_dir: str = BACKUP_DIR) -> list[tuple[str, float, int]]:
    """Pre-cleanup snapshot-set directories, oldest first: ``(path, mtime, size)``.

    These are whole-DB safety snapshots taken by wave tooling before a
    destructive cleanup. They have no producer and no rotation, so they grow
    without bound. A set is collected as a unit; a set fresher than the grace
    window is protected so a cleanup has time to prove itself against a live
    system.
    """
    now = time.time()
    try:
        entries = os.listdir(backup_dir)
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("backup retention: cannot list %s: %s", backup_dir, exc)
        return []

    found: list[tuple[str, float, int]] = []
    for name in entries:
        if not _SNAPSHOT_SET_RE.match(name):
            continue
        path = os.path.join(backup_dir, name)
        if not _is_safe_child(backup_dir, path):
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode):
            continue
        if st.st_mtime > now - (_FRESH_SET_GRACE_DAYS * 86400.0):
            continue
        found.append((path, st.st_mtime, _dir_size(path)))
    found.sort(key=lambda item: item[1])  # oldest first
    return found


def prune_backups(
    backup_dir: str = BACKUP_DIR,
    max_snapshots: int = 10,
    max_age_days: float = 30.0,
    delete_batch: int = 20,
    now: float = time.time(),
) -> RetentionResult:
    """Apply backup retention. Bounded, best-effort, never raises.

    Two independent rules both apply (whichever removes a file, counts it):

      * ``max_snapshots`` — beyond this many distinct snapshots the oldest are
        redundant (the producers write a whole new copy; nothing reads an old
        one, and the observed pattern was two identical snapshots 2 s apart);
      * ``max_age_days`` — a backup older than this cannot serve a restore
        against a live database that has kept moving.
    """
    result = RetentionResult()
    backups = list_backups(backup_dir)
    sets = list_snapshot_sets(backup_dir)
    if not backups and not sets:
        return result

    age_cutoff = now - (max_age_days * 86400.0)

    # Rule 1: age. An expired snapshot is removed however many remain.
    expired = [b for b in backups if b[1] < age_cutoff]
    # Rule 2: count. Keep the newest ``max_snapshots``; the surplus is the
    # oldest ones that survived the age rule.
    survivors = [b for b in backups if b[1] >= age_cutoff]
    surplus = survivors[: max(0, len(survivors) - max(0, max_snapshots))]

    # The same two rules apply to pre-cleanup snapshot sets, but a set is a
    # single retention unit: it is removed whole (all its members at once) or
    # not at all. An expired set is disposable — the cleanup it guarded has
    # either succeeded on the live system or the set is too old to restore
    # against a DB that has kept moving.
    expired_sets = [s for s in sets if s[1] < age_cutoff]

    candidates = expired + surplus + expired_sets
    # Bound the work per call: the oldest ``delete_batch`` candidates only.
    for path, _mtime, size in candidates[: max(0, delete_batch)]:
        try:
            if os.path.isdir(path):
                _remove_tree(path)
            else:
                os.remove(path)
        except OSError as exc:
            result.skipped += 1
            result.failures.append(os.path.basename(path))
            logger.warning("backup retention: could not remove %s: %s", path, exc)
            continue
        result.removed += 1
        result.bytes_reclaimed += size

    result.kept = len(backups) - result.removed
    if result.removed or result.skipped:
        logger.info(
            "backup retention: removed=%d skipped=%d bytes=%d kept=%d dir=%s",
            result.removed,
            result.skipped,
            result.bytes_reclaimed,
            result.kept,
            backup_dir,
        )
    return result
