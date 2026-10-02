"""Wave db-lifecycle-2 lane L4 — R-7 / R-6 evidence-reader index + R-8 duplicate.

Pins the two indexes the Phase-2 remediation matrix justified with a full
gate (PHASE2-RANKED-REMEDIATION-MATRIX.md §2), delivered as AUDIT-0010 /
AUDIT-0011 through the migration registry, and the duplicate-index drop.

The test-design rule this wave imposes: a fixture small enough for SQLite to
serve from a handful of pages gives a false green (an IN-list probe over 3
rows is a scan either way), so the fixtures here are PRODUCTION-SHAPED and
LARGE ENOUGH THAT REVERTING THE MIGRATION FAILS THE TEST:

* ``audit_signals`` carries ~20,000 rows with UNIQUE request_ids (the live
  ledger: 11,266 rows / 11,266 distinct) plus the ~1% gate-rejection second
  rows the matrix measured (182/20,182 ≈ 0.9%; production 92/9,108).
* ``audit_orders`` carries the near-unique order_id shape (644 distinct /
  2,576 rows, matching the measured 566/2,379).

Every assertion is on OBSERVABLE BEHAVIOUR — the EXPLAIN QUERY PLAN node
(``SCAN <table>`` vs ``SEARCH <table> USING INDEX <name>``) and the resolved
verdict — never on the existence of a symbol.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.database.engine import DatabaseMigrationEngine
from nexus_scalp.database.manifest import MANIFESTS
from nexus_scalp.database.models import DatabaseDomain
from nexus_scalp.database.registry import expected_version_for_domain

#: The exact statement experience/decision_evidence.py:128-135 emits (R-7).
R7_SQL = """SELECT id, decision_stage FROM audit_signals
   WHERE request_id = ?
     AND decision_stage IN
         ('EXPERIENCE_INTELLIGENCE_GATE', 'TRADE_INTELLIGENCE_GATE')
   ORDER BY id LIMIT 1"""

#: The exact statement decision_evidence.py:152-155 emits (R-6 reader 1).
R6A_SQL = """SELECT id, ticket FROM audit_orders
   WHERE order_id = ? AND ticket != 0 ORDER BY id LIMIT 1"""

#: The statement experience/outcome_recovery_sweep.py:218 emits (R-6 reader 2).
R6B_SQL = "SELECT ticket FROM audit_orders WHERE order_id = ? AND ticket != 0 ORDER BY id LIMIT 3"

#: The statement web/operator_routes.py:622 emits (R-6 reader 3).
R6C_SQL = (
    "SELECT id, ticket, order_id, symbol, action FROM audit_orders "
    "WHERE order_id = ? ORDER BY id DESC LIMIT 20"
)

#: The statement incidents/trace_lineage.py:102 emits (R-6 reader 4).
R6D_SQL = "SELECT * FROM audit_orders WHERE ticket=? OR order_id=? ORDER BY timestamp DESC LIMIT 10"

N_SIGNALS = 20_000
N_GATE_EXTRA = 182  # ~0.9% second rows, production ratio (92 / 9,108)
N_ORDERS = 2_576
N_ORDER_IDS = 644


def _plan(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list[str]:
    return [str(r[3]) for r in con.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall()]


def _seed_signals(con: sqlite3.Connection) -> None:
    """Production-shaped audit_signals: UNIQUE request_ids + the gate rows.

    The migration engine's baseline already created the table (the real
    column contract, healed from APP_REQUIRED_COLUMNS) — never shadow it.
    """
    cols = {r[1] for r in con.execute("PRAGMA table_info(audit_signals)")}
    assert "request_id" in cols, "baseline skeleton missing request_id"
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_signals_dedup "
        "ON audit_signals(signal_dedup_key)"
    )
    con.executemany(
        "INSERT INTO audit_signals (request_id,symbol,action,confidence,proposed_entry,"
        "stop_loss,take_profit,regime,generated_at,payload,execution_mode,reason_code,"
        "decision_stage,blocked_by,signal_dedup_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (
                f"REQ-{i:07d}",
                "XAUUSD",
                "NO_TRADE",
                0.3,
                3300.0,
                3290.0,
                3318.0,
                "RANGING_MEAN_REVERSION",
                "2026-09-01T00:00:00+00:00",
                "{}",
                "STANDARD",
                "INSUFFICIENT_CONFIDENCE",
                "CONFIDENCE_GATE",
                "CONFIDENCE_FAIL",
                f"sig_{i:07d}",
            )
            for i in range(N_SIGNALS)
        ),
    )
    # The gate-rejection second rows: one extra row for ~0.9% of requests, so
    # the probe's ORDER BY id LIMIT 1 has a real match to find past rowid 0.
    con.executemany(
        "INSERT INTO audit_signals (request_id,symbol,action,confidence,proposed_entry,"
        "stop_loss,take_profit,regime,generated_at,payload,execution_mode,reason_code,"
        "decision_stage,blocked_by,signal_dedup_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (
                f"REQ-{i:07d}",
                "XAUUSD",
                "NO_TRADE",
                0.2,
                3300.0,
                3290.0,
                3318.0,
                "RANGING_MEAN_REVERSION",
                "2026-09-01T00:00:00+00:00",
                "{}",
                "STANDARD",
                "EXPERIENCE_GATE",
                "EXPERIENCE_INTELLIGENCE_GATE",
                "EXPERIENCE_DEGRADED",
                f"sig_gate_{i:07d}",
            )
            for i in range(0, N_SIGNALS, N_SIGNALS // N_GATE_EXTRA)
        ),
    )


def _seed_orders(con: sqlite3.Connection) -> None:
    """Production-shaped audit_orders: near-unique order_id (4 rows each)."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(audit_orders)")}
    assert "order_id" in cols, "baseline skeleton missing order_id"
    con.execute("CREATE INDEX IF NOT EXISTS idx_orders_ticket ON audit_orders(ticket, order_id)")
    con.executemany(
        "INSERT INTO audit_orders (ticket,order_id,symbol,action,price,stop_loss,"
        "take_profit,volume,reason,latency,execution_mode,execution_id,timestamp) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (
                700_000 + k,
                f"REQ-{oid:07d}",
                "XAUUSD",
                "BUY_LIMIT",
                3300.0,
                3290.0,
                3318.0,
                0.01,
                "MODEL_SIGNAL",
                1.0,
                "STANDARD",
                f"EXEC-{oid:06d}-{k}",
                "2026-09-01T00:00:00+00:00",
            )
            for oid in range(N_SIGNALS - N_ORDER_IDS, N_SIGNALS)
            for k in range(N_ORDERS // N_ORDER_IDS)
        ),
    )


