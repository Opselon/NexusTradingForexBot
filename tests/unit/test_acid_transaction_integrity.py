"""ACID transaction integrity gates for the financial write paths.

Scope (the mission's acceptance contract): prove the guarantees with repeatable
tests on REAL SQLite and REAL PostgreSQL, no mocks standing in for the engine.

This file holds the *provider-agnostic* assertions plus the SQLite execution of
them. The PostgreSQL execution lives in test_acid_pg_real.py and runs only when
a real server is reachable (the repo's established PG test contract — see
test_pg_dba_migrations.py / test_audit_pg_read_plane.py).

ACID-01 (this file): a position lifecycle is one logical unit — the OPENED
placeholder and the CLOSED autopsy must not be reordered by the write plane's
batch grouping. Reordering is a real partial-write: the CLOSE upserts a row that
was never opened, then the OPEN is silently discarded by ON CONFLICT DO NOTHING,
so entry context (open_time / order_id / entry_reason / account_source) is lost
forever. Both halves are single statements that are atomic individually, so the
defect is purely inter-statement ordering — exactly what a transaction boundary
exists to protect.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.audit_write_plane import group_batch_for_commit

# The exact production statements, abridged to the columns each one owns. The
# ordering hazard depends on the *relative text order* of the two statements,
# which this preserves (the CLOSE lists exit_price before status; the OPEN lists
# status right after entry_price — that is what makes the sort disagree with the
# enqueue order). Every column the real CLOSE upserts that the OPEN sets is
# carried through so the loss is observable, not assumed.
_OPENED_SQL = """
INSERT INTO audit_ledger
(ticket, symbol, direction, volume, entry_price, status, timestamp,
 order_id, open_time, open_price, entry_reason, ai_confidence_at_open,
 market_regime_at_open, initial_sl_price, account_source)
