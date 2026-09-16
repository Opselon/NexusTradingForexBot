"""RECON CRITICAL GATE — DeadLetterStore durable contracts (wave-2 restoration).

Restores the store-behavior half of the deleted test_dead_letter_store_split.py
after the wave-2 adversarial audit found those contracts had no surviving
coverage (orphans by name-overlap check, scratch/recon/verify_orphans.py):
record/list round-trip, per-record counter + monotonic sequence algebra,
unserializable-arg envelope (row NEVER lost), hard truncation bounds
(query<=8000 / error_message<=2000 / payload_note<=1000 — oversized input is
stored truncated, never rejected), non-SQLite fail-counted semantics, verbatim
schema + create_table idempotency, and cross-restart durability through the
facade (the CR-004/006 durability family for the dead-letter table itself).

The 6 repo-delegation mirror tests from the deleted file (attribute reads
delegate, shared accessor identity, json-shim parity) are INTENTIONALLY not
restored: they assert the A4 refactor's wiring shape, not observable behavior
(brief §12 delegation-mirror rule) — the same behavior is asserted through the
facade by test_recon_batch_atomicity_real_failure (dead-letter rows written
via the recovery path) and test_runtime_safety_mission (unserializable arg).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.dead_letter_store import DeadLetterStore


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'dl_contracts.db'}")
    yield r
    r.close()


@pytest.fixture
def store(tmp_path):
    """A bare DeadLetterStore over the same connection accessor pattern the
    repository uses (sqlite3.connect on one shared tmp path)."""
    db_path = str(tmp_path / "store_direct.db")
    conn = sqlite3.connect(db_path)
    DeadLetterStore(conn_factory=sqlite3.connect, is_sqlite=True, db_path=db_path).create_table(
        conn
    )
    conn.commit()
    conn.close()
    return DeadLetterStore(conn_factory=sqlite3.connect, is_sqlite=True, db_path=db_path)


def test_store_record_list_roundtrip(store) -> None:
    ok = store.record(
        query="INSERT INTO audit_orders (order_id) VALUES (?)",
        args=("ord-1",),
        error=ValueError("bad row"),
        payload_note="unit probe",
    )
    assert ok is True
    rows = store.list_recent()
    assert len(rows) == 1
    row = rows[0]
    assert "audit_orders" in row["query"]
    assert json.loads(row["args_json"]) == ["ord-1"]
    assert row["error_type"] == "ValueError"
    assert row["error_message"] == "bad row"
    assert row["payload_note"] == "unit probe"
    assert row["failed_at"]
    # newest-first ordering with multiple rows
    store.record(
        query="INSERT INTO audit_orders (order_id) VALUES (?)",
        args=("ord-2",),
        error=KeyError("worse"),
    )
    rows = store.list_recent()
    assert [r["error_type"] for r in rows] == ["KeyError", "ValueError"]


def test_store_counter_increments_per_record(store) -> None:
    assert store.audit_dead_letter_rows == 0
    for i in range(3):
        assert (
            store.record(
                query="INSERT INTO audit_orders (order_id) VALUES (?)",
                args=(f"o{i}",),
                error=ValueError(f"e{i}"),
            )
            is True
        )
        assert store.audit_dead_letter_rows == i + 1
    # sequence numbers are persisted monotonically 1..3
    seqs = sorted(r["sequence_no"] for r in store.list_recent(limit=10))
    assert seqs == [1, 2, 3]


def test_store_corrupt_payload_unserializable_arg_is_recoverable(store) -> None:
    """A value json.dumps cannot encode becomes the __unserializable__
    envelope — the row is NEVER lost and never raises."""

    class Opaque:
        def __repr__(self) -> str:  # pragma: no cover
            return "<opaque>"

    assert (
        store.record(
            query="INSERT INTO audit_orders VALUES (?, ?)",
            args=(1, Opaque()),
            error=ValueError("bad row"),
        )
        is True
    )
    rows = store.list_recent()
    assert len(rows) == 1
    assert "__unserializable__" in rows[0]["args_json"]
    envelope = json.loads(rows[0]["args_json"])[1]
    assert envelope["__unserializable__"] is True
    assert envelope["type"] == "Opaque"
    assert envelope["repr"] == "<opaque>"


def test_store_oversized_payload_is_truncated_not_rejected(store) -> None:
    """Hard bounds: query <=8000 chars, error_message <=2000, payload_note
    <=1000 — oversized input is durably stored TRUNCATED, never dropped,
    never raised."""
    assert (
        store.record(
            query="INSERT INTO audit_orders VALUES (?) " + "x" * 12000,
            args=("a",),
            error=ValueError("E" * 5000),
            payload_note="N" * 4000,
        )
        is True
    )
    rows = store.list_recent()
    assert len(rows) == 1
    assert len(rows[0]["query"]) == 8000
    assert len(rows[0]["error_message"]) == 2000
    assert len(rows[0]["payload_note"]) == 1000
    assert store.audit_dead_letter_rows == 1


def test_store_non_sqlite_counts_loss_and_returns_false(tmp_path) -> None:
    """Non-SQLite handle: no durable write is possible, so the row counts as
    lost (counter increments, record returns False, list is empty) — the
    fail-counted contract; silent loss is what this forbids."""
    store = DeadLetterStore(
        conn_factory=sqlite3.connect, is_sqlite=False, db_path=str(tmp_path / "nope.db")
    )
    assert store.record(query="q", args=(), error=ValueError("x")) is False
    assert store.audit_dead_letter_rows == 1
    assert store.list_recent() == []


def test_store_create_table_is_idempotent_verbatim_schema(store, tmp_path) -> None:
    """create_table on an already-migrated DB is a no-op (IF NOT EXISTS):
    second application must not raise and must not change the shape."""
    db_path = str(tmp_path / "store_direct.db")
    conn = sqlite3.connect(db_path)
    store.create_table(conn)  # second application on the same DB
    conn.commit()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_dead_letter)").fetchall()]
    conn.close()
    assert cols == [
        "id",
        "failed_at",
        "table_name",
        "query",
        "args_json",
        "error_type",
        "error_message",
        "retry_count",
        "sequence_no",
        "payload_note",
    ]


def test_dead_letter_survives_simulated_restart(tmp_path) -> None:
    """Durability contract across instances (CR-004/006 family for the
    dead-letter table): record via facade #1, read via facade #2 (fresh
    repository over the same file). A dead-lettered row is the last evidence
    of a lost financial event — losing IT on restart erases the incident."""
    db = tmp_path / "restart.db"
    r1 = AuditRepository(db_url=f"sqlite:///{db}")
    try:
        assert r1.record_dead_letter(
            query="INSERT INTO audit_orders (order_id) VALUES (?)",
            args=("persist",),
            error=ValueError("v1"),
        )
    finally:
        r1.close()
    r2 = AuditRepository(db_url=f"sqlite:///{db}")
    try:
        rows = r2.get_dead_letter_rows()
        assert len(rows) == 1
        assert "persist" in rows[0]["args_json"]
        assert rows[0]["error_type"] == "ValueError"
    finally:
        r2.close()


# ---------------------------------------------------------------------------
# Facade contracts that the wave-2 name-overlap audit found WITHOUT a surviving
# home (second pass, after the store-behavior restoration). These are not
# delegation mirrors: both assert observable, incident-bearing behavior of the
# PUBLIC AuditRepository surface that no other battery exercises.
# ---------------------------------------------------------------------------


def test_repo_schema_created_at_setup_zero_migration(repo) -> None:
    """The audit_dead_letter table must exist right after repository
    construction (DDL applied on the repo's setup connection) with the
    pre-split column contract — no schema change to the incident ledger.

    Distinct from test_store_create_table_is_idempotent_verbatim_schema, which
    pins create_table() on a bare store: this pins that a *constructed
    repository* already carries the table, i.e. the DDL is wired into setup and
    not deferred to first write (a deferred DDL would drop the first
    dead-letter of a fresh install — the incident nobody would ever see)."""
    with sqlite3.connect(repo._db_path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_dead_letter)").fetchall()]
    assert cols == [
        "id",
        "failed_at",
        "table_name",
        "query",
        "args_json",
        "error_type",
        "error_message",
        "retry_count",
        "sequence_no",
        "payload_note",
    ]


def test_repo_batch_recovery_counter_contract_still_holds(repo) -> None:
    """The counter the batch-recovery worker path reads must be the store-owned
    one: after N dead-letters through the PUBLIC facade (the same call site the
    batch-recovery block uses), repo.audit_dead_letter_rows agrees with the
    store it composes, and the rows carry the batch-recovery payload note.

    A split-brain counter here means the recovery worker believes rows were
    salvaged that were never recorded — the exact failure the dead-letter
    surface exists to make impossible."""
    for i in range(2):
        repo.record_dead_letter(
            query="INSERT INTO audit_signals (request_id) VALUES (?)",
            args=(f"req-{i}",),
            error=sqlite3.IntegrityError("constraint"),
            retry_count=1,
            payload_note="audit worker batch-retry failure",
        )
    assert repo.audit_dead_letter_rows == 2
    assert repo.audit_dead_letter_rows == repo.dead_letter_store.audit_dead_letter_rows
    dl = repo.get_dead_letter_rows()
    assert len(dl) == 2
    assert all("audit worker batch-retry failure" == r["payload_note"] for r in dl)
