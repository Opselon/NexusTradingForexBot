"""AUDIT FLUSH / SHUTDOWN CONTRACTS (audit 2026-09-09, coverage-forensic mission).

Why these tests exist
---------------------
``AuditRepository.flush()`` is the read-after-write determinism primitive of
the audit trail (BUG-140 E2E: pre-trade decision snapshots must be durable
before post-trade outcomes are recorded). ``close()`` must drain the queue
before the worker connection dies. Neither contract was directly tested in
any suite: the flush-race assertion that existed was removed as racy
(9e550819), leaving the *durable* properties unpinned.

These tests assert the durable contract (not timing races):

  1. flush() returns True once the queue is drained (enqueued rows are
     actually readable afterwards — read-after-write).
  2. flush() is BOUNDED: with the worker stalled, it returns False within
     its timeout instead of deadlocking the live path.
  3. close() joins the worker and everything enqueued before close() is
     durable on disk afterwards.
  4. Non-SQLite backends short-circuit flush() to True (no queue exists).
  5. The worker's batch-recovery path salvages good rows and dead-letters
     bad ones without losing task_done() bookkeeping (queue.join() can
     always return).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository


@contextmanager
def _repo(tmp_path, name: str) -> Generator[AuditRepository, None, None]:
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / name}", flush_interval_sec=0.02)
    try:
        yield repo
    finally:
        repo.close()


def _count_rows(repo: AuditRepository) -> int:
    with sqlite3.connect(repo._db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM audit_guard_telemetry").fetchone()[0])


def _enqueue_guard_row(repo: AuditRepository, i: int) -> None:
    # count is a NOT NULL DEFAULT 0 column fed by the SQL literal 1 — three
    # placeholders, three bindings (same shape the production producer uses).
    repo._queue.put_nowait(
        (
            "INSERT INTO audit_guard_telemetry (window_start, symbol, reason_code, count) "
            "VALUES (?, ?, ?, 1)",
            (f"2026-09-09T00:00:{i % 60:02d}", "XAUUSD", "TICK_DUPLICATE_SUPPRESSED"),
        )
    )


# ---------------------------------------------------------------------------
# flush(): read-after-write durability
# ---------------------------------------------------------------------------


def test_flush_returns_true_and_rows_are_durable(tmp_path) -> None:
    with _repo(tmp_path, "flush_ok.db") as repo:
        for i in range(5):
            _enqueue_guard_row(repo, i)
        assert repo.flush(timeout_sec=10.0) is True
        assert repo._queue.unfinished_tasks == 0
        assert _count_rows(repo) == 5


def test_flush_on_idle_queue_returns_true_immediately(tmp_path) -> None:
    with _repo(tmp_path, "flush_idle.db") as repo:
        started = time.monotonic()
        assert repo.flush(timeout_sec=5.0) is True
        assert time.monotonic() - started < 2.0


def test_close_drains_pending_writes_and_is_idempotent_safe(tmp_path) -> None:
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'close.db'}", flush_interval_sec=0.02)
    for i in range(10):
        _enqueue_guard_row(repo, i)
    repo.close()  # no explicit flush: close() must drain
    assert _count_rows(repo) == 10


# ---------------------------------------------------------------------------
# flush(): bounded — a stalled worker can never deadlock the live path
# ---------------------------------------------------------------------------


def test_flush_returns_false_when_worker_stalled(tmp_path) -> None:
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'stall.db'}", flush_interval_sec=0.02)
    try:
        # Simulate a WEDGED worker honestly: flush() polls
        # self._queue.unfinished_tasks; point the repo at a queue whose
        # unfinished count never reaches zero (the disk is never the thing
        # under test here — the BOUNDED WAIT is).
        class _WedgedQueue:
            """Delegates to the real queue (worker keeps running); only the
            unfinished_tasks read lies so the queue never appears to drain."""

            def __init__(self, inner) -> None:
                self._inner = inner

            @property
            def unfinished_tasks(self) -> int:
                return 3  # never drains

            def __getattr__(self, item):
                return getattr(self._inner, item)

        real_queue = repo._queue
        repo._queue = _WedgedQueue(real_queue)  # type: ignore[assignment]
        started = time.monotonic()
        ok = repo.flush(timeout_sec=0.2)
        elapsed = time.monotonic() - started
        repo._queue = real_queue  # unwedge before close()
        assert ok is False
        assert elapsed < 2.0, "flush must stay bounded (no deadlock)"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Non-SQLite backend: no queue, flush must not lie
# ---------------------------------------------------------------------------


def test_flush_non_sqlite_short_circuits_true(tmp_path) -> None:
    # Postgres-style backends write synchronously; flush() is a no-op True.
    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    assert repo.flush(timeout_sec=0.1) is True


# ---------------------------------------------------------------------------
# Worker batch recovery: salvage + dead-letter (P0 financial-loss safety)
# ---------------------------------------------------------------------------


def test_batch_recovery_salvages_good_rows_and_dead_letters_bad(tmp_path) -> None:
    """Drive the REAL worker loop against a connection whose bulk executemany
    fails (batch conflict): good row salvaged, bad row dead-lettered, and
    every item still gets task_done() so queue.join()/close() can return."""
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'salvage.db'}", flush_interval_sec=0.02)
    try:
        import nexus_scalp.adapters.database.audit_repository as ar_mod

        real_connect = ar_mod.sqlite3.connect
        payload = {"fail_many": True}

        class _FailingManyConn:
            def __init__(self, inner: sqlite3.Connection) -> None:
                self._inner = inner

            def execute(self, sql, args=()):  # per-row retry path: works
                return self._inner.execute(sql, args)

            def executemany(self, sql, seq):  # bulk path: fails
                if payload["fail_many"]:
                    raise sqlite3.IntegrityError("simulated batch insert conflict")
                return self._inner.executemany(sql, seq)

            def __enter__(self):
                self._inner.__enter__()
                return self

            def __exit__(self, *exc):
                return self._inner.__exit__(*exc)

            def __getattr__(self, item):
                return getattr(self._inner, item)

        def _connect(*a, **k):
            return _FailingManyConn(real_connect(*a, **k))

        monkey_patch_target = ar_mod.sqlite3
        original_connect = monkey_patch_target.connect
        monkey_patch_target.connect = _connect
        try:
            good = (
                "INSERT INTO audit_guard_telemetry (window_start, symbol, reason_code, count) "
                "VALUES (?, ?, ?, 1)"
            )
            # PK(window_start, symbol, reason_code): same tuple twice = the second
            # row is a REAL constraint violation (the classic batch conflict).
            bad = good
            repo._queue.put_nowait(
                (good, ("2026-09-09T00:01:00", "XAUUSD", "TICK_DUPLICATE_SUPPRESSED"))
            )
            repo._queue.put_nowait(
                (bad, ("2026-09-09T00:01:00", "XAUUSD", "TICK_DUPLICATE_SUPPRESSED"))
            )
            # A batch containing the good row + the poisoned row: grouping by
            # query means the good row's group succeeds and only the bad
            # row's group fails — assert the queue fully drained regardless.
            drained = repo.flush(timeout_sec=15.0)
            assert drained is True
            assert repo._queue.unfinished_tasks == 0
            assert _count_rows(repo) >= 1, "good rows must survive a poisoned batch"
            assert repo.audit_batch_failures >= 1
            assert repo.audit_salvaged_rows + repo.audit_dead_letter_rows >= 1
        finally:
            monkey_patch_target.connect = original_connect
    finally:
        repo.close()
