"""BUG-140 regression: outcome recorded immediately after the pre-trade
experience must NOT fail with NO_DECISION_SNAPSHOT.

The pre-trade experience row is queued to the AuditRepository background
writer; a fast fill / instant terminal path can call record_trade_outcome
(or ledger.record_terminal_outcome) before the worker flushed the decision
row. The read path must drain the queue once (bounded) before refusing.
"""

from __future__ import annotations

import queue
import sqlite3
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

    ts = datetime.now(UTC)
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
        deadline = time.monotonic() + 5.0
        row = ledger.get_experience_by_key("exp_req_flush_a")
        while row is None and time.monotonic() < deadline:
            time.sleep(0.05)
            row = ledger.get_experience_by_key("exp_req_flush_a")
        assert row is not None

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
            outcome_timestamp=datetime.now(UTC),
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
                outcome_timestamp=datetime.now(UTC),
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
        and flush(). Deterministic stall: freeze the worker inside its
        blocking q.get() by raising _flush_interval, then stop the loop —
        the worker sleeps in get() for an hour while an item sits in the
        queue with no consumer, so unfinished_tasks > 0 and flush() must
        time out (return False) instead of hanging.
        """
        worker = repo._worker_thread
        assert worker is not None and worker.is_alive()
        # Deterministic stall (no worker-timing dependence): the writer loop
        # captured its local ref `q = self._queue` at startup. Swap the
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
