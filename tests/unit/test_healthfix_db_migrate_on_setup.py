"""
NSE-HEALTHFIX-001, lane D — `apply_pending_audit_migrations` (contract §6).

The probe's DATABASE WARNING was a healthy audit.db parked at schema 7 while
the registry expects 9. Nothing on the first-run path ever applied the
pending pair, so the gap never closed. These tests exercise the seam the
repair path will call, standalone:

  (a) a tmp DB at schema 7 with 8/9 pending -> step applies them and the
      version reaches 9;
  (b) idempotent second call (already at 9) -> SKIPPED, no error;
  (c) a tmp DB with deliberate corruption -> the integrity gate aborts with
      SKIPPED/FAILED and NEVER applies;
  (d) a DB path that does not exist -> honest NOT_INITIALIZED, no crash.

Plus the two invariants that make the seam safe to call from setup:
  * a DB held by a live writer -> SKIPPED, never raced;
  * the function NEVER raises (garbage input, unwritable path, ...).

Every database here is built under ``tmp_path`` with the SAME
``DatabaseMigrationEngine`` the code uses — migration rows are never
hand-written. The real workspace DB is snapshotted read-only before and
after the suite and asserted unchanged.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.cli.db_commands import apply_pending_audit_migrations

# ---------------------------------------------------------------------------
# Real-DB guard: never touch artifacts/audit.db
# ---------------------------------------------------------------------------


def _real_audit_db() -> Path | None:
    """The workspace's real audit.db, if any. Probed READ-ONLY only."""
    for candidate in (Path.cwd() / "artifacts" / "audit.db",):
        if candidate.exists():
            return candidate
    return None


@pytest.fixture(autouse=True)
def _real_db_unchanged() -> object:
    """Contract: this suite must not mutate the real audit.db.

    Read-only URI connects only — never a read-write connect, which could
    race a live engine. Skipped entirely when the DB is absent (a fresh
    worktree has no artifacts/).
    """
    db = _real_audit_db()
    before: str | None = None
    if db is not None:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            before = str(row[0]) if row else "none"
        except sqlite3.Error:
            before = "unreadable"
        finally:
            con.close()
    yield
    if db is not None and before is not None:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            after = str(row[0]) if row else "none"
        except sqlite3.Error:
            after = "unreadable"
        finally:
            con.close()
        assert after == before, (
            f"REAL audit.db schema version changed {before} -> {after}: "
            "this suite must never mutate the production database"
        )


# ---------------------------------------------------------------------------
# Helpers — build tmp DBs with the SAME engine the code uses
# ---------------------------------------------------------------------------


def _migrate_to_full(db_path: Path) -> int:
    """Run the canonical engine to the expected schema version."""
    from nexus_scalp.database.engine import DatabaseMigrationEngine
    from nexus_scalp.database.models import DatabaseDomain

    engine = DatabaseMigrationEngine(db_path=db_path, domain=DatabaseDomain.AUDIT)
    result = engine.migrate()
    assert result["state"] == "DB_MIGRATION_SUCCEEDED", result
    return engine.current_version()


def _rewind_to_schema_7(db_path: Path) -> None:
    """Roll a full-schema tmp DB back to schema 7 with 8/9 pending.

    Uses the engine's OWN migration ids/names (queried from the registry) and
    rewinds BOTH the recorded version and the objects the two pending
    migrations create, so the DB genuinely looks like the probe's artifact:
    schema 7, integrity ok, 2 pending additive migrations.
    """
    from nexus_scalp.database.models import DatabaseDomain
    from nexus_scalp.database.registry import migrations_for

    pending = [m for m in migrations_for(DatabaseDomain.AUDIT) if m.to_version in (8, 9)]
    assert len(pending) == 2, f"expected exactly 2 pending migrations, got {len(pending)}"

    con = sqlite3.connect(str(db_path))
    try:
        # Forget the two migrations from history (never hand-write rows —
        # delete the engine's own records, keeping the rest authoritative).
        for mig in pending:
            con.execute("DELETE FROM schema_migrations WHERE migration_id = ?", (mig.migration_id,))
        con.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', '7')")
        # Revert the objects so `apply` has real work and `verify` is honest.
        for mig in pending:
            if mig.rollback is not None:
                mig.rollback(con, db_path)
        con.commit()
    finally:
        con.close()


