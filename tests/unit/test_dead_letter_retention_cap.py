"""Dead-letter bounded retention (PERF-DEADLETTER, 2026-09-10).

Pins the cap contract on the audit_dead_letter DIAGNOSTIC table:

* a producer failure loop (~10 rows/s measured in the incident) cannot grow
  audit.db without bound — the store keeps only the NEWEST max_rows rows;
* pruning is throttled (never per-row), batched (short WAL-safe
  transactions), and LOUD (one WARNING per overflow event, re-armed);
* financial truth is untouched: the store deletes from audit_dead_letter
  ONLY (pinned by the protected-tables probe);
* the in-memory write counter and the live COUNT stay decoupled (the cap is
  enforced from the live COUNT, so restarts cannot resurrect unbounded
  growth).
"""

from __future__ import annotations

import sqlite3

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.dead_letter_store import DeadLetterStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "retention.db")
    conn = sqlite3.connect(db_path)
    DeadLetterStore(conn_factory=sqlite3.connect, is_sqlite=True, db_path=db_path).create_table(
        conn
    )
    conn.commit()
    conn.close()
    return DeadLetterStore(
        conn_factory=sqlite3.connect,
        is_sqlite=True,
        db_path=db_path,
        max_rows=10,
        prune_batch=3,
    )


def _count(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM audit_dead_letter").fetchone()[0]
    finally:
        con.close()


def test_cap_keeps_only_newest_rows(store) -> None:
    store.PRUNE_MIN_INTERVAL_SEC = 0.0  # test-speed only: prune every record
    for i in range(25):
        assert store.record(
            query="INSERT INTO audit_orders (order_id) VALUES (?)",
            args=(f"o{i}",),
            error=ValueError(f"e{i}"),
        )
    con = sqlite3.connect(store._db_path)
    try:
        ids = [r[0] for r in con.execute("SELECT id FROM audit_dead_letter ORDER BY id")]
    finally:
        con.close()
    assert len(ids) == 10, "cap must hold regardless of producer volume"
    assert ids == list(range(16, 26)), "the NEWEST rows must be retained"
    assert store.dead_letter_pruned_rows >= 15
    assert store._overflow_warned is True, "overflow must be loud (WARNING)"


def test_prune_is_throttled_not_per_row(store) -> None:
    """With the default throttle the first pass runs, later records inside
    the window do NOT re-prune (a 10/s producer cannot prune per-row)."""
    for i in range(5):
        assert store.record(
            query="INSERT INTO audit_orders (order_id) VALUES (?)",
            args=(f"o{i}",),
            error=ValueError("x"),
        )
    first = store._last_prune
    assert first is not None, "first pass must always be due (None sentinel)"
    n_before = _count(store._db_path)
    assert n_before == 5
    assert store.record(
        query="INSERT INTO audit_orders (order_id) VALUES (?)",
        args=("o5",),
        error=ValueError("x"),
    )
    assert store._last_prune == first, "records inside the throttle window must not re-prune"


def test_retention_touches_only_the_dead_letter_table(tmp_path) -> None:
    """Cleanup safety: pruning must NEVER delete from financial/active tables."""
    db_path = str(tmp_path / "protected.db")
    conn = sqlite3.connect(db_path)
    store = DeadLetterStore(conn_factory=sqlite3.connect, is_sqlite=True, db_path=db_path)
    store.create_table(conn)
    conn.execute("CREATE TABLE audit_ledger (ticket INTEGER PRIMARY KEY, pnl REAL)")
    conn.execute("INSERT INTO audit_ledger VALUES (1, 12.5)")
    conn.commit()
    conn.close()
    store.PRUNE_MIN_INTERVAL_SEC = 0.0
    for i in range(30):
        assert store.record(
            query="INSERT INTO audit_ledger VALUES (?, ?)",
            args=(i, 1.0),
            error=ValueError("probe"),
        )
    con = sqlite3.connect(db_path)
    try:
        ledger = con.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0]
        dl = con.execute("SELECT COUNT(*) FROM audit_dead_letter").fetchone()[0]
    finally:
        con.close()
    assert ledger == 1, "financial truth must never be touched by retention"
    assert dl <= store._max_rows


def test_repo_facade_exposes_pruned_rows(tmp_path) -> None:
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'facade.db'}")
    try:
        assert repo.dead_letter_pruned_rows == 0
        assert repo.dead_letter_store.dead_letter_pruned_rows == 0
    finally:
        repo.close()


def test_retention_spares_runtime_risk_state_and_accounting(tmp_path) -> None:
    """The prune surface is audit_dead_letter ONLY — the persisted safety
    state (PERSISTED_HALTED / KILL_SWITCH) and accounting truth survive a
    full overflow cycle (LIVE/PAPER separation + fail-closed halt intact)."""
    db_path = str(tmp_path / "safety_guard.db")
    conn = sqlite3.connect(db_path)
    store = DeadLetterStore(conn_factory=sqlite3.connect, is_sqlite=True, db_path=db_path)
    store.create_table(conn)
    conn.execute(
        "CREATE TABLE runtime_risk_state ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER, state TEXT, "
        "reason TEXT DEFAULT '', source TEXT DEFAULT '', triggered_at TEXT DEFAULT '')"
    )
    conn.execute("INSERT INTO runtime_risk_state VALUES (1, 1, 'HALTED', 'dd', 'circuit', '')")
    conn.execute("CREATE TABLE audit_ledger (ticket INTEGER PRIMARY KEY, net_pnl_usd REAL)")
    conn.execute("INSERT INTO audit_ledger VALUES (9001, -12.5)")
    conn.commit()
    conn.close()
    store.PRUNE_MIN_INTERVAL_SEC = 0.0
    for i in range(40):
        assert store.record(
            query="INSERT INTO audit_ledger VALUES (?, ?)",
            args=(i, 1.0),
            error=ValueError("overflow probe"),
        )
    con = sqlite3.connect(db_path)
    try:
        halt = con.execute("SELECT state FROM runtime_risk_state WHERE id = 1").fetchone()[0]
        ledger = con.execute("SELECT net_pnl_usd FROM audit_ledger WHERE ticket = 9001").fetchone()[
            0
        ]
        dl = con.execute("SELECT COUNT(*) FROM audit_dead_letter").fetchone()[0]
    finally:
        con.close()
    assert halt == "HALTED", "persisted halt must survive dead-letter retention"
    assert ledger == pytest.approx(-12.5), "accounting truth must survive retention"
    assert dl <= store._max_rows
