"""BUG-140 regression: outcome recorded immediately after the pre-trade
experience must NOT fail with NO_DECISION_SNAPSHOT.

The pre-trade experience row is queued to the AuditRepository background
writer; a fast fill / instant terminal path can call record_trade_outcome
(or ledger.record_terminal_outcome) before the worker flushed the decision
row. The read path must drain the queue once (bounded) before refusing.
"""

from __future__ import annotations

import sqlite3
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
        assert ledger.get_experience_by_key("exp_req_flush_a") is None  # still queued
        assert repo.flush(timeout_sec=5.0) is True
        assert ledger.get_experience_by_key("exp_req_flush_a") is not None

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
        """A stalled worker must not deadlock the live path."""
        repo._running = False  # simulate a dead worker loop
        repo._queue.put(("SELECT 1", ()))  # item that will never be task_done'd
        result = repo.flush(timeout_sec=0.05)
        assert result is False  # returned instead of hanging