@pytest.fixture()
def migrated_db(tmp_path: Path) -> sqlite3.Connection:
    """A DB at the registry-expected version, carrying the seeded corpus."""
    db = tmp_path / "audit.db"
    eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
    res = eng.migrate()
    assert res["state"] == "DB_MIGRATION_SUCCEEDED", res
    con = sqlite3.connect(db)
    _seed_signals(con)
    _seed_orders(con)
    con.commit()
    # Refresh planner statistics over the seeded corpus so the plan reflects
    # the data, not the empty table the migration created.
    con.execute("ANALYZE")
    con.commit()
    yield con
    con.close()


# ---------------------------------------------------------------------------
# R-7: the request_id probe is index-served
# ---------------------------------------------------------------------------


class TestR7RequestIdIndex:
    def test_gate_probe_is_index_served(self, migrated_db: sqlite3.Connection) -> None:
        """The exact R-7 statement must SEARCH by the request_id index.

        Reverting AUDIT-0010 makes this FAIL: the plan degrades to
        ``SCAN audit_signals`` over the 20,182-row fixture.
        """
        plan = _plan(migrated_db, R7_SQL, ("REQ-0000100",))
        assert any("idx_audit_signals_request_id" in p for p in plan), plan
        assert not any("SCAN audit_signals" in p for p in plan), plan

    def test_gate_probe_finds_the_rejection_row(self, migrated_db: sqlite3.Connection) -> None:
        """Correctness under the index: the probe still returns the verdict."""
        row = migrated_db.execute(R7_SQL, ("REQ-0000109",)).fetchone()
        assert row is not None
        assert row[1] == "EXPERIENCE_INTELLIGENCE_GATE"

    def test_gate_probe_reports_no_evidence_for_ungated_request(
        self, migrated_db: sqlite3.Connection
    ) -> None:
        row = migrated_db.execute(R7_SQL, ("REQ-0000007",)).fetchone()
        assert row is None  # NO_EVIDENCE — the resolver's honest verdict

    def test_n_plus_one_shape_is_bounded_by_the_index(
        self, migrated_db: sqlite3.Connection
    ) -> None:
        """The N+1 loop body (2,000 probes) stays index-served throughout.

        The probes hit requests with NO gate row (the dominant N+1 case: the
        sweep classifies most orphans NO_EVIDENCE) — the shape that seq-scanned
        8,791 rows per iteration in production.
        """
        probe_ids = [f"REQ-{i:07d}" for i in range(2_000)]
        for rid in probe_ids:
            plan = _plan(migrated_db, R7_SQL, (rid,))
            assert not any("SCAN audit_signals" in p for p in plan), (rid, plan)


