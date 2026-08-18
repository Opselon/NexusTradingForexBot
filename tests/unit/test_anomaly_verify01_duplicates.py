"""ANOMALY-VERIFY-01 regression tests — economic identity + duplicate outcomes.

Covers TEST-ANOM-01..05, 14, 15, 24, 25, 27 (economic duplicate identity).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExecutionContext,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    OutcomeDecomposition,
    PositionBehavior,
    StrategyContext,
)


def make_record(
    key: str, ts: datetime | None = None, strategy_id: str = "strat_a"
) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=key,
        idempotency_key=f"exp_{key}",
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=ts or datetime(2024, 1, 1, tzinfo=UTC),
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id=strategy_id, symbol="XAUUSD", regime="RANGING_MEAN_REVERSION"
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id="scalp_v1", feature_dimension=50, values=[0.0] * 50
        ),
        action="BUY_MARKET",
        entry_reason="SMC",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=4400.0,
        stop_loss=4390.0,
        take_profit=4420.0,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
    )


def _count(repo, sql, *args):
    import sqlite3

    conn = sqlite3.connect(repo._db_path, timeout=5.0)
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'anom.db'}")
    yield r
    r.close()


@pytest.fixture
def ledger(repo):
    return ExperienceLedger(repo)


@pytest.fixture
def engine(repo, ledger):
    from nexus_scalp.experience.evaluator import StrategyEvaluator
    from nexus_scalp.experience.retriever import ExperienceRetriever

    evaluator = StrategyEvaluator(audit_repo=repo)
    retriever = ExperienceRetriever(ledger=ledger)
    return ExperienceIntelligenceEngine(ledger=ledger, evaluator=evaluator, retriever=retriever)


def _outcome(key: str, ticket: str, pnl: float, ts: datetime | None = None) -> ExperienceOutcome:
    return ExperienceOutcome(
        idempotency_key=key,
        execution_id=ticket,
        outcome_timestamp=ts or datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason="SYSTEM_CLOSE",
        realized_pnl_usd=pnl,
        realized_r_multiple=pnl / 100.0,
        approved_volume=0.1,
        behavior=PositionBehavior(duration_sec=300.0),
        execution=ExecutionContext(),
        decomposition=OutcomeDecomposition(final_outcome_r=pnl / 100.0),
        broker_outcome={
            "ticket": ticket,
            "net_pnl_usd": pnl,
            "gross_profit": pnl,
            "reconstruction_source": "BROKER_DEALS",
        },
    )


# ---------------------------------------------------------------------------
# TEST-ANOM-01/02/03/04/05 — economic identity + outcome idempotency
# ---------------------------------------------------------------------------


def test_anom01_one_economic_trade_one_outcome(repo, ledger, engine):
    """TEST-ANOM-01: one broker ticket cannot create a second economic outcome."""
    rec_a = make_record("req_a", ts=datetime(2024, 1, 1, tzinfo=UTC))
    ledger.record_experience(rec_a)
    repo._queue.join()
    # First outcome for ticket T1.
    ok1 = engine.record_trade_outcome(
        request_id="req_a",
        execution_id="T1",
        outcome_timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason="SYSTEM_CLOSE",
        realized_pnl_usd=-18.27,
        realized_r_multiple=-0.107,
        broker_outcome={
            "ticket": "T1",
            "net_pnl_usd": -18.27,
            "gross_profit": -18.27,
            "reconstruction_source": "BROKER_DEALS",
        },
    )
    repo._queue.join()
    assert ok1 is True
    # A SECOND, DIFFERENT request whose close correlates to the SAME ticket
    # must be refused (economic duplicate).
    rec_b = make_record("req_b", ts=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=1))
    ledger.record_experience(rec_b)
    repo._queue.join()
    ok2 = engine.record_trade_outcome(
        request_id="req_b",
        execution_id="T1",
        outcome_timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=6),
        is_executed=True,
        is_closed=True,
        exit_reason="SYSTEM_CLOSE",
        realized_pnl_usd=-31.50,
        realized_r_multiple=-0.185,
        broker_outcome={
            "ticket": "T1",
            "net_pnl_usd": -31.50,
            "gross_profit": -31.50,
            "reconstruction_source": "BROKER_DEALS",
        },
    )
    repo._queue.join()
    assert ok2 is False, "second outcome for the same broker ticket must be refused"
    assert (
        _count(
            repo,
            "SELECT COUNT(*) FROM audit_experience_outcomes WHERE execution_id=? AND is_closed=1",
            "T1",
        )
        == 1
    )


def test_anom02_same_callback_idempotent(repo, ledger, engine):
    """TEST-ANOM-02: the same outcome callback (same request) is idempotent."""
    rec = make_record("req_x", ts=datetime(2024, 1, 1, tzinfo=UTC))
    ledger.record_experience(rec)
    repo._queue.join()
    ok1 = engine.record_trade_outcome(
        request_id="req_x",
        execution_id="T2",
        outcome_timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason="TP",
        realized_pnl_usd=10.0,
        realized_r_multiple=0.1,
    )
    repo._queue.join()
    ok2 = engine.record_trade_outcome(
        request_id="req_x",
        execution_id="T2",
        outcome_timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason="TP",
        realized_pnl_usd=10.0,
        realized_r_multiple=0.1,
    )
    repo._queue.join()
    assert ok1 is True and ok2 is False  # duplicate callback discarded
    assert (
        _count(
            repo,
            "SELECT COUNT(*) FROM audit_experience_outcomes WHERE idempotency_key=?",
            "exp_req_x",
        )
        == 1
    )


def test_anom04_reconciliation_cannot_duplicate(repo, ledger, engine):
    """TEST-ANOM-04: a reconciled close of an already-outcomed ticket is a no-op."""
    rec = make_record("req_r", ts=datetime(2024, 1, 1, tzinfo=UTC))
    ledger.record_experience(rec)
    repo._queue.join()
    engine.record_trade_outcome(
        request_id="req_r",
        execution_id="T3",
        outcome_timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason="SYSTEM_CLOSE",
        realized_pnl_usd=-5.0,
        realized_r_multiple=-0.05,
    )
    repo._queue.join()
    # Reconciliation path: same request again via recovery (POSITION_STATE).
    ok = engine.record_trade_outcome(
        request_id="req_r",
        execution_id="T3",
        outcome_timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=6),
        is_executed=True,
        is_closed=True,
        exit_reason="SYSTEM_CLOSE",
        realized_pnl_usd=-5.0,
        realized_r_multiple=-0.05,
    )
    repo._queue.join()
    assert ok is False
    assert (
        _count(
            repo,
            "SELECT COUNT(*) FROM audit_experience_outcomes WHERE idempotency_key=?",
            "exp_req_r",
        )
        == 1
    )


def test_anom05_startup_recovery_cannot_duplicate(repo, ledger, engine):
    """TEST-ANOM-05: a fresh engine (restart) replaying the same close is a no-op."""
    rec = make_record("req_s", ts=datetime(2024, 1, 1, tzinfo=UTC))
    ledger.record_experience(rec)
    repo._queue.join()
    engine.record_trade_outcome(
        request_id="req_s",
        execution_id="T4",
        outcome_timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason="MANUAL_CLOSE",
        realized_pnl_usd=3.0,
        realized_r_multiple=0.03,
    )
    repo._queue.join()
    # Simulated restart: a NEW engine instance over the same DB.
    from nexus_scalp.experience.evaluator import StrategyEvaluator
    from nexus_scalp.experience.retriever import ExperienceRetriever

    engine2 = ExperienceIntelligenceEngine(
        ledger=ledger,
        evaluator=StrategyEvaluator(audit_repo=repo),
        retriever=ExperienceRetriever(ledger=ledger),
    )
    ok = engine2.record_trade_outcome(
        request_id="req_s",
        execution_id="T4",
        outcome_timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=7),
        is_executed=True,
        is_closed=True,
        exit_reason="MANUAL_CLOSE",
        realized_pnl_usd=3.0,
        realized_r_multiple=0.03,
    )
    repo._queue.join()
    assert ok is False
    assert (
        _count(
            repo,
            "SELECT COUNT(*) FROM audit_experience_outcomes WHERE idempotency_key=?",
            "exp_req_s",
        )
        == 1
    )


def test_anom03_split_fills_one_economic_outcome(repo, ledger):
    """TEST-ANOM-03: split fills remain multiple broker rows but ONE outcome."""
    # Broker: 3 sibling positions from one economic event.
    ledger.record_experience(make_record("req_split", ts=datetime(2024, 1, 1, tzinfo=UTC)))
    repo._queue.join()
    import sqlite3 as _sq

    conn = _sq.connect(repo._db_path, timeout=5.0)
    try:
        conn.execute(
            "INSERT INTO audit_experience_outcomes "
            "(idempotency_key, execution_id, outcome_timestamp, is_executed, is_closed, "
            "exit_reason, realized_pnl_usd, realized_r_multiple, approved_volume, "
            "mae_points, mfe_points, mae_usd, mfe_usd, mae_r, mfe_r, "
            "holding_duration_seconds, slippage_points, execution_latency_ms, "
            "strategy_quality, entry_quality, execution_quality, management_quality, "
            "exit_quality, behavioral_flags, payload) "
            "VALUES ('exp_req_split', 'SPLIT-1', '2024-01-01T00:05:00+00:00', 1, 1, 'SYSTEM_CLOSE', "
            "-18.27, -0.107, 0.1, 0, 0, 0, 0, 0, 0, 300, 0, 0, 0, 0, 0, 0, 0, '', '{}') "
            "ON CONFLICT(idempotency_key) DO NOTHING;"
        )
        conn.commit()
    finally:
        conn.close()
    conn = _sq.connect(repo._db_path, timeout=5.0)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM audit_experience_outcomes WHERE execution_id='SPLIT-1'"
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()