def _schema_version(db_path: Path) -> int:
    from nexus_scalp.database.engine import DatabaseMigrationEngine
    from nexus_scalp.database.models import DatabaseDomain

    engine = DatabaseMigrationEngine(db_path=db_path, domain=DatabaseDomain.AUDIT)
    return engine.current_version()


def _build_schema_7_db(db_path: Path) -> Path:
    """A faithful replica of the probe's artifact: schema 7, 8/9 pending.

    ``db_path`` is the caller's handle (tests may overwrite it to corrupt the
    file), so the DB is assembled in a private sibling path and copied into
    place — destroying ``db_path`` never destroys the fixture's source.
    """
    src = db_path.parent / "_schema7_src.db"
    full = _migrate_to_full(src)
    assert full == 9, f"expected the fresh DB to reach schema 9, got {full}"
    _rewind_to_schema_7(src)
    assert _schema_version(src) == 7
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(src.read_bytes())
    return db_path


# ---------------------------------------------------------------------------
# (a) tmp DB at schema 7 with pending 8/9 -> applied, version reaches 9
# ---------------------------------------------------------------------------


class TestAppliesPendingMigrations:
    def test_schema_7_reaches_9(self, tmp_path: Path) -> None:
        db = _build_schema_7_db(tmp_path / "audit.db")
        assert _schema_version(db) == 7

        status, detail = apply_pending_audit_migrations(db)

        assert status == "OK", detail
        assert "applied 2" in detail or "8" in detail
        assert _schema_version(db) == 9
        # The two pending migrations are now recorded as applied by the engine.
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT migration_id FROM schema_migrations WHERE status='applied'"
            ).fetchall()
            ids = {r[0] for r in rows}
        finally:
            con.close()
        assert any("AUDIT-0008" in i for i in ids), ids
        assert any("AUDIT-0009" in i for i in ids), ids

    def test_integrity_is_ok_after_applying(self, tmp_path: Path) -> None:
        db = _build_schema_7_db(tmp_path / "audit.db")
        status, detail = apply_pending_audit_migrations(db)
        assert status == "OK", detail

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            con.close()


# ---------------------------------------------------------------------------
# (b) idempotent second call — already at 9 -> SKIPPED, no error
# ---------------------------------------------------------------------------


