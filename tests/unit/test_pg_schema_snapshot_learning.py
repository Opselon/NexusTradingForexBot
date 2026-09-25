"""PG-READ-PLANE-001/C — the learning-cycle schema must reach the snapshot.

The live engine logs ``[error] [LEARNING_CYCLE] recovery failed error='no
such table: learning_cycles'`` under PostgreSQL: the schema snapshot that
provisions the PG database replayed every audit-domain table except the two
``LearningCycleStore`` owns (its ``ensure_schema`` is hardcoded
``sqlite3.connect`` and is never called from the audit bootstrap). The fix
replays the owner's DDL, so these tests pin the *contract* rather than the
current statement count: the snapshot must contain the tables, their
indexes, in the idempotent spelling, and harvested from the owner itself
(no second spelling of the schema can drift in unnoticed).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from nexus_scalp.database.migration import schema_snapshot
from nexus_scalp.database.migration.pg_schema import translate_ddl
from nexus_scalp.database.migration.schema_snapshot import audit_schema_statements


def _learning(statements: tuple[str, ...]) -> list[str]:
    return [s for s in statements if "learning" in s.lower()]


def _created_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0]).lower()
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _created_indexes(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0]).lower()
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }


def test_snapshot_contains_learning_cycle_tables() -> None:
    """D4: recovery reads these tables — absent from PG, it can only fail."""
    statements = audit_schema_statements()
    tables = {s.split("\n")[0].upper() for s in statements if s.upper().startswith("CREATE TABLE")}
    for table in ("LEARNING_CYCLES", "LEARNING_CYCLE_EVENTS"):
        assert any(table in line for line in tables), (
            f"{table} missing from audit_schema_statements() — the PG snapshot "
            "provisions a database where the learning loop cannot recover"
        )


def test_snapshot_contains_learning_cycle_indexes() -> None:
    """The owner's three indexes arrive with the tables (hot path reads)."""
    indexes = {
        s.split()[5].lower()
        for s in audit_schema_statements()
        if s.upper().startswith("CREATE INDEX")
    }
    for index in (
        "idx_learning_cycles_status",
        "idx_learning_cycles_trigger",
        "idx_learning_cycle_events_cycle",
    ):
        assert index in indexes, f"{index} missing from the snapshot"


def test_learning_statements_are_idempotent() -> None:
    """Every statement re-applies on an existing schema (re-provisioning is safe)."""
    for statement in _learning(audit_schema_statements()):
        assert "IF NOT EXISTS" in statement.upper(), (
            "the provisioner re-runs the snapshot on an existing database; "
            f"a statement without IF NOT EXISTS would fail: {statement[:70]!r}"
        )


def test_learning_statements_replay_cleanly_on_sqlite() -> None:
    """The harvested statements are executable SQLite (no dialect surprises)."""
    conn = sqlite3.connect(":memory:")
    try:
        for statement in _learning(audit_schema_statements()):
            conn.execute(statement)
        # Re-applying the whole set must be a no-op, not an error.
        for statement in _learning(audit_schema_statements()):
            conn.execute(statement)
        assert {"learning_cycles", "learning_cycle_events"} <= _created_tables(conn)
    finally:
        conn.close()


def test_learning_columns_match_the_owner_contract() -> None:
    """The harvested DDL is the owner's schema, not a paraphrase of it."""
    conn = sqlite3.connect(":memory:")
    try:
        for statement in _learning(audit_schema_statements()):
            if statement.upper().startswith("CREATE TABLE"):
                conn.execute(statement)
        cycles = {
            str(row[1]).lower() for row in conn.execute('PRAGMA table_info("learning_cycles")')
        }
        # Columns the state machine writes (transition() field_updates) and
        # recovery reads: dropping any one of them breaks a live code path.
        assert {
            "cycle_id",
            "trigger",
            "trigger_identity",
            "created_at",
            "status",
            "dataset_id",
            "training_run_id",
            "candidate_model_id",
            "validation_run_id",
            "shadow_run_id",
            "decision",
            "reasons",
            "error_code",
            "retry_count",
            "payload",
        } <= cycles
        events = {
            str(row[1]).lower()
            for row in conn.execute('PRAGMA table_info("learning_cycle_events")')
        }
        assert {"id", "cycle_id", "ts", "previous_state", "new_state", "reason"} <= events
    finally:
        conn.close()


