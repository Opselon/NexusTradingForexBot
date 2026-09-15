"""PERF-WAVE R1 pin (2026-09-14 wave, docs/audit/wave_20260914/10_performance.md).

``AuditRepository._enqueue_financial`` runs on the TICK path (every
log_signal/log_order call). Under queue saturation it used to block for
``max(flush_interval*2, 2.0)`` — a hard 2-second floor, 20x the "worst case
costs one flush interval" claim in the class header comment (default flush
cadence is 1.0 s, and the audit file itself measured a ~19-row/s signal
flood: a 2 s stall per financial row would wedge the loop).

Fix: ``min(flush_interval*2, 0.1)`` — backpressure still exists (blocking put
+ counter + WARNING), durable overflow still wins when capacity never frees,
but no financial enqueue may ever hold the hot path longer than 100 ms.

Pins are behavior-level via a recording queue, plus a source pin so the floor
shape cannot silently return.
"""

from __future__ import annotations

import queue
import re
from pathlib import Path

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository

SRC = Path("src/nexus_scalp/adapters/database/audit_repository.py")


@pytest.fixture()
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'audit.db'}")
    yield r
    r.close()


class _RecordingQueue(queue.Queue):
    def __init__(self) -> None:
        super().__init__(maxsize=10000)
        self.blocking_timeouts: list[float] = []

    def put(self, item, block=True, timeout=None):  # type: ignore[override]
        if timeout is not None:
            self.blocking_timeouts.append(float(timeout))
            raise queue.Full  # saturation stays saturation: bounded wait fails


def test_financial_blocking_put_window_is_bounded_100ms(repo, monkeypatch) -> None:
    """R1: on the backpressure path the blocking timeout is <= 0.1 s."""
    rq = _RecordingQueue()
    repo._queue = rq  # type: ignore[assignment]
    overflow_calls: list[tuple] = []
    monkeypatch.setattr(
        repo,
        "_write_financial_overflow",
        lambda q, a, error=None: overflow_calls.append((q, a)),
    )
    original_qsize = queue.Queue.qsize

    def _full(self):  # type: ignore[no-untyped-def]
        return 9500

    queue.Queue.qsize = _full  # type: ignore[assignment]
    try:
        repo._enqueue_financial("INSERT INTO audit_orders (ticket) VALUES (?)", (1,))
    finally:
        queue.Queue.qsize = original_qsize  # type: ignore[assignment]
    assert rq.blocking_timeouts, "backpressure path did not attempt a blocking put"
    assert max(rq.blocking_timeouts) <= 0.1 + 1e-9, (
        f"R1 regression: financial enqueue blocked the tick path up to "
        f"{max(rq.blocking_timeouts)}s (cap is 0.1s)"
    )
    # Durability order preserved: bounded-wait failure still lands in the
    # overflow accounting (never a silent drop).
    assert repo.financial_events_overflowed == 1
    assert len(overflow_calls) == 1


def test_blocking_window_scales_with_flush_interval_below_the_cap(repo, monkeypatch) -> None:
    """A sub-50ms flush cadence must keep its own (smaller) window: min() not
    a constant."""
    repo._flush_interval = 0.02
    rq = _RecordingQueue()
    repo._queue = rq  # type: ignore[assignment]
    monkeypatch.setattr(repo, "_write_financial_overflow", lambda *a, **k: None)
    original_qsize = queue.Queue.qsize
    queue.Queue.qsize = lambda self: 9500  # type: ignore[assignment]
    try:
        repo._enqueue_financial("INSERT INTO audit_orders (ticket) VALUES (?)", (2,))
    finally:
        queue.Queue.qsize = original_qsize  # type: ignore[assignment]
    assert rq.blocking_timeouts == [pytest.approx(0.04)]


def test_no_hard_2s_floor_returns_to_the_enqueue_source() -> None:
    src = SRC.read_text(encoding="utf-8")
    body = src[src.index("def _enqueue_financial") :]
    body = body[: body.index("def _write_financial_overflow")]
    assert not re.search(r"max\(\s*self\._flush_interval\s*\*\s*2\.0\s*,\s*2\.0\s*\)", body), (
        "R1 regression: the 2-second hot-path blocking floor is back"
    )
    assert re.search(r"min\(\s*self\._flush_interval\s*\*\s*2\.0\s*,\s*0\.1\s*\)", body), (
        "R1 shape missing: the bounded window must be min(flush*2, 0.1)"
    )
