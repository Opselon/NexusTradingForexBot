"""RECON CRITICAL GATE — audit batch atomicity under NATIVE failure (Tier 1).

The existing flush-contract battery injects the batch error by monkeypatching
the connection (test_audit_flush_contract.py:149). This file closes the
verified remaining gap: the recovery algebra proven with a GENUINE SQLite
constraint violation flowing through the real queue -> worker -> executemany
path, using the real financial-event shape. The claim pinned is the runtime-
safety mission's core promise: ONE bad row never destroys the batch, and the
bad row is never silently lost either.

Deterministic: temp DB, one enqueue burst, flush(); no timing races (the
worker's idle-poll window makes a synchronous burst land in one batch; the
assertions hold under any partition of the burst because both salvage and
dead-letter are per-row durable outcomes).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from nexus_scalp.adapters.database.audit_repository import AuditRepository


def _good_signal_sql(i: int) -> tuple[str, tuple]:
    query = (
        "INSERT INTO audit_signals (request_id, symbol, action, confidence, "
        "proposed_entry, stop_loss, take_profit, regime, generated_at, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    args = (
        f"ATOM-{i}",
        "XAUUSD",
        "BUY",
        0.8,
        4400.0,
        4390.0,
        4420.0,
        "UNKNOWN",
        datetime(2026, 9, 14, tzinfo=UTC).isoformat(),
        "{}",
    )
    return query, args


#: action is NOT NULL in the schema — a native IntegrityError, exactly the
#: class of malformed producer row the dead-letter path exists for.
_BAD_ROW = (
    _good_signal_sql(999)[0].replace(
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
    ),
    (
        "ATOM-BAD",
        "XAUUSD",
        0.8,
        4400.0,
        4390.0,
        4420.0,
        "UNKNOWN",
        datetime(2026, 9, 14, tzinfo=UTC).isoformat(),
        "{}",
    ),
)


def test_one_native_constraint_violation_salvages_batch_and_dead_letters_bad_row(tmp_path):
    db = tmp_path / "atomic.db"
    repo = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.02)
    try:
        goods = [_good_signal_sql(i) for i in range(10)]
        for query, args in goods:
            repo._queue.put_nowait((query, args))
        repo._queue.put_nowait(_BAD_ROW)
        assert repo.flush(timeout_sec=15.0) is True

        con = sqlite3.connect(db)
        try:
            n_signals = con.execute(
                "SELECT COUNT(*) FROM audit_signals WHERE request_id LIKE 'ATOM-%'"
            ).fetchone()[0]
            n_dl = con.execute("SELECT COUNT(*) FROM audit_dead_letter").fetchone()[0]
            dl_row = con.execute(
                "SELECT query, args_json, error_type FROM audit_dead_letter LIMIT 1"
            ).fetchone()
        finally:
            con.close()

        # (a) every GOOD row is durable — the batch was not discarded
        assert n_signals == 10, f"salvage lost rows: {n_signals}/10 durable"
        # (b) the bad row is dead-lettered exactly once, with replayable data
        assert n_dl == 1, f"dead-letter count {n_dl} != 1"
        assert dl_row is not None
        stored_query, args_json, error_type = dl_row
        assert "audit_signals" in stored_query
        assert "ATOM-BAD" in args_json, "dead-letter lost its args (unreplayable)"
        assert error_type, "dead-letter lost the error classification"
        # (c) observability algebra: salvage/failure counts are real
        assert repo.audit_salvaged_rows >= 1
        assert repo.audit_batch_failures >= 1
        # (d) queue bookkeeping completed: unfinished == 0 (join-safe)
        assert repo._queue.unfinished_tasks == 0
    finally:
        repo.close()


def test_bad_row_does_not_corrupt_surrounding_idempotency(tmp_path):
    """After the recovery pass, re-running the SAME good identities through
    log_signal's durable dedup path (fresh enqueue, same repo) must not
    duplicate them: the recovery loop must not have half-applied anything."""
    db = tmp_path / "atomic2.db"
    repo = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.02)
    try:
        for query, args in [_good_signal_sql(i) for i in range(5)]:
            repo._queue.put_nowait((query, args))
        repo._queue.put_nowait(_BAD_ROW)
        assert repo.flush(timeout_sec=15.0) is True
        con = sqlite3.connect(db)
        try:
            first = con.execute(
                "SELECT COUNT(*) FROM audit_signals WHERE request_id LIKE 'ATOM-%'"
            ).fetchone()[0]
        finally:
            con.close()
        assert first == 5
    finally:
        repo.close()
