"""Retention for orphaned snapshot classes and pre-cleanup snapshot sets.

Extends the R9 producer-side retention contract (``test_r9_backup_retention``)
with the two classes the original pruner deliberately could not name:

* ``audit_v7_<ts>.bak`` / ``news_v0_<ts>.bak`` — manual, pre-v8 safety
  snapshots. No code producer writes them, so the original regex (a strict
  producer-name match) left them accumulating forever.
* ``hygiene-<YYYYMMDD>/`` directories — pre-cleanup snapshot *sets*. Removed
  as a single retention unit, never member-by-member.

These tests pin the same safety floor the R9 suite pins for the producer's
own backups: nothing is collected that the registry cannot name, nothing
outside the backup directory is touched, and a partially-removed set is
re-listable rather than corrupting.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, "src")

from nexus_scalp.hygiene.backup_retention import (
    _FRESH_SET_GRACE_DAYS,
    BACKUP_DIR,
    RetentionResult,
    list_backups,
    list_snapshot_sets,
    prune_backups,
)


def _make(
    backup_dir: str,
    name: str,
    *,
    size: int = 4096,
    age_days: float = 1.0,
) -> str:
    path = os.path.join(backup_dir, name)
    with open(path, "wb") as fh:
        fh.seek(size - 1)
        fh.write(b"\0")
    t = time.time() - (age_days * 86400.0)
    os.utime(path, (t, t))
    return path


def _make_set(
    backup_dir: str,
    name: str,
    *,
    members: tuple[str, ...] = ("audit.db.snapshot", "news.db.snapshot"),
    size: int = 4096,
    age_days: float = 1.0,
) -> str:
    path = os.path.join(backup_dir, name)
    os.makedirs(path, exist_ok=True)
    for member in members:
        with open(os.path.join(path, member), "wb") as fh:
            fh.seek(size - 1)
            fh.write(b"\0")
    t = time.time() - (age_days * 86400.0)
    os.utime(path, (t, t))
    return path


# --- registry scope -------------------------------------------------------


def test_registry_names_the_orphaned_bak_classes() -> None:
    """The two manual ``.bak`` schemes are collected; nothing else is."""
    import re

    from nexus_scalp.hygiene.backup_retention import _ORPHAN_SNAPSHOT_RES

    names = {
        "audit_v7_20260924T005049.bak": True,
        "news_v0_20260822T093437.bak": True,
        "audit_backup_20260924-212854.db": False,
        "audit_v7_20260924T005049.db": False,
        "audit_v8_20260924T005049.bak": False,
        "news_v0_20260822T093437.bak.tmp": False,
    }
    for name, expected in names.items():
        assert bool(any(p.match(name) for p in _ORPHAN_SNAPSHOT_RES)) is expected, name
    assert isinstance(_ORPHAN_SNAPSHOT_RES, tuple)
    assert all(isinstance(p, re.Pattern) for p in _ORPHAN_SNAPSHOT_RES)


def test_each_orphaned_scheme_is_independently_named() -> None:
    """Either registered scheme alone is enough to collect its own files.

    Guards against a single over-broad pattern silently covering (or missing)
    both classes.
    """
    from nexus_scalp.hygiene.backup_retention import _ORPHAN_SNAPSHOT_RES

    audit = [p for p in _ORPHAN_SNAPSHOT_RES if p.match("audit_v7_20260924T005049.bak")]
    news = [p for p in _ORPHAN_SNAPSHOT_RES if p.match("news_v0_20260822T093437.bak")]
    assert len(audit) == 1
    assert len(news) == 1
    assert audit[0] is not news[0]


# --- list_backups: orphaned .bak files ------------------------------------


def test_list_backups_collects_orphaned_bak_files(backup_dir: str) -> None:
    """A manual ``.bak`` snapshot is listed with its real mtime and size."""
    _make(backup_dir, "audit_v7_20260924T005049.bak", size=8192, age_days=10)
    _make(backup_dir, "news_v0_20260822T093437.bak", size=4096, age_days=20)

    found = list_backups(backup_dir)
    by_name = {os.path.basename(p): (m, s) for p, m, s in found}
    assert set(by_name) == {
        "audit_v7_20260924T005049.bak",
        "news_v0_20260822T093437.bak",
    }
    assert by_name["audit_v7_20260924T005049.bak"][1] == 8192
    assert by_name["news_v0_20260822T093437.bak"][1] == 4096


def test_list_backups_rejects_unknown_bak_schemes(backup_dir: str) -> None:
    """An unregistered ``.bak`` name is left alone — the pruner never widens."""
    _make(backup_dir, "strategies_v3_20260924T005049.bak", age_days=400)
    _make(backup_dir, "audit_v7_20260924T005049.bak.tmp", age_days=400)

    assert list_backups(backup_dir) == []


def test_list_backups_mixes_producer_and_orphan_classes(backup_dir: str) -> None:
    """Both classes share one oldest-first ordering and one age rule."""
    _make(backup_dir, "audit_backup_20260924-212854.db", age_days=1)
    _make(backup_dir, "audit_v7_20260924T005049.bak", age_days=5)
    _make(backup_dir, "news_v0_20260822T093437.bak", age_days=20)

    found = list_backups(backup_dir)
    ages = [age for _p, age, _s in found]
    assert ages == sorted(ages)
    assert len(found) == 3


# --- list_snapshot_sets ---------------------------------------------------


def test_list_snapshot_sets_collects_expired_hygiene_dirs(backup_dir: str) -> None:
    """An expired ``hygiene-<date>`` set is one retention unit, summed size."""
    _make_set(
        backup_dir,
        "hygiene-20260928",
        members=("audit.db.snapshot", "news.db.snapshot", "strategies.db.snapshot"),
        size=2048,
        age_days=_FRESH_SET_GRACE_DAYS + 10,
    )

    sets = list_snapshot_sets(backup_dir)
    assert len(sets) == 1
    path, mtime, size = sets[0]
    assert os.path.basename(path) == "hygiene-20260928"
    assert size == 2048 * 3
    assert mtime < time.time()


def test_list_snapshot_sets_protects_a_fresh_set(backup_dir: str) -> None:
    """A set inside the grace window is invisible to the pruner."""
    _make_set(backup_dir, "hygiene-20260928", size=757 * 1024 * 1024, age_days=2)

    assert list_snapshot_sets(backup_dir) == []


def test_list_snapshot_sets_ignores_backup_shaped_dirs(backup_dir: str) -> None:
    """A directory that is not a registered set is not collected."""
    _make_set(backup_dir, "audit_backup_20260924-212854.db", age_days=100)
    _make_set(backup_dir, "manual-snapshot-2026", age_days=100)

    assert list_snapshot_sets(backup_dir) == []


def test_list_snapshot_sets_ignores_nested_directories(backup_dir: str) -> None:
    """A set nested inside another is never collected (no recursion)."""
    outer = os.path.join(backup_dir, "hygiene-20260928")
    _make_set(outer, "hygiene-20260901", age_days=_FRESH_SET_GRACE_DAYS + 20)
    # Age the parent *after* its member exists, so creating the member does not
    # refresh the parent's mtime past the grace window.
    t = time.time() - ((_FRESH_SET_GRACE_DAYS + 10) * 86400.0)
    os.utime(outer, (t, t))

    found = list_snapshot_sets(backup_dir)
    assert [os.path.basename(p) for p, _m, _s in found] == ["hygiene-20260928"]


# --- prune_backups: orphaned files ----------------------------------------


def test_prune_removes_expired_orphaned_bak(backup_dir: str) -> None:
    """An expired manual ``.bak`` is removed; a fresh one is kept."""
    _make(backup_dir, "audit_v7_20260924T005049.bak", size=1024, age_days=100)
    _make(backup_dir, "audit_v7_20260928T120000.bak", size=2048, age_days=1)

    result = prune_backups(backup_dir, max_age_days=30.0)

    assert result.removed == 1
    assert result.bytes_reclaimed == 1024
    assert not os.path.exists(os.path.join(backup_dir, "audit_v7_20260924T005049.bak"))
    assert os.path.exists(os.path.join(backup_dir, "audit_v7_20260928T120000.bak"))


def test_prune_keeps_orphaned_bak_under_retention(backup_dir: str) -> None:
    """A 5-day-old manual snapshot is well inside the 30-day floor."""
    _make(backup_dir, "audit_v7_20260924T005049.bak", size=414 * 1024 * 1024, age_days=5)

    result = prune_backups(backup_dir, max_age_days=30.0, max_snapshots=10)

    assert result.removed == 0
    assert os.path.exists(os.path.join(backup_dir, "audit_v7_20260924T005049.bak"))


def test_prune_counts_orphaned_bak_against_the_snapshot_budget(backup_dir: str) -> None:
    """Orphaned files and producer backups share one ``max_snapshots`` budget."""
    for i in range(4):
        _make(backup_dir, f"audit_backup_2026092{i}-120000.db", age_days=10 - i)
    _make(backup_dir, "audit_v7_20260910T120000.bak", age_days=9)

    result = prune_backups(backup_dir, max_age_days=30.0, max_snapshots=2)

    # 5 snapshots, keep the newest 2: the 3 oldest are surplus.
    assert result.removed == 3
    assert result.kept == 2


# --- prune_backups: snapshot sets -----------------------------------------


def test_prune_removes_an_expired_set_whole(backup_dir: str) -> None:
    """An expired set removes every member and the directory itself."""
    _make_set(
        backup_dir,
        "hygiene-20260928",
        members=("audit.db.snapshot", "news.db.snapshot"),
        size=4096,
        age_days=100,
    )

    result = prune_backups(backup_dir, max_age_days=30.0)

    assert result.removed == 1
    assert result.bytes_reclaimed == 4096 * 2
    assert not os.path.exists(os.path.join(backup_dir, "hygiene-20260928"))


def test_prune_keeps_a_fresh_set(backup_dir: str) -> None:
    """A set inside the grace window must survive a prune."""
    _make_set(backup_dir, "hygiene-20260928", size=757 * 1024 * 1024, age_days=2)

    result = prune_backups(backup_dir, max_age_days=30.0)

    assert result.removed == 0
    assert os.path.isdir(os.path.join(backup_dir, "hygiene-20260928"))


def test_prune_never_remembers_a_partially_removed_set(backup_dir: str) -> None:
    """A set that cannot be fully removed is reported, not silently dropped."""
    set_dir = _make_set(
        backup_dir,
        "hygiene-20260928",
        members=("audit.db.snapshot", "news.db.snapshot"),
        age_days=100,
    )
    # Make the directory non-writable so member deletion raises across all
    # platforms (POSIX requires write permission on the directory to unlink a
    # child; Windows handles read-only on the directory similarly).
    os.chmod(set_dir, 0o500)
    try:
        result = prune_backups(backup_dir, max_age_days=30.0)
    finally:
        os.chmod(set_dir, 0o700)

    # Either the set was skipped due to the permission error, or it failed
    # during member removal. Either way, it must be reported in failures.
    assert result.skipped >= 1
    assert "hygiene-20260928" in result.failures
    # The set survives and can be re-listed for retry on the next backup.
    assert os.path.isdir(set_dir)


def test_prune_applies_the_delete_batch_to_sets_and_files(backup_dir: str) -> None:
    """``delete_batch`` bounds total work across both classes."""
    _make(backup_dir, "audit_v7_20260901T000000.bak", age_days=60)
    _make(backup_dir, "audit_v7_20260902T000000.bak", age_days=59)
    _make_set(backup_dir, "hygiene-20260901", age_days=58)

    result = prune_backups(backup_dir, max_age_days=30.0, delete_batch=2)

    assert result.removed == 2
    remaining_bak = [f for f in os.listdir(backup_dir) if f.endswith(".bak")]
    remaining_sets = [f for f in os.listdir(backup_dir) if f.startswith("hygiene-")]
    # Exactly one of the three expired candidates survived the batch limit.
    assert (len(remaining_bak) + len(remaining_sets)) == 1


# --- safety floor ---------------------------------------------------------


def test_prune_cannot_escape_the_backup_directory(backup_dir: str, tmp_path) -> None:
    """A symlinked set pointing outside is never followed."""
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable on this platform")
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"\0" * 4096)
    link = os.path.join(backup_dir, "hygiene-20260928")
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"cannot create a symlink without elevated privileges: {exc}")

    result = prune_backups(backup_dir, max_age_days=0.0)

    assert result.removed == 0
    assert outside.exists()


def test_prune_reports_zero_when_backup_dir_is_missing(tmp_path) -> None:
    """A absent backup directory is an empty result, never an exception."""
    result = prune_backups(str(tmp_path / "absent"), max_age_days=30.0)

    assert isinstance(result, RetentionResult)
    assert result.removed == 0 and result.kept == 0


def test_default_backup_dir_is_the_producers_own() -> None:
    """Retention still targets the directory both producers write into."""
    assert BACKUP_DIR == os.path.join("artifacts", "backups")
