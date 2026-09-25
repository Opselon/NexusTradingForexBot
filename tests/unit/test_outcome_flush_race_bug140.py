"""BUG-140 regression: outcome recorded immediately after the pre-trade
experience must NOT fail with NO_DECISION_SNAPSHOT.

The pre-trade experience row is queued to the AuditRepository background
writer; a fast fill / instant terminal path can call record_trade_outcome
(or ledger.record_terminal_outcome) before the worker flushed the decision
row. The read path must drain the queue once (bounded) before refusing.
"""

from __future__ import annotations

import contextlib
import queue
import sqlite3
import threading
import time
from datetime import UTC, datetime

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.broker_history import create_history_tables
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExperienceOutcome,
    FeatureSnapshot,
    StrategyContext,
)
from tests.e2e.chain_clock import budget_cpu_ms

# ML-QA-012: ONE module-level frozen instant for every timestamp this module
# stamps. Previously three independent ``datetime.now(UTC)`` calls supplied the
# decision timestamp (``_record``) and two outcome timestamps. The wall clock
# contributed nothing to any assert here — no value is compared to ``now()`` —
# but independent reads made the test's own causality window depend on the OS
# scheduler: the outcome read could legitimately precede the decision read
# whenever the two calls straddled a clock tick, and a date-boundary flake
# could stamp the outcome in a different day from the decision. A single frozen
# instant makes the three stamps provably consistent (the causality guard in
# ledger._merge_row / record_terminal_outcome /
# ExperienceIntelligenceEngine.record_trade_outcome is ``outcome < decision``
# -> an EQUAL pair is accepted on the write path and merged on the read path;
# under independent reads that equality was a coin flip across a tick).
_FIXED_NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)


def _now() -> datetime:
    """The single timestamp supplier for this module (deterministic)."""
    return _FIXED_NOW


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'flush.db'}")
    conn = sqlite3.connect(r._db_path)
    create_history_tables(conn)
    conn.close()
    yield r
    r.close()


def _record(request_id: str) -> object:
    from nexus_scalp.experience.models import ExperienceRecord

    ts = _now()
    return ExperienceRecord(
        experience_id=f"exp_row_{request_id}",
        request_id=request_id,
        idempotency_key=f"exp_{request_id}",
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=ts,
        strategy_id="strat_flush_race",
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id="strat_flush_race",
            symbol="XAUUSD",
            session="LONDON",
            regime="TRENDING_MOMENTUM",
            volatility_regime="NORMAL",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(values=[0.0] * 50),
        action="BUY_MARKET",
        entry_reason="SMC",
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        approved_volume=0.1,
    )