# ---------------------------------------------------------------------------
# R-6: the order_id probe is index-served
# ---------------------------------------------------------------------------


class TestR6OrderIdIndex:
    @pytest.mark.parametrize(
        ("label", "sql", "args_factory"),
        [
            ("decision_evidence", R6A_SQL, lambda rid: (rid,)),
            ("recovery_sweep", R6B_SQL, lambda rid: (rid,)),
            ("operator_detail", R6C_SQL, lambda rid: (rid,)),
            (
                "trace_lineage",
                R6D_SQL,
                lambda rid: (700_000, rid),  # ticket=0-style OR probe
            ),
        ],
    )
    def test_order_id_reader_is_index_served(
        self,
        migrated_db: sqlite3.Connection,
        label: str,
        sql: str,
        args_factory,
    ) -> None:
        """All four order_id-led reader shapes must be index-served.

        Reverting AUDIT-0010 makes this FAIL for every shape: the plan
        degrades to ``SCAN audit_orders`` (measured on the live artifact).
        """
        rid = f"REQ-{N_SIGNALS - 1:07d}"  # an order_id that exists
        plan = _plan(migrated_db, sql, args_factory(rid))
        assert not any("SCAN audit_orders" in p for p in plan), (label, plan)

    def test_order_probe_returns_the_dispatch_rows(self, migrated_db: sqlite3.Connection) -> None:
        rid = f"REQ-{N_SIGNALS - 1:07d}"
        rows = migrated_db.execute(R6B_SQL, (rid,)).fetchall()
        assert 0 < len(rows) <= 3
        assert all(r[0] != 0 for r in rows)


# ---------------------------------------------------------------------------
# R-8 (L4's pair): the duplicate idx_release_metadata_key is gone
# ---------------------------------------------------------------------------


class TestR8DuplicateReleaseMetadataKey:
    def test_duplicate_index_is_absent_after_migration(
        self, migrated_db: sqlite3.Connection
    ) -> None:
        names = {
            r[0] for r in migrated_db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "idx_release_metadata_key" not in names

    def test_pk_autoindex_still_serves_the_lookup(self, migrated_db: sqlite3.Connection) -> None:
        """Dropping the duplicate must not lose the lookup path."""
        migrated_db.execute("INSERT INTO release_metadata (key, value) VALUES ('probe', 'v1')")
        migrated_db.commit()
        plan = _plan(migrated_db, "SELECT value FROM release_metadata WHERE key = ?", ("probe",))
        assert any("sqlite_autoindex_release_metadata_1" in p for p in plan), plan
        assert (
            migrated_db.execute(
                "SELECT value FROM release_metadata WHERE key = ?", ("probe",)
            ).fetchone()[0]
            == "v1"
        )


# ---------------------------------------------------------------------------
# Registry / manifest SSOT agreement (BUG-194 pin, extended to v11, then v12)
# ---------------------------------------------------------------------------


class TestSchemaVersionAgreement:
    def test_manifest_matches_registry_expected_version(self) -> None:
        assert MANIFESTS[DatabaseDomain.AUDIT].schema_version == expected_version_for_domain(
            DatabaseDomain.AUDIT
        )

    def test_expected_version_is_12(self) -> None:
        assert expected_version_for_domain(DatabaseDomain.AUDIT) == 12

    def test_manifest_declares_the_new_indexes(self) -> None:
        expected = MANIFESTS[DatabaseDomain.AUDIT].expected_indexes()
        assert "idx_audit_signals_request_id" in expected
        assert "idx_orders_order_id" in expected

    def test_registry_migrations_carry_verify_and_rollback(self) -> None:
        from nexus_scalp.database.registry import migrations_for

        for m in migrations_for(DatabaseDomain.AUDIT):
            if m.migration_id.startswith(("AUDIT-0010", "AUDIT-0011")):
                assert m.verify is not None, m.migration_id
                assert m.rollback is not None, m.migration_id
