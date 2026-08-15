"""
Phase 08 Integration — Experience Gate <-> Execution Boundary
============================================================
End-to-end verification that the Experience Intelligence layer participates in
the REAL decision path (policy -> experience gate -> risk sizing -> dispatch)
while remaining structurally incapable of executing anything itself.

No broker connection is required: `PaperMT5Adapter` provides a deterministic
in-memory execution boundary, and the dispatch call itself is observed through a
counting spy so the test proves *whether an order was attempted*, not merely
what an object looked like.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExperienceAction,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
    StrategyLifecycle,
)
from nexus_scalp.experience.provenance import ModelRegistry
from nexus_scalp.experience.quality import compute_behavior_metrics
from nexus_scalp.experience.retriever import ExperienceRetriever
from nexus_scalp.features.scalp_features import BarData, ScalpFeatureEngine
from nexus_scalp.risk.risk_engine import RiskEngine


@pytest.fixture
def wired(tmp_path):
    """Fully wired experience subsystem + paper execution boundary."""
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'exp_integration.db'}")
    ledger = ExperienceLedger(audit_repo=repo)
    evaluator = StrategyEvaluator(audit_repo=repo)
    retriever = ExperienceRetriever(ledger=ledger)
    engine = ExperienceIntelligenceEngine(
        ledger=ledger,
        evaluator=evaluator,
        retriever=retriever,
        enabled=True,
        max_inline_refresh_per_sec=0.0,
        score_cache_ttl_sec=1.0,
    )
    adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    adapter.connect()
    risk = RiskEngine(config=RiskConfig(), max_allowed_lots=RiskConfig().max_allowed_lots)
    om = OrderLifecycleManager(
        adapter=adapter,
        audit_repo=repo,
        risk_engine=risk,
        experience_engine=engine,
    )
    yield repo, ledger, evaluator, retriever, engine, adapter, risk, om
    repo.close()


@pytest.fixture
def feature_vector():
    now = datetime.now(UTC)
    bars = [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=now - timedelta(minutes=80 - i),
            open=2000.0,
            high=2004.0,
            low=1996.0,
            close=2001.0,
            tick_volume=120.0,
            is_complete=True,
        )
        for i in range(60)
    ]
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=2000.0, ask=2000.20)
    return ScalpFeatureEngine().compute_from_bars(bars, current_tick=tick)


def _proposal(request_id: str, confidence: float = 0.85) -> TradeProposal:
    return TradeProposal(
        request_id=request_id,
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY_MARKET,
        confidence=confidence,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        reason_code="PURE_AI",
    )


def _seed_losing_history(repo, ledger, context: StrategyContext, count: int = 20) -> None:
    """Seeds a statistically significant losing history for a context family."""
    base = datetime.now(UTC) - timedelta(hours=6)
    for i in range(count):
        key = f"seed_{context.strategy_id}_{i}"
        ts = base + timedelta(minutes=i)
        ledger.record_experience(
            ExperienceRecord(
                experience_id=f"exp_{i}",
                request_id=f"req_seed_{i}",
                idempotency_key=key,
                symbol="XAUUSD",
                decision_timestamp=ts,
                strategy_id=context.strategy_id,
                context=context,
                feature_snapshot=FeatureSnapshot(values=[0.1] * 50),
                action="BUY_MARKET",
                entry_reason="PURE_AI",
                model_probability=0.8,
                signal_confidence=0.8,
                proposed_entry=2000.0,
                stop_loss=1990.0,
                take_profit=2020.0,
                risk_reward_ratio=2.0,
            )
        )
        ledger.record_outcome(
            ExperienceOutcome(
                idempotency_key=key,
                execution_id=f"tk_{i}",
                outcome_timestamp=ts + timedelta(minutes=2),
                is_executed=True,
                is_closed=True,
                exit_reason="HARD_SL_HIT",
                realized_pnl_usd=-120.0,
                realized_r_multiple=-1.2,
                behavior=compute_behavior_metrics(
                    mae_points=-10.5,
                    mfe_points=1.0,
                    mae_usd=-105.0,
                    mfe_usd=10.0,
                    planned_risk_distance=10.0,
                    duration_sec=280.0,
                    initial_sl_distance=10.0,
                    atr_at_entry=4.0,
                ),
            )
        )
    repo._queue.join()


def test_harmful_strategy_is_rejected_before_any_order_is_attempted(wired, feature_vector):
    """
    A statistically harmful strategy family must be rejected by the gate, and the
    dispatch path must never be entered for that proposal.
    """
    repo, ledger, _, _, engine, adapter, risk, om = wired
    ctx = engine.build_proposal_context(_proposal("req_ctx"), feature_vector)
    _seed_losing_history(repo, ledger, ctx, count=20)
    engine.invalidate_score_cache()

    dispatch_calls: list[str] = []
    original_dispatch = om.dispatch_order

    def spy(decision, volume):
        dispatch_calls.append(getattr(decision.action, "value", str(decision.action)))
        return original_dispatch(decision, volume)

    om.dispatch_order = spy

    proposal = _proposal("req_live_reject")
    gated, decision = engine.evaluate_proposal(proposal, feature_vector)

    assert decision.strategy_lifecycle == StrategyLifecycle.RETIRED
    assert decision.action == ExperienceAction.REJECT
    assert decision.qualifies_trade is False
    assert gated.action == ActionType.NO_TRADE

    # Mirror the LiveEngine dispatch guard: NO_TRADE never reaches dispatch.
    entry_actions = {
        ActionType.BUY,
        ActionType.SELL,
        ActionType.BUY_MARKET,
        ActionType.SELL_MARKET,
        ActionType.BUY_LIMIT,
        ActionType.SELL_LIMIT,
        ActionType.BUY_STOP,
        ActionType.SELL_STOP,
    }
    if gated.action in entry_actions:
        om.dispatch_order(gated, 0.10)

    assert dispatch_calls == []
    assert adapter.get_positions("XAUUSD") == []


def test_healthy_context_reaches_dispatch_and_risk_engine_still_governs(wired, feature_vector):
    """
    With no adverse evidence the gate must not interfere: the proposal reaches
    dispatch and RiskEngine remains the authority on size.
    """
    _, _, _, _, engine, adapter, risk, om = wired

    proposal = _proposal("req_live_allow")
    gated, decision = engine.evaluate_proposal(proposal, feature_vector)

    assert decision.action == ExperienceAction.INSUFFICIENT_EVIDENCE
    assert gated.action == ActionType.BUY_MARKET
    assert gated.confidence == pytest.approx(0.85)

    account = adapter.get_account_info()
    symbol_info = adapter.get_symbol_info("XAUUSD")
    volume = risk.calculate_volume(
        entry=gated.proposed_entry,
        sl=gated.stop_loss,
        tp=gated.take_profit,
        account=account,
        symbol_info=symbol_info,
    )
    volume = risk.get_clamped_position_size(volume=volume, account=account, symbol_info=symbol_info)
    assert volume > 0.0
    assert volume <= 10.0

    assert om.dispatch_order(gated, volume) is True
    assert len(adapter.get_positions("XAUUSD")) == 1


def test_experience_tables_persist_across_repository_restart(tmp_path):
    """
    Experience memory must survive process restart independently of any model
    artifact, and the schema census must remain intact.
    """
    db_url = f"sqlite:///{tmp_path / 'restart.db'}"
    repo = AuditRepository(db_url=db_url)
    ledger = ExperienceLedger(audit_repo=repo)
    ctx = StrategyContext(strategy_id="strat_restart")
    ledger.record_experience(
        ExperienceRecord(
            experience_id="exp_restart",
            request_id="req_restart",
            idempotency_key="k_restart",
            symbol="XAUUSD",
            decision_timestamp=datetime.now(UTC),
            strategy_id="strat_restart",
            context=ctx,
            feature_snapshot=FeatureSnapshot(values=[0.3] * 50),
            action="BUY_MARKET",
            entry_reason="PURE_AI",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
        )
    )
    repo._queue.join()
    repo.close()

    # Fresh repository instance over the same file: schema creation must be
    # idempotent and existing rows must be preserved.
    repo2 = AuditRepository(db_url=db_url)
    try:
        ledger2 = ExperienceLedger(audit_repo=repo2)
        rows = ledger2.get_experiences_for_strategy("strat_restart")
        assert len(rows) == 1
        assert rows[0].feature_dimension == 50
        assert ledger2.get_schema_distribution()["scalp_v1/50D"] == 1
    finally:
        repo2.close()


def test_all_experience_tables_and_indexes_exist(tmp_path):
    """The Phase 08 schema (tables + retrieval indexes) must be created."""
    db_file = tmp_path / "schema.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    try:
        conn = sqlite3.connect(repo._db_path)
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
            }
            indexes = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index';")
            }
        finally:
            conn.close()
    finally:
        repo.close()

    for table in (
        "audit_experiences",
        "audit_experience_outcomes",
        "audit_experience_corrections",
        "strategy_intelligence_registry",
        "experience_model_registry",
    ):
        assert table in tables, f"missing table {table}"

    for index in (
        "idx_exp_strategy_time",
        "idx_exp_symbol_time",
        "idx_exp_outcome_key",
    ):
        assert index in indexes, f"missing index {index}"


def test_experience_rest_endpoints_expose_real_state(tmp_path):
    """
    The Phase 08 REST surface must report ACTUAL persisted state, and the
    self-heal endpoint must rebuild derived intelligence without touching raw
    experience rows.
    """
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'api.db'}")
    try:
        ledger = ExperienceLedger(audit_repo=repo)
        evaluator = StrategyEvaluator(audit_repo=repo)
        retriever = ExperienceRetriever(ledger=ledger)
        engine = ExperienceIntelligenceEngine(
            ledger=ledger,
            evaluator=evaluator,
            retriever=retriever,
            enabled=True,
            max_inline_refresh_per_sec=0.0,
        )
        ctx = StrategyContext(strategy_id="strat_api", regime="TRENDING_MOMENTUM")
        _seed_losing_history(repo, ledger, ctx, count=18)
        evaluator.evaluate_strategy(
            "strat_api", ledger.get_experiences_for_strategy("strat_api", limit=100)
        )
        repo._queue.join()

        artifact = tmp_path / "model.pt"
        artifact.write_bytes(b"WEIGHTS")
        registry = ModelRegistry(audit_repo=repo)
        registry.register_model(artifact_path=artifact, model_version="v1.0")
        repo._queue.join()

        engine_ref = MagicMock()
        engine_ref.audit = repo
        engine_ref.experience_engine = engine
        engine_ref.model_registry = registry
        engine_ref._last_experience_decision = None
        engine_ref.rebuild_experience_intelligence = lambda: len(engine.self_heal())

        client = TestClient(create_app(engine_ref=engine_ref))

        summary = client.get("/api/experience/summary")
        assert summary.status_code == 200
        body = summary.json()
        assert body["enabled"] is True
        assert body["recorded_experiences"] == 18
        assert body["feature_dimension"] == 50
        assert body["retired_strategies"] >= 1
        assert body["schema_distribution"]["scalp_v1/50D"] == 18

        strategies = client.get("/api/experience/strategies?limit=10")
        assert strategies.status_code == 200
        rows = strategies.json()
        assert any(r["strategy_id"] == "strat_api" for r in rows)
        assert any(r["lifecycle_state"] == "RETIRED" for r in rows)

        models = client.get("/api/experience/models")
        assert models.status_code == 200
        assert any(m["model_version"] == "v1.0" for m in models.json())

        decision = client.get("/api/experience/decision")
        assert decision.status_code == 200
        assert decision.json()["available"] is False

        heal = client.post("/api/experience/self-heal")
        assert heal.status_code == 200
        assert heal.json()["success"] is True
        repo._queue.join()

        # Raw experience rows survived the rebuild.
        assert ledger.count_experiences() == 18
    finally:
        repo.close()