class TestOutcomeFlushRace:
    def test_audit_repo_flush_drains_queue(self, repo):
        ledger = ExperienceLedger(repo)
        ledger.record_experience(_record("req_flush_a"))
        # NOT asserted here: the row may or may not be visible pre-flush — the
        # background worker flushes on its own cadence (CI run #1014: slow
        # scheduling let the worker drain between record() and this read, so
        # asserting "still queued" raced the worker). The CONTRACT under test:
        # flush() must make the row durably visible (polled below).
        assert repo.flush(timeout_sec=5.0) is True
        # flush() guarantees durability (worker committed). The ledger reads
        # through a SEPARATE WAL connection; poll briefly for the reader to
        # observe the committed tx (CI run #985: single-shot read raced the
        # WAL visibility window on a fresh db file).
        # ML-QA-012: the poll is bounded on CPU time, never the wall clock.
        # The removed shape (``deadline = time.monotonic() + 5.0`` polled in a
        # ``time.sleep(0.05)`` loop) measured how long the OS gave this test,
        # not the code: under xdist saturation on a 2-core runner a scheduler
        # stall inflates the wait with zero change in the code under test.
        # ``time.process_time()`` (the CPU clock ``budget_cpu_ms`` wraps, the
        # helper ML-QA-004/007/008/009/010/011 standardised on) is insensitive
        # to co-tenant load, and the row lands in a few ms of CPU either way.
        # The inner CPU bound is also the FAIL-FAST path: it exits the poll so
        # ``row is not None`` reports the real regression instead of hanging
        # until the CI-level test timeout.
        with budget_cpu_ms(5000.0) as sw:
            cpu_deadline = time.process_time() + 3.0
            row = ledger.get_experience_by_key("exp_req_flush_a")
            while row is None and time.process_time() < cpu_deadline:
                row = ledger.get_experience_by_key("exp_req_flush_a")
        assert row is not None
        assert sw.consumed_ms < 5000.0, "read-after-flush poll must be CPU-bounded"

    def test_outcome_immediately_after_pretrade_write_succeeds(self, repo):
        """The exact E2E failure: outcome arrives before any queue.join()."""
        ledger = ExperienceLedger(repo)
        engine = ExperienceIntelligenceEngine(
            ledger=ledger,
            evaluator=None,
            retriever=None,
            enabled=True,
        )
        ledger.record_experience(_record("req_flush_b"))
        # NO repo._queue.join() here -- the row is still queued.
        ok = engine.record_trade_outcome(
            request_id="req_flush_b",
            execution_id="99999999",
            outcome_timestamp=_now(),
            is_executed=True,
            is_closed=True,
            exit_reason="TAKE_PROFIT_HIT",
            realized_pnl_usd=200.0,
            realized_r_multiple=2.0,
            approved_volume=0.1,
            actual_entry=2000.0,
            slippage_points=0.0,
        )
        assert ok is True
        repo.flush()
        merged = ledger.get_experience_by_key("exp_req_flush_b")
        assert merged is not None
        assert merged.realized_r_multiple == 2.0

    def test_terminal_outcome_immediately_after_pretrade_write_succeeds(self, repo):
        ledger = ExperienceLedger(repo)
        ledger.record_experience(_record("req_flush_c"))
        # NO queue.join(): terminal emit for a cancel arrives instantly.
        ok = ledger.record_terminal_outcome(
            ExperienceOutcome(
                idempotency_key="exp_req_flush_c",
                execution_id="88888888",
                outcome_timestamp=_now(),
                is_executed=False,
                is_closed=True,
                exit_reason="CANCELED_UNFILLED",
                realized_pnl_usd=0.0,
                realized_r_multiple=0.0,
            )
        )
        assert ok is True
        repo.flush()
        merged = ledger.get_experience_by_key("exp_req_flush_c")
        assert merged is not None
        assert merged.exit_reason == "CANCELED_UNFILLED"
        assert merged.realized_r_multiple == 0.0  # no fabricated R

    def test_flush_is_bounded_when_worker_stalled(self, repo):
        """A stalled worker must not deadlock the live path.

        CI run #983 race: with the worker ALIVE, flush() returned True
        because the live writer drained the poisoned item between put()
        and flush(). The swap-based stall relies on a contract BUG-288
        made real: the writer captured `q = self._queue` BEFORE the
        constructor returned (the _start_background_worker capture
        handshake). Before that fix the capture raced thread scheduling +
        the sqlite connect, so on slow runners (windows-latest, run
        34925973236 at b1fe0137) the not-yet-bound worker ADOPTED the
        swapped queue and drained the poisoned item -> flush() returned
        True -> 'assert True is False'. See
        test_worker_never_adopts_a_rebound_queue_bug288 for the
        deterministic pin of that adoption window.
        """
        worker = repo._worker_thread
        assert worker is not None and worker.is_alive()
        # Worker is bound to the ORIGINAL queue (BUG-288 handshake): swap the
        # instance attribute to a FRESH queue — the worker keeps draining the
        # old object while flush() polls self._queue.unfinished_tasks on the
        # new one. An item parked in the new queue has NO consumer, so
        # flush(timeout) must time out (return False) instead of hanging.
        stalled_queue: queue.Queue[tuple[str, tuple]] = queue.Queue(maxsize=10000)
        repo._queue = stalled_queue
        repo._queue.put(("SELECT 1", ()))  # enqueued, never consumed
        result = repo.flush(timeout_sec=0.05)
        assert result is False  # returned instead of hanging
        repo._queue = queue.Queue(maxsize=10000)  # restore for teardown/close()

    def test_worker_never_adopts_a_rebound_queue_bug288(self, tmp_path, monkeypatch):
        """BUG-288: the writer must be bound to self._queue at CONSTRUCTION.

        Deterministic adoption probe: stall the worker's sqlite connect (the
        window between Thread.start() reporting aliveness and the old
        `q = self._queue` capture). Swap the instance queue and park an item
        during the stall, then wait well past the stall. If the worker can
        still adopt a rebound queue (pre-fix), it drains and CONSUMES the
        poisoned item — unfinished_tasks falls to 0 and the whole
        stall-based test family becomes a coin flip. Fixed contract: the
        item stays unconsumed forever; flush() stays bounded (False).
        """
        original_connect = AuditRepository._connect_sqlite
        release = threading.Event()

        def slow_connect(self, timeout):
            # Stall ONLY the worker thread's connect (the constructor's own
            # _setup_storage connect must stay fast).
            if threading.current_thread().name == "AuditDB_Worker":
                release.wait(timeout=5.0)  # hold the worker inside the window
            return original_connect(self, timeout)

        monkeypatch.setattr(AuditRepository, "_connect_sqlite", slow_connect)
        r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'bug288.db'}")
        worker = r._worker_thread
        assert worker is not None and worker.is_alive()

        stalled_queue: queue.Queue[tuple[str, tuple]] = queue.Queue(maxsize=10000)
        r._queue = stalled_queue
        stalled_queue.put(("SELECT 1", ()))
        assert r.flush(timeout_sec=0.05) is False  # bounded, never hangs
        release.set()  # worker now proceeds through its (slow) connect
        # Misbehavior poll: a worker that can still adopt a rebound queue
        # connects, captures the SWAPPED object, and drains + task_done()s
        # the poisoned item within milliseconds (that is what windows-latest
        # did at run 34925973236). Fixed contract: the item stays
        # unconsumed — the worker is bound to the ORIGINAL queue.
        # ML-QA-012: the poll is bounded on CPU time, never the wall clock.
        # The removed shape (``deadline = time.monotonic() + 1.0`` slept in a
        # ``time.sleep(0.02)`` loop) measured how long the OS gave this test,
        # not the contract: under xdist saturation on a 2-core runner a
        # scheduler stall can stretch any 1 s window with zero change in the
        # code under test. The invariant under test is
        # ``unfinished_tasks == 1`` (the item is NEVER consumed); the poll
        # exists only to give a misbehaving worker a window to reveal itself.
        # The inner CPU bound is also the FAIL-FAST path: it exits the poll so
        # the ``== 1`` assert reports the real regression instead of hanging
        # until the CI-level test timeout.
        with budget_cpu_ms(2000.0) as sw:
            cpu_deadline = time.process_time() + 1.0
            while stalled_queue.unfinished_tasks > 0 and time.process_time() < cpu_deadline:
                pass
        assert stalled_queue.unfinished_tasks == 1, (
            "BUG-288 adoption window is open again: the writer bound itself "
            "to a queue rebound after construction and consumed its items"
        )
        assert sw.consumed_ms < 2000.0, "misbehavior poll must be CPU-bounded"
        r._queue = queue.Queue(maxsize=10000)  # restore for teardown/close()
        with contextlib.suppress(Exception):
            r.close()

    def test_bug288_handshake_source_pins(self):
        """The capture handshake must not be silently removed (class guard)."""
        import inspect

        start_src = inspect.getsource(AuditRepository._start_background_worker)
        worker_src = inspect.getsource(AuditRepository._process_queue_worker)
        assert ".start()" in start_src and "ready.wait(" in start_src, (
            "constructor no longer waits for the worker's queue capture"
        )
        assert start_src.index(".start()") < start_src.index("ready.wait("), (
            "handshake wait must come AFTER the thread start (else deadlock)"
        )
        assert "ready.set()" in worker_src, "worker no longer publishes its capture"
        assert worker_src.index("q = self._queue") < worker_src.rindex("ready.set()"), (
            "capture must be published BEFORE the ready.set() on the sqlite path"
        )