VALUES (?, ?, ?, ?, ?, 'OPENED', ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(ticket) DO NOTHING
"""

_CLOSED_SQL = """
INSERT INTO audit_ledger
(ticket, symbol, direction, volume, entry_price, exit_price, status, pnl,
 commission, swap, duration_sec, timestamp, close_time, close_price,
 gross_pnl_usd, net_pnl_usd, order_id, entry_reason, account_source)
VALUES (?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(ticket) DO UPDATE SET
  exit_price=excluded.exit_price,
  status=excluded.status,
  pnl=excluded.pnl,
  close_time=excluded.close_time,
  close_price=excluded.close_price,
  gross_pnl_usd=excluded.gross_pnl_usd,
  net_pnl_usd=excluded.net_pnl_usd,
  order_id=CASE WHEN excluded.order_id != '' THEN excluded.order_id ELSE audit_ledger.order_id END,
  entry_reason=CASE WHEN excluded.entry_reason != '' THEN excluded.entry_reason ELSE audit_ledger.entry_reason END,
  account_source=CASE WHEN excluded.account_source != '' THEN excluded.account_source ELSE audit_ledger.account_source END
"""

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS audit_ledger (
  ticket INTEGER PRIMARY KEY,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL,
  volume REAL NOT NULL,
  entry_price REAL NOT NULL,
  exit_price REAL,
  status TEXT NOT NULL,
  pnl REAL DEFAULT 0.0,
  commission REAL DEFAULT 0.0,
  swap REAL DEFAULT 0.0,
  duration_sec REAL DEFAULT 0.0,
  timestamp TEXT NOT NULL,
  close_time TEXT,
  open_time TEXT,
  close_price REAL,
  open_price REAL,
  gross_pnl_usd REAL,
  net_pnl_usd REAL,
  order_id TEXT DEFAULT '',
  entry_reason TEXT DEFAULT '',
  ai_confidence_at_open REAL DEFAULT 0.0,
  market_regime_at_open TEXT DEFAULT '',
  initial_sl_price REAL DEFAULT 0.0,
  account_source TEXT DEFAULT ''
)
"""

_OPENED_ARGS = (
    100,
    "XAUUSD",
    "BUY",
    0.5,
    4400.0,
    "2026-10-12T09:00:00Z",
    "OID-100",
    "2026-10-12T09:00:00Z",
    4400.0,
    "BREAKOUT_STRUCT",
    0.85,
    "TREND_CONTINUATION",
    4390.0,
    "LIVE",
)
_CLOSED_ARGS = (
    100,
    "XAUUSD",
    "BUY",
    0.5,
    4400.0,
    4420.0,
    100.0,
    0.5,
    0.1,
    1800.0,
    "2026-10-12T09:30:00Z",
    "2026-10-12T09:30:00Z",
    4420.0,
    98.4,
    100.0,
    "OID-100",
    "",
    "LIVE",
)


def _fresh_ledger(tmp_path: Path) -> Path:
    """A real SQLite file carrying the production audit_ledger shape."""
    path = tmp_path / "ledger.db"
    with sqlite3.connect(path) as conn:
        conn.execute(_LEDGER_DDL)
        conn.commit()
    return path


def _row(conn: sqlite3.Connection) -> tuple:
    return conn.execute(
        "SELECT status, open_time, close_time, order_id, entry_reason, account_source "
        "FROM audit_ledger WHERE ticket = 100"
    ).fetchone()


# ---------------------------------------------------------------------------
# ACID-01: lifecycle ordering is not a write-plane optimization concern
# ---------------------------------------------------------------------------


def test_write_plane_groupby_sort_does_not_reorder_lifecycle(tmp_path: Path) -> None:
    """The batch grouping must preserve enqueue order across statements.

    Drives the REAL write plane: an OPENED and a CLOSED for the same ticket are
    enqueued into one batch and flushed. The financial consequence of reordering
    is a closed position with no entry provenance — the audit ledger is the
    accounting system of record, so that row cannot be repaired later from a
    better source.
    """
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'plane.db'}", flush_interval_sec=0.05)
    try:
        repo.log_ledger_opened(
            ticket=100,
            symbol="XAUUSD",
            direction="BUY",
            volume=0.5,
            entry_price=4400.0,
            timestamp_str="2026-10-12T09:00:00Z",
            order_id="OID-100",
            entry_reason="BREAKOUT_STRUCT",
            ai_confidence_at_open=0.85,
            market_regime_at_open="TREND_CONTINUATION",
            initial_sl_price=4390.0,
            account_source="LIVE",
        )
        repo.log_ledger_closed(
            ticket=100,
            symbol="XAUUSD",
            direction="BUY",
            volume=0.5,
            entry_price=4400.0,
            exit_price=4420.0,
            status="CLOSED",
            pnl=100.0,
            commission=0.5,
            swap=0.1,
            duration_sec=1800.0,
            timestamp_str="2026-10-12T09:30:00Z",
            order_id="OID-100",
            open_time="2026-10-12T09:00:00Z",
            close_time="2026-10-12T09:30:00Z",
            entry_reason="BREAKOUT_STRUCT",
            ai_confidence_at_open=0.85,
            market_regime_at_open="TREND_CONTINUATION",
            account_source="LIVE",
        )
        # Both rows are in one batch: flush forces the worker to commit them
        # together, which is the window the grouping runs in.
        assert repo.flush(timeout_sec=20.0) is True

        row = repo.get_ledger_row(100)
    finally:
        repo.close()

    # The contract: a closed position carries the entry context it was opened
    # with. Before the fix the sort put the CLOSE first, the upsert created the
    # row, and ON CONFLICT DO NOTHING then discarded the OPEN — losing
    # open_time / order_id / entry_reason / account_source for good.
    assert row is not None, "the ledger row for ticket 100 never landed"
    assert row["status"] == "CLOSED"
    assert row["close_time"] == "2026-10-12T09:30:00Z"
    assert row["open_time"] == "2026-10-12T09:00:00Z", (
        "lifecycle reordered: the OPEN was discarded after the CLOSE upserted the row"
    )
    assert row["order_id"] == "OID-100"
    assert row["entry_reason"] == "BREAKOUT_STRUCT"
    assert row["account_source"] == "LIVE"


def test_write_plane_grouping_passes_enqueue_order_to_the_backend(tmp_path: Path) -> None:
    """The grouped batch handed to the backend is in enqueue order.

    Deterministic at the grouping seam: both lifecycle statements are placed in
    ONE batch and the grouping (not the worker's timing) decides the order they
    reach the backend in. This is the unit the plane commits, so a reorder here
    is the partial write — it cannot be masked by batching luck.
    """
    from nexus_scalp.adapters.database.audit_write_plane import (
        SqliteAuditWriteBackend,
    )

    db = _fresh_ledger(tmp_path)
    captured: list[list] = []

    backend = SqliteAuditWriteBackend(lambda _timeout: sqlite3.connect(db))
    backend.execute_batch = lambda statements: captured.append(  # type: ignore[assignment]
        [(q, [tuple(r) for r in rows]) for q, rows in statements]
    )

    # The batch as the worker builds it, in enqueue order.
    batch = [(_OPENED_SQL, _OPENED_ARGS), (_CLOSED_SQL, _CLOSED_ARGS)]

    # The plane's production grouping step.
    from nexus_scalp.adapters.database.audit_write_plane import group_batch_for_commit

    backend.execute_batch(group_batch_for_commit(batch))

    # The statement sequence the backend received — OPEN before CLOSE.
    sequence = [q for q, _rows in captured[0]]
    assert sequence == [_OPENED_SQL, _CLOSED_SQL], (
        "the grouping reordered the lifecycle: the backend received "
        f"{[s.split(chr(10))[1].strip()[:40] for s in sequence]}"
    )

    # And the row order inside each group is unchanged from enqueue order.
    for (_q, rows), (_orig_q, orig_args) in zip(captured[0], batch, strict=True):
        assert rows == [orig_args]


def test_write_plane_grouping_collapses_adjacent_identical_statements(tmp_path: Path) -> None:
    """The collapse optimization still works after the sort is removed.

    The sort only existed to merge runs the queue had interleaved. Adjacent
    grouping keeps the common case — a producer emitting a run of identical
    statements — at one executemany call. Without this the regression would be
    pure cost: N round trips where the plane was built to use one.
    """
    import itertools

    batch = [(_OPENED_SQL, (1,)), (_OPENED_SQL, (2,)), (_CLOSED_SQL, (3,)), (_CLOSED_SQL, (4,))]
    grouped = group_batch_for_commit(batch)
    assert len(grouped) == 2, f"expected the two adjacent runs collapsed, got {len(grouped)}"
    assert grouped[0][0] is _OPENED_SQL
    assert grouped[0][1] == [(1,), (2,)]
    assert grouped[1][0] is _CLOSED_SQL
    assert grouped[1][1] == [(3,), (4,)]


def test_write_plane_grouping_does_not_merge_interleaved_statements(tmp_path: Path) -> None:
    """Interleaved statements stay interleaved — the price of not sorting.

    Documents the deliberate trade: an interleaved batch yields 4 groups instead
    of 2. Correctness (ACID-01) is bought with round trips on the rare batch,
    and every group is still applied inside the same backend transaction, so
    atomicity is unaffected.
    """
    batch = [(_OPENED_SQL, (1,)), (_CLOSED_SQL, (2,)), (_OPENED_SQL, (3,)), (_CLOSED_SQL, (4,))]
    grouped = group_batch_for_commit(batch)
    assert len(grouped) == 4
    assert [q for q, _r in grouped] == [_OPENED_SQL, _CLOSED_SQL, _OPENED_SQL, _CLOSED_SQL]


def test_lifecycle_ordering_is_preserved_when_enqueue_order_is_followed(tmp_path: Path) -> None:
    """Control case: the same statements applied in enqueue order lose nothing.

    Proves the defect is purely the sort, not the statements or the schema —
    the statements are individually correct and atomic.
    """
    db = _fresh_ledger(tmp_path)
    conn = sqlite3.connect(db)
    try:
        with conn:
            conn.execute(_OPENED_SQL, _OPENED_ARGS)
            conn.execute(_CLOSED_SQL, _CLOSED_ARGS)
        status, open_time, close_time, order_id, reason, source = _row(conn)
    finally:
        conn.close()

    assert status == "CLOSED"
    assert open_time == "2026-10-12T09:00:00Z"
    assert close_time == "2026-10-12T09:30:00Z"
    assert order_id == "OID-100"
    assert reason == "BREAKOUT_STRUCT"
    assert source == "LIVE"


# ---------------------------------------------------------------------------
# ACID-02: a failed statement in the middle of a batch leaves no partial state
# ---------------------------------------------------------------------------


def test_batch_failure_rolls_back_every_statement_in_the_batch(tmp_path: Path) -> None:
    """A mid-batch failure must not commit the statements that ran before it.

    The write plane's salvage path relies on this: it only retries rows
    individually AFTER the backend rolled the whole batch back, so a backend
    that partially committed would double-write the surviving rows.
    """
    db = _fresh_ledger(tmp_path)
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                conn.execute(_OPENED_SQL, _OPENED_ARGS)
                # NOT NULL violation on symbol — the batch must die as a unit.
                conn.execute(
                    _CLOSED_SQL.replace("?", "?", 1),
                    (
                        101,
                        None,
                        "BUY",
                        0.5,
                        4400.0,
                        4420.0,
                        100.0,
                        0.5,
                        0.1,
                        1800.0,
                        "2026-10-12T09:30:00Z",
                        "2026-10-12T09:30:00Z",
                        4420.0,
                        98.4,
                        100.0,
                        "OID-101",
                        "",
                        "LIVE",
                    ),
                )
        assert conn.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ACID-03: rollback survives being interrupted by a nested exception
# ---------------------------------------------------------------------------


def test_rollback_is_still_attempted_when_the_failure_is_not_sqlite(tmp_path: Path) -> None:
    """A non-DB error inside the transaction must still roll the writes back.

    The driver's transaction context manager (drivers/base.py _DriverTransaction)
    catches Exception, not sqlite3.Error — a broader failure (a serializer
    raising, a timeout) has to trigger the same rollback or the batch leaks.
    """
    db = _fresh_ledger(tmp_path)
    conn = sqlite3.connect(db)
    try:

        class _BoomError(Exception):
            pass

        with pytest.raises(_BoomError):
            with conn:
                conn.execute(_OPENED_SQL, _OPENED_ARGS)
                raise _BoomError("simulated mid-batch serializer failure")

        assert conn.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ACID-04: the batch boundary is a real durability boundary
# ---------------------------------------------------------------------------


def test_commit_then_reopen_durability_holds_across_connections(tmp_path: Path) -> None:
    """A committed batch is visible to a NEW connection, not just the writer's.

    SQLite WAL + synchronous=NORMAL guarantees a committed transaction survives
    a process crash, but readers on a separate connection must also observe it
    immediately — this is the read-after-write contract flush() exists to serve.
    """
    db = _fresh_ledger(tmp_path)
    with sqlite3.connect(db) as writer:
        with writer:
            writer.execute(_OPENED_SQL, _OPENED_ARGS)
            writer.execute(_CLOSED_SQL, _CLOSED_ARGS)

    with sqlite3.connect(db) as reader:
        row = _row(reader)
    assert row[0] == "CLOSED"
    assert row[1] == "2026-10-12T09:00:00Z"


# ---------------------------------------------------------------------------
# ACID-05: concurrency — two writers cannot interleave a lifecycle
# ---------------------------------------------------------------------------


def test_redelivered_lifecycle_events_converge_to_one_row(tmp_path: Path) -> None:
    """A replayed OPEN and a replayed CLOSE converge to exactly one consistent row.

    This is the idempotency contract the ledger's conflict clauses exist to
    serve: the audit producer is a background queue whose batch can be retried
    after a partial commit, and the worker replay can re-emit an OPEN/CLOSE pair
    that already landed. Every replay must be a no-op at the row level, and the
    surviving row must be CLOSED with its entry context intact.

    Written as a sequential replay (the audit plane is a single-writer thread —
    concurrency would only exercise SQLite's lock, not the engine's invariant)
    driven through the real write plane so the conflict clauses are the ones in
    production, not a restatement.
    """
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'replay.db'}", flush_interval_sec=0.05)
    try:
        # First delivery.
        repo.log_ledger_opened(
            ticket=100,
            symbol="XAUUSD",
            direction="BUY",
            volume=0.5,
            entry_price=4400.0,
            timestamp_str="2026-10-12T09:00:00Z",
            order_id="OID-100",
            entry_reason="BREAKOUT_STRUCT",
            ai_confidence_at_open=0.85,
            market_regime_at_open="TREND_CONTINUATION",
            initial_sl_price=4390.0,
            account_source="LIVE",
        )
        repo.log_ledger_closed(
            ticket=100,
            symbol="XAUUSD",
            direction="BUY",
            volume=0.5,
            entry_price=4400.0,
            exit_price=4420.0,
            status="CLOSED",
            pnl=100.0,
            commission=0.5,
            swap=0.1,
            duration_sec=1800.0,
            timestamp_str="2026-10-12T09:30:00Z",
            order_id="OID-100",
            open_time="2026-10-12T09:00:00Z",
            close_time="2026-10-12T09:30:00Z",
            entry_reason="BREAKOUT_STRUCT",
            ai_confidence_at_open=0.85,
            market_regime_at_open="TREND_CONTINUATION",
            account_source="LIVE",
        )
        # Replay: the same pair arrives again (worker retry after a partial
        # commit, or an operator re-run of the reconciliation loop).
        repo.log_ledger_opened(
            ticket=100,
            symbol="XAUUSD",
            direction="BUY",
            volume=0.5,
            entry_price=4400.0,
            timestamp_str="2026-10-12T09:00:00Z",
            order_id="OID-100",
            entry_reason="BREAKOUT_STRUCT",
            ai_confidence_at_open=0.85,
            market_regime_at_open="TREND_CONTINUATION",
            initial_sl_price=4390.0,
            account_source="LIVE",
        )
        repo.log_ledger_closed(
            ticket=100,
            symbol="XAUUSD",
            direction="BUY",
            volume=0.5,
            entry_price=4400.0,
            exit_price=4420.0,
            status="CLOSED",
            pnl=100.0,
            commission=0.5,
            swap=0.1,
            duration_sec=1800.0,
            timestamp_str="2026-10-12T09:30:00Z",
            order_id="OID-100",
            open_time="2026-10-12T09:00:00Z",
            close_time="2026-10-12T09:30:00Z",
            entry_reason="BREAKOUT_STRUCT",
            ai_confidence_at_open=0.85,
            market_regime_at_open="TREND_CONTINUATION",
            account_source="LIVE",
        )
        assert repo.flush(timeout_sec=20.0) is True

        rows = repo.get_ledger_trades(limit=10, status_filter="CLOSED")
        row = repo.get_ledger_row(100)
    finally:
        repo.close()

    assert row is not None
    assert row["status"] == "CLOSED"
    # Exactly one row: the replay neither duplicated nor split the lifecycle.
    assert len(rows) == 1, f"redelivery created {len(rows)} CLOSED rows"
    assert row["open_time"] == "2026-10-12T09:00:00Z"
    assert row["close_time"] == "2026-10-12T09:30:00Z"
    assert row["order_id"] == "OID-100"
    assert row["entry_reason"] == "BREAKOUT_STRUCT"
    assert row["account_source"] == "LIVE"
