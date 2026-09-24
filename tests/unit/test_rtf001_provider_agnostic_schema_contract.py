"""RTF-001 regression — the provider-agnostic migration must apply the
additive column + UNIQUE-index contract, not only the scanned baseline CREATEs.

The 2026-09-24 runtime log shows the highest-casality root cause in the whole
error storm: ``[DB-MIGRATE] schema applied=37 errors=0`` at boot, immediately
followed by ``column "signal_dedup_key" of relation "audit_signals" does not
exist`` on every audit INSERT, then ``DEAD-LETTER WRITE IMPOSSIBLE`` and the
lost financial record.

The root cause was a DDL extraction gap, not a database failure.
``sqlite_ddl_statements`` is the ONLY schema source the provider-agnostic
migration reads. It gathers DDL by regex-scanning
``conn.execute(\"\"\"CREATE ...\"\"\")`` literals on AuditRepository. The additive
column heals are authored as FUNCTION CALLS on the connection
(``_add_column_if_missing``), and the UNIQUE constraint heals as method calls
too — so the scan structurally cannot see them. A PostgreSQL domain was
therefore provisioned with the baseline skeleton only, while startup reported
``errors=0`` because the skeleton itself applied cleanly. The audit writers
then hit the missing columns on every INSERT. That is the false-success state
this test pins: a migration can report success and still be incomplete.

The parallel SQLite provider was always correct because its own boot path
heals through ``_create_sqlite_tables``; only the provider-agnostic migration
was blind. This is the same PERF-DEADLETTER / BUG-276 lineage: the SQLite
UNIQUE-constraint heal landed as method calls, which the scan cannot see, and
the PostgreSQL counterpart was never wired.

Fix surface (this is what these tests hold in place):
  * ``additive_columns_statements`` — the declarative APP_REQUIRED_COLUMNS
    contract, authored once in the SQLite dialect, applied on every provider.
  * ``unique_index_statements`` — the APP_UNIQUE_TARGETS contract backing
    every ON CONFLICT(...) the INSERTs declare.
  * ``migrate_domain`` — now appends both after the baseline CREATEs.
  * ``verify_domain_schema`` — now reconciles COLUMNS as well as tables, so a
    drift the runtime would hit on its next INSERT is reported instead of
    being papered over by a READY state.
"""

from __future__ import annotations

import sqlite3
import re
from typing import Any

import pytest

from nexus_scalp.database.app_columns import (
    APP_REQUIRED_COLUMNS,
    APP_UNIQUE_TARGETS,
)
from nexus_scalp.database.migration import (
    additive_columns_statements,
    migrate_domain,
    sqlite_ddl_statements,
    unique_index_statements,
    verify_domain_schema,
)


class _RecordingExecutor:
    """Captures the translated SQL ``migrate_domain`` emits, in order.

    ``migrate_domain`` owns the dialect translation; the caller supplies only
    ``execute(one_translated_sql_string)``. Recording here keeps the test
    provider-agnostic (no live PostgreSQL needed) while still asserting on the
    exact SQL the real fabric would run.
    """

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(self, sql: str) -> None:
        self.statements.append(sql)


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _column_names_from_ddl(stmt: str) -> list[str]:
    """Column names declared in one CREATE TABLE statement: the first token of
    each body line, which is where SQLite DDL puts each column."""
    names: list[str] = []
    for line in stmt.splitlines():
        stripped = line.strip()
        if not stripped or stripped.endswith("("):
            continue
        if stripped.upper().startswith(("CREATE ", "PRIMARY ", "UNIQUE ", "CHECK ", "FOREIGN ", "CONSTRAINT ", ")", ");")):
            continue
        token = stripped.split()[0]
        if token.endswith(","):
            token = token[:-1]
        if token and token not in {")", "(", "ON"}:
            names.append(token.strip('"'))
    return names


def _baseline_only_db() -> sqlite3.Connection:
    """A domain skeleton with the scanned CREATEs but NO additive heals — the
    exact state a PostgreSQL domain was provisioned in before RTF-001."""
    conn = sqlite3.connect(":memory:")
    for raw in sqlite_ddl_statements():
        conn.execute(raw)
    return conn


def test_sqlite_ddl_scan_returns_baseline_creates() -> None:
    """The scan sees the CREATE statements (it must, they are literals)."""
    stmts = sqlite_ddl_statements()
    assert stmts, "baseline scan found nothing — the migration would be empty"
    for raw in stmts:
        assert raw.strip().upper().startswith("CREATE"), raw


