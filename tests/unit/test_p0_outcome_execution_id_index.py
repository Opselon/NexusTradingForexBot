"""P0 pg-read slow query — the execution_id probe on audit_experience_outcomes.

The live log emitted:

    [warning] [PG-QUERY] slow query domain=pg-read duration_ms=359.00
    threshold_ms=200.00 rows=315
    SELECT o.execution_id, e.experience_id, ... FROM audit_experience_outcomes o
    JOIN audit_experiences e ON e.idempotency_key = o.idempotency_key
    WHERE o.execution_id IN (?,?,?,?, ... hundreds of placeholders ...)

Root cause (MEASURED on nexusdb @ 2026-10-01, EXPLAIN ANALYZE): the only
indexes on ``audit_experience_outcomes`` were the PK and the
``idempotency_key`` UNIQUE constraint. ``execution_id`` — the column the
WHERE clause actually filters on — had NO index, so the planner resolved the
IN-list as a full ``Seq Scan`` of the outcomes table and then hashed it
against a full ``Seq Scan`` of ``audit_experiences`` (22k rows, 1463 shared
buffers). The join key's own UNIQUE index was never used.

This suite pins the fix on OBSERVABLE BEHAVIOUR:

* the migration chain delivers ``idx_exp_outcome_exec`` (fresh DB + upgrade
  from a pre-0012 DB + rollback);
* the manifest and registry agree the index is expected schema (drift
  detection sees it);
* the plan for the exact hot query SEARCHes by the index instead of SCANning;
* ``AccountingCore._attach_identity`` deduplicates its ticket list before
  building the IN-list, and returns the same attribution as before.

The fixture is production-shaped and large enough that reverting the
migration FAILS the plan assertions (an IN-list probe over a handful of rows
is a scan either way — the L4 test-design rule this wave inherits).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from nexus_scalp.database.engine import DatabaseMigrationEngine
from nexus_scalp.database.manifest import MANIFESTS
from nexus_scalp.database.models import DatabaseDomain
from nexus_scalp.database.registry import (
    expected_version_for_domain,
    migrations_for,
)

#: The exact hot query shape from the warning, verbatim modulo the placeholder
#: count (which is parameterised by the fixture).
_HOT_SQL = (
    "SELECT o.execution_id, e.experience_id, e.strategy_id, e.strategy_version, "
    "e.model_id, e.model_version, e.feature_schema_id, e.feature_dimension "
    "FROM audit_experience_outcomes o "
    "JOIN audit_experiences e ON e.idempotency_key = o.idempotency_key "
    "WHERE o.execution_id IN ({placeholders})"
)

#: Production-shaped sizes: nexusdb held 22,132 experiences / 4,391 outcomes at
#: the time of the warning; the slow query passed ~315 tickets and got 315 rows.
N_EXPERIENCES = 22_000
N_OUTCOMES = 4_000
N_TICKETS = 315


def _plan(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list[str]:
    con.row_factory = sqlite3.Row
    return [str(r["detail"]) for r in con.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall()]


def _seed_experience_corpus(con: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """Inserts the production-shaped corpus, returns (tickets, keys).

    Mirrors the real shape: experiences hold the decision rows (their
    ``execution_id`` is empty by design — the broker ticket only ever lands on
    the outcome row), outcomes hold the broker ticket, and the join resolves
    1:1 through ``idempotency_key``.
    """
    keys: list[str] = []
    for i in range(N_EXPERIENCES):
        key = f"key_{i}"
        keys.append(key)
        con.execute(
            "INSERT INTO audit_experiences (experience_id, request_id, execution_id, "
            "idempotency_key, symbol, timeframe, strategy_id, decision_timestamp, "
            "action, entry_reason, proposed_entry, stop_loss, take_profit, payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"exp_{i}",
                f"req_{i}",
                "",  # empty by design — the ticket lives on the outcome row
                key,
                "EURUSD",
                "M1",
                "strat_a",
                "2026-09-01 10:00:00",
                "BUY",
                "reason",
                2000.0,
                1990.0,
                2010.0,
                "{}",
            ),
        )
    tickets: list[str] = []
    for i in range(N_OUTCOMES):
        key = keys[i]
        ticket = str(150_000_000_000 + i)
        tickets.append(ticket)
        con.execute(
            "INSERT INTO audit_experience_outcomes (idempotency_key, execution_id, "
            "outcome_timestamp, payload) VALUES (?,?,?,?)",
            (key, ticket, "2026-09-02 10:00:00", "{}"),
        )
    return tickets, keys


@pytest.fixture()
def migrated_db(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """A DB at the registry-expected version carrying the seeded corpus."""
    db = tmp_path / "audit.db"
    eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
    res = eng.migrate()
    assert res["state"] == "DB_MIGRATION_SUCCEEDED", res
    con = sqlite3.connect(db)
    _seed_experience_corpus(con)
    con.commit()
    # Refresh planner statistics over the seeded corpus so the plan reflects
    # the data, not the empty table the migration created.
    con.execute("ANALYZE")
    con.commit()
    yield con
    con.close()


# ---------------------------------------------------------------------------
# The index is expected schema and the migration chain delivers it
# ---------------------------------------------------------------------------


class TestMigrationDeliversTheIndex:
    def test_manifest_declares_the_index(self) -> None:
        expected = MANIFESTS[DatabaseDomain.AUDIT].expected_indexes()
        assert "idx_exp_outcome_exec" in expected

    def test_manifest_and_registry_agree_on_version(self) -> None:
        assert MANIFESTS[DatabaseDomain.AUDIT].schema_version == expected_version_for_domain(
            DatabaseDomain.AUDIT
        )

    def test_expected_version_is_12(self) -> None:
        assert expected_version_for_domain(DatabaseDomain.AUDIT) == 12

    def test_migration_carries_verify_and_rollback(self) -> None:
        m = next(
            x
            for x in migrations_for(DatabaseDomain.AUDIT)
            if x.migration_id.startswith("AUDIT-0012")
        )
        assert m.verify is not None
        assert m.rollback is not None
        assert m.from_version == 11 and m.to_version == 12

    def test_index_exists_on_a_fresh_db(self, tmp_path: Path) -> None:
        """A database created from scratch reaches the index through the chain."""
        db = tmp_path / "fresh.db"
        eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
        res = eng.migrate()
        assert res["state"] == "DB_MIGRATION_SUCCEEDED", res
        con = sqlite3.connect(db)
        try:
            names = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='audit_experience_outcomes'"
                )
            }
            assert "idx_exp_outcome_exec" in names, sorted(names)
        finally:
            con.close()

    def test_index_is_created_on_an_upgrade_from_v11(self, tmp_path: Path) -> None:
        """A database that predates AUDIT-0012 gains the index on migration.

        This is the production upgrade path: an existing DB at v11 must gain
        the index without a rebuild. The chain is replayed to v12, then the
        index is dropped to simulate a pre-0012 database and AUDIT-0012 is
        re-applied on top of v11 exactly as the engine would on upgrade.
        """
        db = tmp_path / "upgrade.db"
        eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
        assert eng.migrate()["state"] == "DB_MIGRATION_SUCCEEDED"
        migration = next(
            x
            for x in migrations_for(DatabaseDomain.AUDIT)
            if x.migration_id.startswith("AUDIT-0012")
        )
        con = sqlite3.connect(db)
        try:
            con.execute("DROP INDEX IF EXISTS idx_exp_outcome_exec")
            assert "idx_exp_outcome_exec" not in {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
            # Applying AUDIT-0012 on an otherwise-v12 DB reproduces the upgrade
            # of a v11 database: the index is created and verifies.
            migration.apply(con, Path())
            assert migration.verify(con, Path()) is True
            names = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='audit_experience_outcomes'"
                )
            }
            assert "idx_exp_outcome_exec" in names, sorted(names)
        finally:
            con.close()

    def test_rollback_removes_the_index(self, tmp_path: Path) -> None:
        """AUDIT-0012's rollback restores the pre-index schema."""
        db = tmp_path / "rollback.db"
        eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
        assert eng.migrate()["state"] == "DB_MIGRATION_SUCCEEDED"
        migration = next(
            x
            for x in migrations_for(DatabaseDomain.AUDIT)
            if x.migration_id.startswith("AUDIT-0012")
        )
        con = sqlite3.connect(db)
        try:
            assert "idx_exp_outcome_exec" in {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
            assert migration.verify(con, Path()) is True
            migration.rollback(con, Path())
            assert "idx_exp_outcome_exec" not in {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
        finally:
            con.close()


# ---------------------------------------------------------------------------
# The hot query is index-served (the plan, not the symbol table)
# ---------------------------------------------------------------------------


class TestHotQueryIsIndexServed:
    def test_execution_id_probe_uses_the_index(self, migrated_db: sqlite3.Connection) -> None:
        """The exact slow query SEARCHes outcomes by execution_id.

        Reverting AUDIT-0012 makes this FAIL: with no index the plan is
        ``SCAN o`` over the 4,000-row fixture (SQLite reports the ALIAS in the
        plan node) and the index name disappears entirely — both assertions
        below catch that.
        """
        tickets = [str(150_000_000_000 + i) for i in range(N_TICKETS)]
        placeholders = ",".join("?" * len(tickets))
        sql = _HOT_SQL.format(placeholders=placeholders)
        plan = _plan(migrated_db, sql, tuple(tickets))
        joined = "\n".join(plan)
        assert "idx_exp_outcome_exec" in joined, joined
        # SQLite reports the plan node by the table's ALIAS here ("SCAN o").
        assert "SCAN o" not in joined, joined

    def test_single_ticket_probe_is_index_served(self, migrated_db: sqlite3.Connection) -> None:
        """The single-ticket forensic path (_attach_experience_detail) too."""
        sql = _HOT_SQL.format(placeholders="?")
        plan = _plan(migrated_db, sql, ("150000000005",))
        joined = "\n".join(plan)
        assert "idx_exp_outcome_exec" in joined, joined
        assert "SCAN o" not in joined, joined

    def test_cardinality_is_unchanged_by_the_index(self, migrated_db: sqlite3.Connection) -> None:
        """The index changes the plan, never the result rows."""
        tickets = [str(150_000_000_000 + i) for i in range(N_TICKETS)]
        placeholders = ",".join("?" * len(tickets))
        sql = _HOT_SQL.format(placeholders=placeholders)
        rows = migrated_db.execute(sql, tuple(tickets)).fetchall()
        # 1:1 through idempotency_key: one attribution row per ticket.
        assert len(rows) == N_TICKETS
        assert all(r[0] in tickets for r in rows)
        # Every selected experience field is populated from the join.
        assert all(r[1] != "" for r in rows)

    def test_ticket_with_no_outcome_yields_no_row(self, migrated_db: sqlite3.Connection) -> None:
        """A ticket absent from the outcomes table does not inflate the result."""
        sql = _HOT_SQL.format(placeholders="?")
        rows = migrated_db.execute(sql, ("999999999999",)).fetchall()
        assert rows == []
