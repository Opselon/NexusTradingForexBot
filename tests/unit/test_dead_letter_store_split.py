"""A4 dead-letter store split — DeadLetterStore ownership + facade delegation.

Proves the audit A4 refactor is behavior-preserving:

1. DeadLetterStore owns the audit_dead_letter surface directly:
   record + list roundtrip, counter increments, corrupt/oversized payload
   handling (mirroring the former record_dead_letter semantics verbatim),
   sequence bookkeeping, and non-SQLite (fail-counted) behavior.
2. AuditRepository composes the store and delegates: the public
   record_dead_letter / get_dead_letter_rows signatures are unchanged, the
   audit_dead_letter_rows / _dead_letter_seq attribute reads delegate to the
   store, the schema is created at repository-setup time (same table, zero
   migration), and the store shares the repository's connection accessor
   (sqlite3.connect over the same _db_path — never a second handle).
3. Cross-restart: rows written through the facade are read back from a NEW
   repository instance over the same file (durability contract).

xdist-safe: every test constructs its own repository on an isolated
tmp_path database (same fixture style as test_runtime_safety_mission.py).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.dead_letter_store import DeadLetterStore


@pytest.fixture
def repo(tmp_path):
    """Isolated AuditRepository on a tmp file DB (mirrors the
    test_runtime_safety_mission.repo fixture style)."""
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'dl_split.db'}")
    yield r
    r.close()


@pytest.fixture
def store(tmp_path):
    """A bare DeadLetterStore over the SAME connection accessor the
    repository uses (sqlite3.connect on one shared tmp path)."""
    db_path = str(tmp_path / "store_direct.db")
    # Schema comes from the store's own DDL (the verbatim table contract).
    conn = sqlite3.connect(db_path)
    DeadLetterStore(conn_factory=sqlite3.connect, is_sqlite=True, db_path=db_path).create_table(
        conn
    )
    conn.commit()
    conn.close()
    return DeadLetterStore(conn_factory=sqlite3.connect, is_sqlite=True, db_path=db_path)


# =====================================================================
# 1. DeadLetterStore directly
# =====================================================================


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
    """Mirrors test_runtime_safety_mission.test_dead_letter_survives_unserializable_arg:
    a value json.dumps cannot encode becomes the __unserializable__ envelope —
    the row is NEVER lost and never raises."""

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
    """Mirrors the former record_dead_letter bounds: query ≤8000 chars,
    error_message ≤2000 chars, payload_note ≤1000 chars — oversized input is
    durably stored TRUNCATED, never dropped, never raised."""
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
    assert len(rows[0]["query"]) == 8000  # str[:8000] hard bound (verbatim move)
    assert len(rows[0]["error_message"]) == 2000
    assert len(rows[0]["payload_note"]) == 1000
    assert store.audit_dead_letter_rows == 1


def test_store_non_sqlite_counts_loss_and_returns_false(tmp_path) -> None:
    """Non-SQLite handle: no durable write is possible, so the row counts as
    lost (counter increments, record returns False, list is empty) — exactly
    the pre-split fail-counted contract."""
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


# =====================================================================
# 2. AuditRepository delegation (public surface byte-compatible)
# =====================================================================


def test_repo_record_and_list_delegate_to_store(repo) -> None:
    assert (
        repo.record_dead_letter(
            query="INSERT INTO audit_orders (order_id) VALUES (?)",
            args=("ord-9",),
            error=RuntimeError("boom"),
            retry_count=1,
            payload_note="delegation probe",
        )
        is True
    )
    # counter read on the FACADE reflects the STORE-owned value
    assert repo.audit_dead_letter_rows == 1
    assert repo.dead_letter_store.audit_dead_letter_rows == 1
    rows = repo.get_dead_letter_rows()
    assert len(rows) == 1
    assert rows[0]["error_type"] == "RuntimeError"
    assert rows[0]["payload_note"] == "delegation probe"
    assert rows[0]["retry_count"] == 1
    # listing goes through the store
    assert repo.get_dead_letter_rows() == repo.dead_letter_store.list_recent(limit=200)


def test_repo_counter_attribute_read_and_write_delegate(repo) -> None:
    """debug_snapshot/tests read `audit.audit_dead_letter_rows`; the batch
    recovery path increments it. Both directions must hit the store."""
    repo.audit_dead_letter_rows += 4
    assert repo.dead_letter_store.audit_dead_letter_rows == 4
    repo.dead_letter_store.audit_dead_letter_rows += 1
    assert repo.audit_dead_letter_rows == 5
    # sequence counter delegates too (overflow-file naming reads it)
    repo._dead_letter_seq = 7
    assert repo.dead_letter_store._dead_letter_seq == 7
    repo._dead_letter_seq += 1
    assert repo.dead_letter_store._dead_letter_seq == 8


def test_repo_schema_created_at_setup_zero_migration(repo) -> None:
    """The audit_dead_letter table must exist right after repository
    construction (DDL applied on the repo's setup connection) with the
    pre-split column contract — no schema change."""
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


def test_repo_store_shares_connection_accessor_not_second_handle(repo) -> None:
    """Constructor injection: the store must borrow the repository's own
    connection accessor (sqlite3.connect over the SAME _db_path) — no second
    connection factory, no second database path."""
    assert isinstance(repo.dead_letter_store, DeadLetterStore)
    assert repo.dead_letter_store._db_path == repo._db_path
    assert repo.dead_letter_store._is_sqlite == repo._is_sqlite
    assert repo.dead_letter_store._conn_factory is sqlite3.connect
    # a row written via the store IS visible through a plain connection to
    # the same file (proves same-database, not a parallel handle elsewhere)
    assert repo.record_dead_letter(
        query="INSERT INTO audit_orders (order_id) VALUES (?)",
        args=("shared",),
        error=ValueError("v"),
    )
    with sqlite3.connect(repo._db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM audit_dead_letter").fetchone()[0]
    assert n == 1


def test_repo_dead_letter_survives_simulated_restart(tmp_path) -> None:
    """Durability contract across instances: record via facade #1, read via
    facade #2 (fresh repository over the same file)."""
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


def test_repo_batch_recovery_counter_contract_still_holds(repo, monkeypatch) -> None:
    """The facade counter the worker recovery path increments must be the
    store-owned counter: after N dead-letters through the public API (the
    same call site the batch-recovery block uses), repo.audit_dead_letter_rows
    and the store agree."""
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


def test_repo_json_safe_args_shim_matches_store() -> None:
    """The repository keeps its static _json_safe_args entry point (used by
    the durable-overflow writer); it must behave exactly like the store's
    canonical implementation."""

    class Opaque:
        def __repr__(self) -> str:  # pragma: no cover
            return "<opaque>"

    args = (1, "ok", Opaque(), {"k": [1, 2]})
    assert AuditRepository._json_safe_args(args) == DeadLetterStore._json_safe_args(args)
    decoded = json.loads(AuditRepository._json_safe_args(args))
    assert decoded[0] == 1 and decoded[1] == "ok" and decoded[3] == {"k": [1, 2]}
    assert decoded[2]["__unserializable__"] is True