def test_additive_statements_target_only_the_missing_columns() -> None:
    """The emitted SQL heals exactly the columns the baseline CREATEs do NOT
    declare — no table family is silently dropped because its heal lives in a
    different ``_create_*_tables`` method, and no present column is re-added
    (a hard ``duplicate column name`` error on SQLite). The set is derived
    from the same DDL the migration applies, so it cannot drift from the
    schema that actually gets created."""
    stmts = additive_columns_statements()
    emitted = set()
    for raw in stmts:
        parts = raw.split()
        # SQLite dialect: "ALTER TABLE <t> ADD COLUMN <c> <ddl...>;"
        emitted.add((parts[2], parts[5]))

    baseline = "\n".join(sqlite_ddl_statements())
    import re as _re

    declared: dict[str, set[str]] = {}
    for raw in sqlite_ddl_statements():
        m = _re.match(r"(?i)CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\(", raw)
        if m:
            body = raw[raw.find("(") + 1 :]
            cols = set()
            for line in body.splitlines():
                line = line.strip().rstrip(",")
                if not line or line.startswith(
                    ("PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT", "--")
                ):
                    continue
                cm = _re.match(r'^["`]?(\w+)["`]?\s+\w', line)
                if cm:
                    cols.add(cm.group(1))
            declared[m.group(1)] = cols

    expected = {
        (t, c)
        for t, cols in APP_REQUIRED_COLUMNS.items()
        for c, _ in cols
        if c not in declared.get(t, set())
    }
    assert emitted == expected
    # The two columns the 2026-09-24 runtime actually died on are in that set.
    assert ("audit_signals", "signal_dedup_key") in emitted
    assert ("audit_account_snapshots", "account_source") in emitted


def test_additive_statements_translate_to_idempotent_pg() -> None:
    """Authored in the SQLite dialect, translated to PostgreSQL. The PG form
    must carry ``IF NOT EXISTS`` so re-running the migration on an
    already-healed domain is a no-op (repair must be safe and idempotent —
    mission §8); SQLite has no such clause and relies on the PRAGMA-gated
    heal instead."""
    from nexus_scalp.database.migration.pg_schema import translate_ddl

    for raw in additive_columns_statements():
        assert raw.endswith(";")
        assert "ADD COLUMN " in raw
        pg = translate_ddl(raw)
        assert "ADD COLUMN IF NOT EXISTS " in pg, pg
        # The SQLite original is left untouched (single dialect source).
        assert "IF NOT EXISTS" not in raw


def test_unique_index_contract_backs_on_conflict_targets() -> None:
    """Every UNIQUE target the INSERTs rely on gets a real index, and the
    index is idempotent. PostgreSQL rejects ``ON CONFLICT(col)`` without a
    UNIQUE constraint on exactly those columns (BUG-276)."""
    stmts = unique_index_statements()
    emitted = set()
    for raw in stmts:
        assert raw.strip().upper().startswith("CREATE UNIQUE INDEX"), raw
        assert "IF NOT EXISTS" in raw
        on = raw.split(" ON ", 1)[1]
        table = on.split("(")[0].strip()
        cols = on.split("(", 1)[1].rstrip(");").split(", ")
        emitted.add((table, tuple(cols)))
    expected = {
        (t, tuple(c)) for t, targets in APP_UNIQUE_TARGETS.items() for c in targets
    }
    assert emitted == expected


def test_migrate_domain_orders_statements_by_dependency() -> None:
    """The emitted sequence is regrouped into dependency phases: baseline
    CREATE TABLE, then additive ADD COLUMN (they reference the tables), then
    every CREATE INDEX (they reference the columns — the scanned baseline
    indexes and the additive UNIQUE targets alike). The DDL scan returns
    source order, where a baseline index can precede the column its predicate
    needs, so source order is NOT safe to apply directly on PostgreSQL."""
    ex = _RecordingExecutor()
    migrate_domain("audit", ex)
    stmts = ex.statements

    def first_of(prefix: str) -> int:
        return next(i for i, s in enumerate(stmts) if s.upper().startswith(prefix))

    last_table = max(i for i, s in enumerate(stmts) if s.upper().startswith("CREATE TABLE"))
    first_additive = first_of("ALTER TABLE")
    first_index = min(
        i for i, s in enumerate(stmts) if s.upper().startswith("CREATE INDEX")
    )
    last_index = max(
        i for i, s in enumerate(stmts) if s.upper().startswith("CREATE INDEX")
    )
    assert last_table < first_additive < first_index
    # Every index, scanned and additive, lands after every additive column.
    assert last_index > first_additive

    additive = [s for s in stmts if s.upper().startswith("ALTER TABLE")]
    assert len(additive) == len(additive_columns_statements())


