"""R-9 (Phase-2 remediation) — backup retention pins.

Measured defect (Phase-2 matrix R-9): ``artifacts/backups/`` reached 2.40 GiB
with two producers (``nexus db backup`` and ``POST /api/db/manage/backup``) and
zero pruners. ``artifacts/`` is gitignored, so no external cleanup ever sees
it; two of three observed snapshots were the same DB copied 2 s apart.

PART 3 §21 requires a purge to name policy / owner / batch / telemetry /
retry / failure-recovery. This module is that subsystem for backups, and these
tests pin the whole contract:

  1. the AGE rule removes an expired snapshot however many remain;
  2. the COUNT rule keeps only the newest ``max_snapshots``;
  3. the operation is bounded (``delete_batch`` caps work per call);
  4. only files matching the producer's naming scheme are ever considered —
     a copied-in artifact or a hand-placed file is invisible to the pruner;
  5. a path-traversal / symlinked entry cannot escape the backup directory;
  6. one unremovable file is reported, not fatal — the next backup retries;
  7. the producer reports the outcome (kept/removed/bytes) in its response.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from nexus_scalp.hygiene.backup_retention import (
    BACKUP_DIR,
    RetentionResult,
    list_backups,
    prune_backups,
)


def _make_backup(backup_dir: str, name: str, size: int = 1024, age_days: float = 0.0) -> str:
    path = os.path.join(backup_dir, name)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    if age_days:
        when = time.time() - (age_days * 86400.0)
        os.utime(path, (when, when))
    return path


def test_an_empty_directory_prunes_to_nothing(backup_dir: str) -> None:
    result = prune_backups(backup_dir)
    assert result.removed == 0
    assert result.kept == 0
    assert result.as_payload()["removed"] == 0


def test_the_age_rule_removes_expired_snapshots(backup_dir: str) -> None:
    """A snapshot older than the horizon cannot serve any restore."""
    _make_backup(backup_dir, "audit_backup_20260101-000000.db", age_days=45)
    _make_backup(backup_dir, "audit_backup_20260201-000000.db", age_days=20)

    result = prune_backups(backup_dir, max_age_days=30.0)

    assert result.removed == 1
    assert result.kept == 1
    assert os.path.exists(os.path.join(backup_dir, "audit_backup_20260201-000000.db"))
    assert not os.path.exists(os.path.join(backup_dir, "audit_backup_20260101-000000.db"))


def test_the_count_rule_keeps_only_the_newest(backup_dir: str) -> None:
    """The producers write whole copies; the oldest are redundant."""
    for i in range(6):
        _make_backup(backup_dir, f"audit_backup_2026030{i}-000000.db")

    result = prune_backups(backup_dir, max_snapshots=2, max_age_days=999.0)

    assert result.removed == 4
    assert result.kept == 2
    remaining = sorted(os.listdir(backup_dir))
    assert remaining == ["audit_backup_20260304-000000.db", "audit_backup_20260305-000000.db"]


def test_both_rules_apply_independently(backup_dir: str) -> None:
    """Age removes the expired, count removes the redundant survivors."""
    _make_backup(backup_dir, "audit_backup_20260101-000000.db", age_days=100)
    _make_backup(backup_dir, "audit_backup_20260201-000000.db", age_days=80)
    _make_backup(backup_dir, "audit_backup_20260928-120000.db")

    result = prune_backups(backup_dir, max_snapshots=1, max_age_days=30.0)

    assert result.removed == 2
    assert result.kept == 1
    assert os.listdir(backup_dir) == ["audit_backup_20260928-120000.db"]


def test_the_operation_is_batched(backup_dir: str) -> None:
    """A huge directory cannot make a single prune unbounded."""
    for i in range(10):
        _make_backup(backup_dir, f"audit_backup_2026030{i}-000000.db")

    one = prune_backups(backup_dir, max_snapshots=1, delete_batch=3)
    assert one.removed == 3, "the batch caps the work in one call"
    assert one.kept == 10 - 3, "kept counts the backups still on disk"

    # Two batches of 3 removed; the newest 4 survive (6..9).
    two = prune_backups(backup_dir, max_snapshots=1, delete_batch=3)
    assert two.removed == 3
    remaining = sorted(os.listdir(backup_dir))
    assert remaining == [
        "audit_backup_20260306-000000.db",
        "audit_backup_20260307-000000.db",
        "audit_backup_20260308-000000.db",
        "audit_backup_20260309-000000.db",
    ]

    # A final call reaches the retention target: one newest snapshot.
    three = prune_backups(backup_dir, max_snapshots=1, delete_batch=3)
    assert three.removed == 3
    assert len(os.listdir(backup_dir)) == 1


def test_only_the_producers_own_names_are_ever_removed(backup_dir: str) -> None:
    """A copied-in artifact or a hand-placed file is invisible to the pruner."""
    _make_backup(backup_dir, "audit_backup_20260101-000000.db", age_days=100)
    _make_backup(backup_dir, "manual_copy.db", age_days=100)
    _make_backup(backup_dir, "notes.txt", age_days=100)
    _make_backup(backup_dir, "audit_backup_20260101-000000.db.bak", age_days=100)

    result = prune_backups(backup_dir, max_age_days=30.0)

    assert result.removed == 1
    assert set(os.listdir(backup_dir)) == {
        "manual_copy.db",
        "notes.txt",
        "audit_backup_20260101-000000.db.bak",
    }


def test_a_path_resolving_outside_the_directory_is_skipped(backup_dir: str) -> None:
    """An entry that resolves outside the backup root is never deleted.

    ``_is_safe_child`` compares the resolved parent against the resolved root,
    so an entry pointing at another directory is rejected before any remove.
    On hosts where symlinks need a privilege (Windows without
    SeCreateSymbolicLinkPrivilege) the escape is exercised directly instead.
    """
    outside_dir = os.path.dirname(backup_dir)
    victim = os.path.join(outside_dir, "victim.db")
    _make_backup(outside_dir, "victim.db", age_days=100)

    escaped = False
    try:
        os.symlink(victim, os.path.join(backup_dir, "audit_backup_20260928-120000.db"))
        escaped = True
    except OSError:
        # No symlink privilege: simulate the same resolution outcome by
        # asserting the guard rejects a target outside the root directly.
        pass

    if escaped:
        result = prune_backups(backup_dir, max_age_days=30.0)
        assert result.removed == 0
        assert os.path.exists(victim), "the symlink target must survive"
    else:
        # The guard must still reject an out-of-root resolution.
        from nexus_scalp.hygiene.backup_retention import _is_safe_child

        assert _is_safe_child(backup_dir, victim) is False
        assert prune_backups(backup_dir, max_age_days=30.0).removed == 0
        assert os.path.exists(victim)


def test_one_unremovable_file_is_reported_not_fatal(backup_dir: str, monkeypatch: Any) -> None:
    """A held-open handle must not abort the purge (PART 3 §21 failure path)."""
    _make_backup(backup_dir, "audit_backup_20260101-000000.db", age_days=100)
    _make_backup(backup_dir, "audit_backup_20260201-000000.db", age_days=100)

    calls: list[str] = []

    real_remove = os.remove

    def _flaky_remove(path: str) -> None:
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("held open by another process")
        real_remove(path)

    monkeypatch.setattr(os, "remove", _flaky_remove)

    result = prune_backups(backup_dir, max_age_days=30.0)

    assert result.removed == 1
    assert result.skipped == 1
    assert len(result.failures) == 1
    assert os.path.exists(os.path.join(backup_dir, "audit_backup_20260101-000000.db"))


def test_the_producer_reports_the_outcome(backup_dir: str) -> None:
    """kept/removed/bytes must reach the operator (PART 3 §21 telemetry)."""
    _make_backup(backup_dir, "audit_backup_20260101-000000.db", size=4096, age_days=100)

    result = prune_backups(backup_dir, max_age_days=30.0)

    payload = result.as_payload()
    assert payload["removed"] == 1
    assert payload["bytes_reclaimed"] == 4096
    assert payload["kept"] == 0
    assert payload["failures"] == []


def test_list_backups_ignores_directories_and_missing_dirs(backup_dir: str) -> None:
    """A backup-shaped subdirectory is not a snapshot; a missing dir is empty."""
    from pathlib import Path

    os.makedirs(os.path.join(backup_dir, "audit_backup_20260101-000000.db"))
    assert list_backups(backup_dir) == []
    # A missing directory is an empty result, never an exception.
    assert list_backups(str(Path(backup_dir) / "absent")) == []


def test_the_default_dir_is_the_producers_own() -> None:
    """Retention targets exactly the directory both producers write into."""
    assert BACKUP_DIR == os.path.join("artifacts", "backups")


def test_retention_result_defaults() -> None:
    r = RetentionResult()
    assert r.removed == 0 and r.kept == 0 and r.skipped == 0
    assert r.bytes_reclaimed == 0
    assert r.failures == []