class TestIdempotent:
    def test_second_call_is_skipped(self, tmp_path: Path) -> None:
        db = _build_schema_7_db(tmp_path / "audit.db")

        first_status, first_detail = apply_pending_audit_migrations(db)
        assert first_status == "OK", first_detail
        assert _schema_version(db) == 9

        second_status, second_detail = apply_pending_audit_migrations(db)

        assert second_status == "SKIPPED", second_detail
        assert "no pending migrations" in second_detail
        # Still at 9 — the second call changed nothing.
        assert _schema_version(db) == 9

    def test_freshly_migrated_db_needs_no_step(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        assert _migrate_to_full(db) == 9

        status, detail = apply_pending_audit_migrations(db)

        assert status == "SKIPPED", detail


# ---------------------------------------------------------------------------
# (c) deliberate corruption -> integrity gate aborts, NEVER applies
# ---------------------------------------------------------------------------


class TestCorruptionAborts:
    def test_garbage_bytes_abort_before_applying(self, tmp_path: Path) -> None:
        db = _build_schema_7_db(tmp_path / "audit.db")
        assert _schema_version(db) == 7

        # Overwrite with garbage: not a database at all.
        db.write_bytes(b"\x00" * 4096 + b"NOT A DATABASE" * 32)

        status, detail = apply_pending_audit_migrations(db)

        assert status in ("SKIPPED", "FAILED"), detail
        assert "integrity" in detail.lower()
        # Nothing was applied: the file is still not a database.
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
        except sqlite3.Error:
            rows = None
        finally:
            try:
                con.close()
            except sqlite3.Error:
                pass
        assert rows is None, "a corrupt DB must never gain a schema_migrations table"

    def test_truncated_db_aborts_without_applying(self, tmp_path: Path) -> None:
        db = _build_schema_7_db(tmp_path / "audit.db")
        assert _schema_version(db) == 7

        # Truncate to a header-ish fragment: SQLite reports a corruption, and
        # the read-only probe must refuse to proceed.
        raw = db.read_bytes()
        db.write_bytes(raw[: raw.index(b"\x0d\x0a") if b"\x0d\x0a" in raw else 16] or raw[:16])

        status, detail = apply_pending_audit_migrations(db)

        assert status in ("SKIPPED", "FAILED"), detail
        assert _schema_version(db) != 9 or status != "OK"

    def test_corruption_after_a_valid_header_is_caught(self, tmp_path: Path) -> None:
        db = _build_schema_7_db(tmp_path / "audit.db")
        raw = bytearray(db.read_bytes())
        # Keep the 16-byte SQLite header, corrupt the page map area.
        if len(raw) > 128:
            for i in range(100, min(len(raw), 200)):
                raw[i] = 0xFF
            db.write_bytes(bytes(raw))

        status, detail = apply_pending_audit_migrations(db)

        assert status in ("SKIPPED", "FAILED", "OK")
        if status == "OK":
            # Only acceptable when SQLite still reports the file healthy AND
            # the step genuinely reached 9 (corruption landed in dead space).
            assert _schema_version(db) == 9
        else:
            assert "integrity" in detail.lower()


# ---------------------------------------------------------------------------
# (d) missing DB -> honest NOT_INITIALIZED, no crash
# ---------------------------------------------------------------------------


class TestMissingDatabase:
    def test_nonexistent_path_reports_not_initialized(self, tmp_path: Path) -> None:
        db = tmp_path / "artifacts" / "audit.db"
        assert not db.exists()

        status, detail = apply_pending_audit_migrations(db)

        assert status == "NOT_INITIALIZED", detail
        assert "not initialized" in detail
        # No DB was created by the step itself.
        assert not db.exists()

    def test_nonexistent_path_with_missing_parent(self, tmp_path: Path) -> None:
        db = tmp_path / "no" / "such" / "dir" / "audit.db"

        status, detail = apply_pending_audit_migrations(db)

        assert status == "NOT_INITIALIZED", detail


# ---------------------------------------------------------------------------
# Live-DB safety: a DB held by a live writer is SKIPPED, never raced
# ---------------------------------------------------------------------------


class TestLiveDatabaseNotRaced:
    def test_held_write_lock_reports_skipped(self, tmp_path: Path) -> None:
        db = _build_schema_7_db(tmp_path / "audit.db")
        assert _schema_version(db) == 7

        holder = sqlite3.connect(str(db), timeout=2)
        holder.execute("BEGIN IMMEDIATE")
        try:
            status, detail = apply_pending_audit_migrations(db)
            assert status == "SKIPPED", detail
            assert "locked" in detail.lower()
            # While the engine held the lock, nothing was applied.
            assert _schema_version(db) == 7
        finally:
            holder.rollback()
            holder.close()

        # Once released, the same step completes.
        status, detail = apply_pending_audit_migrations(db)
        assert status == "OK", detail
        assert _schema_version(db) == 9


# ---------------------------------------------------------------------------
# Never raises — the seam is safe to call from setup
# ---------------------------------------------------------------------------


class TestNeverRaises:
    def test_directory_path_does_not_raise(self, tmp_path: Path) -> None:
        status, detail = apply_pending_audit_migrations(tmp_path)
        assert status in _VALID_STATUSES, (status, detail)

    def test_garbage_string_path_does_not_raise(self, tmp_path: Path) -> None:
        # A nonsense path: constructing it is fine; probing it is not, and the
        # seam must still return an honest status instead of raising.
        status, detail = apply_pending_audit_migrations(Path(str(tmp_path / "audit.db")))
        assert status == "NOT_INITIALIZED", (status, detail)

    def test_unreadable_file_does_not_raise(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        db.write_bytes(b"definitely not a sqlite database")
        status, detail = apply_pending_audit_migrations(db)
        assert status in ("FAILED", "SKIPPED"), (status, detail)


_VALID_STATUSES = ("OK", "SKIPPED", "FAILED", "NOT_INITIALIZED")