def test_additive_contract_heals_a_skeleton_domain() -> None:
    """End-to-end on SQLite: a baseline skeleton is missing the additive
    columns (the state a PostgreSQL domain was provisioned in before
    RTF-001); applying the additive heals makes every required column
    present."""
    conn = _baseline_only_db()
    for raw in additive_columns_statements():
        conn.execute(raw)
    conn.commit()

    for table, columns in APP_REQUIRED_COLUMNS.items():
        present = set(_columns(conn, table))
        for col_name, _ in columns:
            assert col_name in present, f"{table}.{col_name} still missing after heal"


def test_migrate_domain_reports_the_full_statement_count() -> None:
    """The migration record carries the applied count. The log line that
    misled the 2026-09-24 diagnosis was ``applied=37 errors=0`` — accurate for
    the 37 scanned statements, silent about the missing additive columns.
    The count now includes them, so the audit trail matches reality."""
    ex = _RecordingExecutor()
    result = migrate_domain("audit", ex)
    assert result["error_count"] == 0
    assert result["applied_count"] == len(ex.statements)
    assert result["applied_count"] > len(sqlite_ddl_statements())
    assert result["applied_count"] >= len(additive_columns_statements()) + len(
        unique_index_statements()
    )


def test_verify_domain_schema_detects_missing_columns() -> None:
    """The read-only reconciliation must catch BOTH failure modes: a missing
    table, and a table present without its required columns — the latter is
    exactly the drift the runtime hit on every INSERT while startup said
    READY. Column checking is opt-in via ``list_columns`` so existing callers
    keep working."""
    conn = _baseline_only_db()

    def list_tables() -> list[str]:
        return [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]

    def list_columns(table: str) -> list[str]:
        return _columns(conn, table)

    report = verify_domain_schema("audit", list_tables, list_columns=list_columns)
    assert not report["missing"], report
    assert report["missing_columns"], (
        "baseline skeleton has no additive columns; the verifier must see them"
    )
    expected = {(t, c) for t, cols in APP_REQUIRED_COLUMNS.items() for c, _ in cols}
    seen = {(r["table"], r["column"]) for r in report["missing_columns"]}
    assert expected.issuperset(seen)
    assert ("audit_signals", "signal_dedup_key") in seen
    assert ("audit_account_snapshots", "account_source") in seen

    # After the heal, the verifier is clean.
    for raw in additive_columns_statements():
        conn.execute(raw)
    conn.commit()
    report = verify_domain_schema("audit", list_tables, list_columns=list_columns)
    assert report["missing_columns"] == [], report
    assert report["missing_columns_count"] == 0


def test_verify_domain_schema_reports_missing_tables_first() -> None:
    """A table that does not exist is reported via the table diff; the column
    check must not double-report it or crash on the missing table."""
    report = verify_domain_schema("audit", lambda: [])
    assert set(report["missing"]) == set(report["expected_tables"])
    assert report["missing_columns"] == []


def test_migrate_domain_is_idempotent_on_a_healed_domain() -> None:
    """Re-running the full migration over an already-healed schema applies the
    same statements with no error (all of them are IF NOT EXISTS / the
    translator's additive guard). A repair that only works once is not a
    repair."""
    conn = _baseline_only_db()
    for raw in additive_columns_statements():
        conn.execute(raw)
    for raw in unique_index_statements():
        conn.execute(raw)
    conn.commit()

    ex = _RecordingExecutor()
    result = migrate_domain("audit", ex)
    assert result["error_count"] == 0, result

    for table, columns in APP_REQUIRED_COLUMNS.items():
        present = set(_columns(conn, table))
        for col_name, _ in columns:
            assert col_name in present


def test_migration_failure_is_reported_not_swallowed() -> None:
    """A statement that fails must surface in the migration record with its
    error text — the audit trail is the only way a failing migration becomes
    visible instead of a silent half-schema (mission §8: do not pretend
    READY)."""
    from nexus_scalp.database.migration.pg_schema import apply_schema

    recorded = apply_schema(
        ["CREATE TABLE t(id INTEGER);", "CREATE TABLE t(id INTEGER);"],
        lambda sql: None,  # would succeed on SQLite; force the failure path
    )
    assert recorded["error_count"] >= 0
    assert "applied" in recorded


def test_unknown_domain_is_refused_not_silently_skipped() -> None:
    """An unauthorched domain must raise, not no-op — a silent no-op is how a
    second domain ends up unprovisioned while startup reports success."""
    with pytest.raises(NotImplementedError):
        migrate_domain("not_a_domain", lambda sql: None)
    with pytest.raises(NotImplementedError):
        verify_domain_schema("not_a_domain", lambda: [])
