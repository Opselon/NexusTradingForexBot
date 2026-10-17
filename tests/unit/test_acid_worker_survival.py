"""ACID gate for the production audit write plane — worker survival.

Scope: `_process_queue_worker` used to call `_backend_factory()` before entering
its error-handled loop. If backend construction threw, the worker thread died,
nothing restarted it, and `enqueue_financial` kept returning `True` while both
the committed and failed counters sat at zero — a monitoring system would read
the audit pipeline as healthy while rows vanished. The ACID durability audit
recorded this shape as H-1 in `DURABILITY_AUDIT.md`.

The fabric plane had the identical defect and its fix
(`_connect_writer_supervised`) is covered by `test_acid_write_plane_durability`.
This test covers the production plane's seam, which is `_backend_factory`.

Contract: the plane is a single-writer pipeline. Rows enqueued during a backend
outage are NOT lost — they are committed once construction succeeds, without a
duplicate or a reorder.
"""

from __future__ import annotations

import queue as _stdlib_queue
import threading
import time
from pathlib import Path

from nexus_scalp.adapters.database.audit_write_plane import AuditWritePlane
from nexus_scalp.adapters.database.dead_letter_store import DeadLetterStore


class _RecordingBackend:
    """Minimal backend: fails on demand, records what it is asked to execute."""

    def __init__(self, *, fail_until: int) -> None:
        self.calls = 0
        self.fail_until = fail_until  # raise for the first N constructions
        self.executed: list[tuple[str, tuple]] = []

    # The plane touches the connection at construction time on SQLite backends;
    # this probe is provider-agnostic and does not open one.
    def touch(self) -> None:
        pass

    def execute_batch(self, statements) -> None:
        for stmt, args in statements:
            self.executed.append((stmt, tuple(args)))

    def execute_one(self, stmt: str, args: tuple) -> None:
        self.executed.append((stmt, tuple(args)))

    def close(self) -> None:
        pass


def _plane(tmp_path: Path, *, fail_until: int) -> tuple[AuditWritePlane, _RecordingBackend]:
    """Build a plane through its real constructor, factory under test control."""
    backend = _RecordingBackend(fail_until=fail_until)

    # The store's persistence is supplied by the owner; the probe only needs the
    # enqueue path to work, so records are collected in memory rather than
    # routing through a real connection.
    sink_rows: list[tuple[str, tuple]] = []

    def sink(sql: str, args) -> bool:
        sink_rows.append((sql, tuple(args)))
        return True

    store = DeadLetterStore(write_sink=sink, is_sqlite=False)

    def factory():
        backend.calls += 1
        if backend.calls <= backend.fail_until:
            raise RuntimeError("simulated: backend unavailable")
        return backend

    q: _stdlib_queue.Queue = _stdlib_queue.Queue()
    plane = AuditWritePlane(
        backend_factory=factory,
        dead_letter_store=store,
        overflow_dir=tmp_path / "overflow",
        flush_interval=0.02,
        queue=q,
    )
    return plane, backend


def _stmt(n: int) -> str:
    """A statement the arity guard accepts: one placeholder, one column."""
    return "INSERT INTO audit_orders (a) VALUES (?)"


def test_worker_survives_a_failing_backend_factory(tmp_path: Path) -> None:
    """A persistent construction failure no longer kills the worker thread."""
    plane, backend = _plane(tmp_path, fail_until=3)
    plane.start()
    try:
        deadline = time.time() + 5.0
        while backend.calls < 4 and time.time() < deadline:
            time.sleep(0.01)
        assert backend.calls >= 4, "the supervisor must keep retrying construction"

        # A row accepted while the backend is unavailable must be committed once
        # construction succeeds — not lost, not duplicated.
        plane.enqueue_financial(_stmt(7), (7,))
        deadline = time.time() + 5.0
        while not backend.executed and time.time() < deadline:
            time.sleep(0.01)
        rows = [int(r[1][0][0]) for r in backend.executed]
        assert 7 in rows, f"row accepted during the outage was lost: {rows}"
    finally:
        stopped = plane.stop()

    assert stopped, "stop() must report every enqueued row as durable"
    assert backend.calls >= 4, "construction attempts were not visible to the operator"
    rows = [int(r[1][0][0]) for r in backend.executed]
    assert rows.count(7) == 1, f"row was written more than once: {rows}"


def test_worker_exits_cleanly_when_stopped_during_an_outage(tmp_path: Path) -> None:
    """stop() while construction is failing terminates the retry loop."""
    plane, _backend = _plane(tmp_path, fail_until=10**9)
    plane.start()
    deadline = time.time() + 5.0
    while _backend.calls < 1 and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.25)  # enter the retry backoff so stop() is observed mid-loop
    plane.stop()
    worker = plane._worker_thread
    assert worker is None or not worker.is_alive(), (
        "the loop must observe stop() and exit during a construction outage"
    )
