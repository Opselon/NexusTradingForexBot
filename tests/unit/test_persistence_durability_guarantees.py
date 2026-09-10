"""Agent-17 P0 regression battery 2: durable durability guarantees.

PINS the hard persistence contracts that must hold across crash/restart/races:

  D1  Signal dedup identity is DATABASE-ENFORCED (UNIQUE index present on
      audit_signals.signal_dedup_key) — duplicates can never survive in
      durable state regardless of producer races.
  D2  Order/execution lifecycle events carry a DATABASE-ENFORCED idempotency
      identity (partial unique index on audit_orders execution_id) — a
      redelivered lifecycle event cannot create a second durable row
      (was: plain INSERT, duplicates accepted).
  D3  Unknown-state safety writes are refused fail-closed (no phantom durable
      state) — contract re-pin of the store's validation.
  D4  Financial overflow: a saturated queue still leaves the event on disk
      (durable overflow file) — loss remains observable, never silent.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository


@pytest.fixture()
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'dur.db'}")
    try:
        yield r
    finally:
        r.close()


def _indexes(repo: AuditRepository, table: str) -> dict[str, str]:
    with sqlite3.connect(repo._db_path) as conn:
        rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
        out = {}
        for r in rows:
            name = r[1]
            unique = r[2]
            cols = [c[2] for c in conn.execute(f"PRAGMA index_info({name})").fetchall()]
            out[name] = ("UNIQUE" if unique else "nonunique", ",".join(cols))
        return out


def test_signal_dedup_index_is_database_enforced(repo) -> None:
    idx = _indexes(repo, "audit_signals")
    matches = [v for v in idx.values() if v[0] == "UNIQUE" and "signal_dedup_key" in v[1]]
    assert matches, f"audit_signals must carry a UNIQUE dedup index, got {idx}"


def test_order_lifecycle_idempotency_index_is_database_enforced(repo) -> None:
    """D2: redelivery of one order event must not duplicate durable rows.

    Identity: the engine-issued execution_id stamped once per policy
    evaluation. Enforced by a PARTIAL UNIQUE index (events with execution_id
    = one durable row; legacy/NULL rows unaffected).
    """
    idx = _indexes(repo, "audit_orders")
    matches = [v for v in idx.values() if v[0] == "UNIQUE" and "execution_id" in v[1]]
    assert matches, f"audit_orders must carry a UNIQUE execution_id index, got {idx}"

    for _ in range(2):  # redelivery
        repo.log_order(
            ticket=1,
            order_id="req-dup",
            symbol="XAUUSD",
            action="Executed order",
            price=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            volume=0.1,
            reason="dispatch_order BUY",
            latency=0.01,
            execution_mode="STANDARD",
            execution_id="EXEC-dedup-1",
        )
    assert repo.flush(timeout_sec=15.0) is True
    with sqlite3.connect(repo._db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM audit_orders WHERE execution_id='EXEC-dedup-1'"
        ).fetchone()[0]
    assert n == 1, f"redelivered order event created {n} durable rows (must be 1)"


def test_order_event_without_execution_id_still_persists(repo) -> None:
    """Legacy producers / post-fill events (ticket != 0) keep their row."""
    repo.log_order(
        ticket=7,
        order_id="req-nodup",
        symbol="XAUUSD",
        action="Executed order",
        price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        volume=0.1,
        reason="close_position",
    )
    assert repo.flush(timeout_sec=15.0) is True
    with sqlite3.connect(repo._db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM audit_orders WHERE order_id='req-nodup'").fetchone()[
            0
        ]
    assert n == 1


def test_unknown_state_write_is_refused(repo) -> None:
    assert repo.set_runtime_risk_state(state="WARPED", reason="x") is False
    assert repo.get_runtime_risk_state() is None


def test_saturated_queue_persists_financial_event_to_durable_overflow(
    repo, tmp_path, monkeypatch
) -> None:
    """D4: when the queue cannot accept a financial event, it must land on
    disk (durable overflow file) — never silently vanish."""
    monkeypatch.setattr(repo, "_FINANCIAL_OVERFLOW_DIR", str(tmp_path / "ovf"))

    class _FullQueue:
        def qsize(self) -> int:
            return 10_000  # always 'full'

        def put(self, *a, **k):  # blocking put path
            import queue as _q

            raise _q.Full()

        def put_nowait(self, *a, **k):
            import queue as _q

            raise _q.Full()

    real_queue = repo._queue
    repo._queue = _FullQueue()  # type: ignore[assignment]
    try:
        repo._enqueue_financial("INSERT INTO audit_orders (order_id) VALUES (?)", ("ovf-1",))
    finally:
        repo._queue = real_queue

    files = list((tmp_path / "ovf").glob("overflow_*.json"))
    assert files, "financial event must be persisted to the durable overflow dir"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["query"].startswith("INSERT INTO audit_orders")
    args = json.loads(payload["args"]) if isinstance(payload["args"], str) else payload["args"]
    assert args[0] == "ovf-1"