def test_ddl_is_harvested_from_the_owner_not_duplicated() -> None:
    """One spelling of the schema: the snapshot's DDL is the owner's own text.

    ``ensure_schema`` embeds the CREATE statements inline, so the replay
    harvests them from the catalog of a database the *owner* created. A
    second hand-written copy here would drift the moment the owner's
    contract changes — so the snapshot's tables, columns and indexes are
    compared against a database ``LearningCycleStore`` itself provisioned.
    """
    import os
    import tempfile
    from pathlib import Path

    from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleStore

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    owner = sqlite3.connect(path)
    try:
        LearningCycleStore(path)
        owner_tables = _created_tables(owner) & {"learning_cycles", "learning_cycle_events"}
        owner_indexes = _created_indexes(owner) & {
            "idx_learning_cycles_status",
            "idx_learning_cycles_trigger",
            "idx_learning_cycle_events_cycle",
        }
        owner_columns = {
            table: {str(row[1]).lower() for row in owner.execute(f'PRAGMA table_info("{table}")')}
            for table in owner_tables
        }
    finally:
        owner.close()
        Path(path).unlink(missing_ok=True)

    snapshot = sqlite3.connect(":memory:")
    try:
        for statement in _learning(audit_schema_statements()):
            snapshot.execute(statement)
        assert _created_tables(snapshot) & owner_tables == owner_tables
        assert _created_indexes(snapshot) & owner_indexes == owner_indexes
        for table, cols in owner_columns.items():
            snapshot_cols = {
                str(row[1]).lower() for row in snapshot.execute(f'PRAGMA table_info("{table}")')
            }
            assert snapshot_cols == cols, (
                f"the snapshot's {table} diverged from the owner's schema "
                f"(only-in-owner {sorted(cols - snapshot_cols)}, "
                f"only-in-snapshot {sorted(snapshot_cols - cols)})"
            )
    finally:
        snapshot.close()


def test_learning_statements_translate_to_postgresql() -> None:
    """The statements are SQLite dialect that ``translate_ddl`` accepts.

    No VIRTUAL TABLE (that has no PG equivalent and raises by design), and
    the translation only re-spells the types — the shape the owner wrote is
    what PostgreSQL ends up holding.
    """
    for statement in _learning(audit_schema_statements()):
        translated = translate_ddl(statement)
        assert translated, "translation must not erase a statement"
        assert "VIRTUAL" not in translated.upper()
        if "learning_cycle_events" in statement and "PRIMARY KEY" in statement:
            # AUTOINCREMENT becomes a PG identity column, the parity contract
            # the guard suite asserts for every other audit table.
            assert "GENERATED ALWAYS AS IDENTITY" in translated
            assert "AUTOINCREMENT" not in translated


def test_snapshot_is_deterministic_and_thread_safe() -> None:
    """lru_cache holds one answer; concurrent first callers see the same one."""
    first = audit_schema_statements()
    assert audit_schema_statements() is first, "the cache identity is part of the contract"
    results: list[tuple[str, ...]] = []
    errors: list[BaseException] = []

    def call() -> None:
        try:
            results.append(audit_schema_statements())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert all(r == first for r in results)


def test_replay_leaves_no_scratch_database_behind() -> None:
    """The harvest's scratch database is unlinked, never leaked to disk.

    The owner's DDL lives in a real file (its ``ensure_schema`` opens a
    ``sqlite3.connect``), so the replay creates one, reads the catalog and
    removes it. A leftover would accumulate in a container's tmp on every
    schema provision.
    """
    import tempfile

    created: list[str] = []
    orig_mkstemp = tempfile.mkstemp

    def mkstemp_spied(*args: object, **kwargs: object) -> tuple[int, str]:
        fd, path = orig_mkstemp(*args, **kwargs)
        created.append(path)
        return fd, path

    # cache_clear() forces the harvest to actually run (the snapshot is cached).
    audit_schema_statements.cache_clear()
    try:
        tempfile.mkstemp = mkstemp_spied  # type: ignore[assignment]
        statements = audit_schema_statements()
    finally:
        tempfile.mkstemp = orig_mkstemp  # type: ignore[assignment]
        audit_schema_statements.cache_clear()

    assert created, "the harvest never ran — the spy saw no scratch database"
    leftover = [p for p in created if Path(p).exists()]
    assert not leftover, f"replay leaked a scratch database: {leftover}"
    assert any("learning_cycles" in s for s in statements)


@pytest.mark.parametrize(
    "statement",
    [s for s in audit_schema_statements() if "learning" in s.lower()],
)
def test_each_learning_statement_is_plain_ddl(statement: str) -> None:
    """Every harvested statement is a CREATE TABLE/INDEX (no ALTER, no PRAGMA)."""
    head = statement.strip().upper()
    assert head.startswith(("CREATE TABLE", "CREATE INDEX")), (
        f"translate_ddl only maps CREATE statements: {statement[:70]!r}"
    )
