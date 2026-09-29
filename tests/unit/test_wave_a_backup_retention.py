"""R-6 (Wave A) — backup retention: ``nexus db backup`` and the
/api/db/manage/backup route both wrote WAL-consistent SQLite copies into
artifacts/backups/ and never deleted one.

Measured on the shared checkout: 2.55 GB, three 436 MB copies of audit.db
plus -wal/-shm sidecars, all written 2026-09-24, none ever purged (and a
fourth family, audit_v7_*.bak, from the same week).

Section 66 forbids a purge without a retention owner — so the fix is a
bounded keep-N-newest policy OWNED by the backup command, not a one-off
deletion. This test pins the purge's safety properties: it touches only the
audit_backup_*.db family, keeps the newest N, and never raises.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from nexus_scalp.cli.db_commands import AUDIT_BACKUP_RETENTION, _prune_audit_backups


def _make_backups(root: Path, names: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    # Distinct mtimes, oldest first, so the newest-N selection is unambiguous.
    base = time.time() - (len(names) * 100.0)
    for i, name in enumerate(names):
        p = root / name
        p.write_bytes(b"backup")
        ts = base + (i * 100.0)
        os.utime(p, (ts, ts))
        for suffix in ("-wal", "-shm"):
            side = root / f"{name}{suffix}"
            side.write_bytes(b"sidecar")
            os.utime(side, (ts, ts))


def test_keeps_newest_and_prunes_oldest(tmp_path: Path) -> None:
    d = tmp_path / "backups"
    _make_backups(d, [f"audit_backup_2026092{i}-120000.db" for i in range(5)])
    removed = _prune_audit_backups(str(d), keep=AUDIT_BACKUP_RETENTION)
    assert len(removed) == 5 - AUDIT_BACKUP_RETENTION
    remaining = sorted(p.name for p in d.glob("audit_backup_*.db"))
    # The fixture writes names 20260920..20260924 with mtimes oldest-first,
    # so the newest three by mtime are 20260922/23/24 and 20/21 are pruned.
    assert remaining == [
        "audit_backup_20260922-120000.db",
        "audit_backup_20260923-120000.db",
        "audit_backup_20260924-120000.db",
    ]


def test_never_touches_a_foreign_file(tmp_path: Path) -> None:
    """A file that is not one of ours must survive the purge — the same
    directory holds migration snapshots (audit_v7_*.bak) and other domains'
    backups (news_v0_*.bak)."""
    d = tmp_path / "backups"
    _make_backups(d, [f"audit_backup_2026092{i}-120000.db" for i in range(5)])
    foreign = [
        "audit_v7_20260924T005049.bak",
        "news_v0_20260822T093437.bak",
        "audit_backup_notes.txt",
        "audit_backup_20260920-120000.db.bak",
    ]
    for f in foreign:
        (d / f).write_bytes(b"keep me")
    _prune_audit_backups(str(d), keep=AUDIT_BACKUP_RETENTION)
    for f in foreign:
        assert (d / f).exists(), f"the purge deleted a foreign file: {f}"


def test_sidecars_removed_with_their_main_file(tmp_path: Path) -> None:
    """A pruned main file's -wal/-shm sidecars go with it; a RETAINED file's
    sidecars must be left alone (they belong to a live backup)."""
    d = tmp_path / "backups"
    _make_backups(d, [f"audit_backup_2026092{i}-120000.db" for i in range(4)])
    removed = _prune_audit_backups(str(d), keep=AUDIT_BACKUP_RETENTION)
    assert len(removed) == 1
    pruned_main = removed[0]
    assert not os.path.exists(f"{pruned_main}-wal"), "stale -wal left behind"
    assert not os.path.exists(f"{pruned_main}-shm"), "stale -shm left behind"
    # Retained mains keep their sidecars.
    for main in d.glob("audit_backup_*.db"):
        assert os.path.exists(f"{main}-wal"), f"retained backup lost its -wal: {main}"


def test_no_prune_when_within_retention(tmp_path: Path) -> None:
    d = tmp_path / "backups"
    _make_backups(d, [f"audit_backup_2026092{i}-120000.db" for i in range(2)])
    removed = _prune_audit_backups(str(d), keep=AUDIT_BACKUP_RETENTION)
    assert removed == []
    assert len(list(d.glob("audit_backup_*.db"))) == 2


def test_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    """The backup command must never fail because the purge had nothing to
    do — a first-ever backup runs against a directory with no history."""
    assert _prune_audit_backups(str(tmp_path / "nope"), keep=3) == []


def test_retention_is_small_and_non_negative() -> None:
    """The policy is keep-N-newest with a small N: a backup insures the
    migration that follows it, so the newest few are the whole value."""
    assert 0 < AUDIT_BACKUP_RETENTION <= 5
    # A negative keep would delete everything, which is not the policy.
    assert _prune_audit_backups(str("/nonexistent-path"), keep=-1) == []
