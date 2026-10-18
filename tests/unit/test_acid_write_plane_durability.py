"""ACID durability gates for the SQLite write plane (the fabric's domain writer).

The contract under test: a financial row handed to the write plane must either
become durable or surface as a failure. The one class of bug these tests guard
is the silent middle — the plane accepts the row, reports success, and nothing
ever commits it.

ACID-06 (writer-death): the probe that found this was decisive. When the writer
thread dies, ``enqueue()`` keeps returning True for financial rows while no
consumer exists; ``committed`` and ``failed`` both stay at zero, so even a
metrics dashboard watching the failure counter sees a healthy system. The fix is
a supervised reconnect loop, and these tests pin both halves of it — the thread
stays alive across a connect failure, and a row queued during the outage is
committed once the connection returns.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver
from nexus_scalp.database.fabric.sqlite_planes import SQLiteWritePlane, WriteItem


def _plane(tmp_path: Path, *, overflow_dir: Path | None = None) -> tuple[SQLiteWritePlane, Path]:
    db = tmp_path / "domain.db"
    config = DatabaseConfig(domain="acid", sqlite_path=str(db))
    driver = SQLiteDriver(config)
    plane = SQLiteWritePlane(
        driver,
        config,
        queue_depth=5,
        batch_size=8,
        flush_interval_sec=0.02,
        overflow_dir=overflow_dir or (tmp_path / "overflow"),
    )
    return plane, db


def _financial(plane: SQLiteWritePlane, key: str) -> bool:
    """One financial write — the class the contract says must never drop silently."""
    return plane.enqueue(
        WriteItem(
            sql="INSERT INTO t (k) VALUES (?)", args=(key,), financial=True, idempotency_key=key
        ),
        timeout_sec=0.5,
    )


def test_writer_thread_survives_a_connect_failure(tmp_path: Path) -> None:
    """A connect failure retries instead of killing the worker.

    Before the fix the RuntimeError escaped ``_writer_loop``, the thread died,
    and the plane kept accepting financial rows into a queue with no consumer.
    """
    plane, _db = _plane(tmp_path)
    calls: list[int] = []

    def _boom(_path: str) -> sqlite3.Connection:
        calls.append(1)
        raise RuntimeError("simulated: parent directory vanished")

    plane._connect_factory = _boom
    plane.start()

    try:
        deadline = time.monotonic() + 3.0
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        # Three retries observed — an unsupervised loop raises once and the
        # thread is gone, so this count proves the supervisor is running.
        assert len(calls) >= 3, (
            f"only {len(calls)} connect attempt(s); the worker died instead of retrying"
        )
        # And the worker is still alive to drain when the connection returns.
        thread = plane._thread
        assert thread is not None and thread.is_alive(), (
            "the writer thread died on a connect failure: the queue has no consumer"
        )
    finally:
        plane.stop(drain_timeout_sec=1.0)


def test_queued_row_is_committed_after_the_connection_returns(tmp_path: Path) -> None:
    """A row enqueued during the outage lands when the connection comes back.

    The supervised reconnect must not just keep the thread alive — it must
    resume draining. Otherwise the retry loop converts an outage into a
    permanent stall instead of a transient one.
    """
    plane, db = _plane(tmp_path)
    attempts: list[int] = []

    def _flaky(_path: str) -> sqlite3.Connection:
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("simulated transient disk error")
        return sqlite3.connect(db, timeout=1.0)

    plane._connect_factory = _flaky

    with sqlite3.connect(db) as setup:
        setup.execute("CREATE TABLE IF NOT EXISTS t (k TEXT)")
        setup.commit()

    plane.start()
    try:
        # Enqueue while the writer is still failing to connect.
        assert _financial(plane, "K1") is True
        assert plane.flush(timeout_sec=10.0) is True
    finally:
        plane.stop(drain_timeout_sec=5.0)

    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert n == 1, f"the row queued during the outage was never committed ({n} rows)"
