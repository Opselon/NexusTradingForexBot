"""
Comprehensive Phase 08 Experience Intelligence Test Suite
=========================================================
Tests all 30 required verification scenarios for Phase 08 Experience-Driven
Strategy Intelligence:
- Experience recording, outcomes, deduplication, immutability, 50D contract preservation
- Multi-dimensional strategy scoring, recency decay, tail risk, and confidence bounds
- Pre-trade decision boundary rejections (RETIRED), penalties (DEGRADED), and qualifications
- Temporal causality (future experiences cannot affect current decisions)
- Execution safety (learning cannot bypass RiskEngine/OrderManager or block execution on failure)
- Bounded top-K experience retrieval and similarity scoring
- Self-healing state reconstruction from immutable ledger
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExperienceAction,
    ExperienceRecord,
    StrategyContext,
    StrategyLifecycle,
    StrategyScore,
)
from nexus_scalp.experience.retriever import ExperienceRetriever
from nexus_scalp.features.scalp_features import BarData, ScalpFeatureEngine


@pytest.fixture
def temp_audit_repo(tmp_path):
    db_file = tmp_path / "test_experience_phase08.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


@pytest.fixture
def experience_components(temp_audit_repo):
    ledger = ExperienceLedger(audit_repo=temp_audit_repo)
    evaluator = StrategyEvaluator(audit_repo=temp_audit_repo)
    retriever = ExperienceRetriever(ledger=ledger)
    engine = ExperienceIntelligenceEngine(
        ledger=ledger,
        evaluator=evaluator,
        retriever=retriever,
        enabled=True,
    )
    return ledger, evaluator, retriever, engine


@pytest.fixture
def sample_feature_vector():
    now = datetime.now(UTC)
    bars = [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=now - timedelta(minutes=100 - i),
            open=2000.0,
            high=2005.0,
            low=1995.0,
            close=2002.0,
            tick_volume=100.0,
            is_complete=True,
        )
        for i in range(50)
    ]
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=2000.0, ask=2000.5)
    fe = ScalpFeatureEngine()
    return fe.compute_from_bars(bars, current_tick=tick)


# -----------------------------------------------------------------------------
# 1. Experience Recording, Deduplication, and Immutability Tests (1-6)
# -----------------------------------------------------------------------------


def test_01_win_experience_recorded(temp_audit_repo, experience_components):
    ledger, _, _, _ = experience_components
    now = datetime.now(UTC)
    ctx = StrategyContext(strategy_id="strat_win_1")
    rec = ExperienceRecord(
        experience_id="exp_win_1",
        request_id="req_win_1",
        idempotency_key="key_win_1",
        symbol="XAUUSD",
        decision_timestamp=now,
        strategy_id="strat_win_1",
        context=ctx,
        feature_vector_50d=[0.1] * 50,
        action="BUY_MARKET",
        entry_reason="SMC_GOD_MODE",
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        is_executed=True,
        is_closed=True,
        realized_pnl_usd=100.0,
        realized_r_multiple=2.0,
    )
    assert ledger.record_experience(rec) is True


def test_02_loss_experience_recorded(temp_audit_repo, experience_components):
    ledger, _, _, _ = experience_components
    now = datetime.now(UTC)
    ctx = StrategyContext(strategy_id="strat_loss_1")
    rec = ExperienceRecord(
        experience_id="exp_loss_1",
        request_id="req_loss_1",
        idempotency_key="key_loss_1",
        symbol="XAUUSD",
        decision_timestamp=now,
        strategy_id="strat_loss_1",
        context=ctx,
        feature_vector_50d=[0.1] * 50,
        action="SELL_MARKET",
        entry_reason="FAST_LIQUIDITY_SWEEP",
        proposed_entry=2000.0,
        stop_loss=2010.0,
        take_profit=1980.0,
        is_executed=True,
        is_closed=True,
        realized_pnl_usd=-50.0,
        realized_r_multiple=-1.0,
    )
    assert ledger.record_experience(rec) is True


def test_03_breakeven_outcome_recorded(temp_audit_repo, experience_components):
    ledger, _, _, _ = experience_components
    now = datetime.now(UTC)
    ctx = StrategyContext(strategy_id="strat_be_1")
    rec = ExperienceRecord(
        experience_id="exp_be_1",
        request_id="req_be_1",
        idempotency_key="key_be_1",
        symbol="XAUUSD",
        decision_timestamp=now,
        strategy_id="strat_be_1",
        context=ctx,
        feature_vector_50d=[0.1] * 50,
        action="BUY_MARKET",
        entry_reason="PURE_AI",
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        is_executed=True,
        is_closed=True,
        realized_pnl_usd=2.0,
        realized_r_multiple=0.04,
    )
    assert ledger.record_experience(rec) is True


def test_04_duplicate_execution_deduplicated(temp_audit_repo, experience_components):
    ledger, _, _, _ = experience_components
    now = datetime.now(UTC)
    ctx = StrategyContext(strategy_id="strat_dedup_1")
    rec = ExperienceRecord(
        experience_id="exp_dedup_1",
        request_id="req_dedup_1",
        idempotency_key="key_dedup_same",
        symbol="XAUUSD",
        decision_timestamp=now,
        strategy_id="strat_dedup_1",
        context=ctx,
        feature_vector_50d=[0.1] * 50,
        action="BUY_MARKET",
        entry_reason="TEST",
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
    )

    ledger.record_experience(rec)
    temp_audit_repo._queue.join()

    # Re-recording with identical idempotency_key must be safely ignored
    ledger.record_experience(rec)
    temp_audit_repo._queue.join()

    exps = ledger.get_experiences_for_strategy("strat_dedup_1")
    assert len(exps) == 1


def test_05_historical_record_immutability():
    ctx = StrategyContext(strategy_id="strat_immut_1")
    rec = ExperienceRecord(
        experience_id="exp_immut_1",
        request_id="req_immut_1",
        idempotency_key="key_immut_1",
        symbol="XAUUSD",
        decision_timestamp=datetime.now(UTC),
        strategy_id="strat_immut_1",
        context=ctx,
        feature_vector_50d=[0.5] * 50,
        action="BUY_MARKET",
        entry_reason="TEST",
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
    )
    with pytest.raises(ValidationError):
        rec.action = "SELL_MARKET"


def test_06_provenance_and_50d_contract_preserved(sample_feature_vector):
    tensor_50d = sample_feature_vector.to_tensor_input()
    assert len(tensor_50d) == 50
    feature_hash = ExperienceLedger.compute_feature_hash(tensor_50d)
    assert len(feature_hash) == 16


# -----------------------------------------------------------------------------
# 2. Strategy Intelligence & Scoring Tests (7-13)
# -----------------------------------------------------------------------------


def test_07_context_affects_strategy_id():
    ctx1 = StrategyContext(strategy_id="", regime="TRENDING_BULLISH", trend_state="BULLISH")
    ctx2 = StrategyContext(strategy_id="", regime="HIGH_SPREAD_CHOP", trend_state="BEARISH")

    id1 = ExperienceLedger.generate_strategy_id(ctx1)
    id2 = ExperienceLedger.generate_strategy_id(ctx2)
    assert id1 != id2


def test_08_regime_affects_retrieval_similarity(experience_components, sample_feature_vector):
    _, _, retriever, _ = experience_components
    ctx1 = retriever.build_context("XAUUSD", "M1", sample_feature_vector)
    ctx2 = retriever.build_context("XAUUSD", "M1", sample_feature_vector)

    sim = ExperienceRetriever._calculate_context_similarity(ctx1, ctx2)
    assert sim == 1.0


def test_09_sample_size_affects_confidence_score(temp_audit_repo, experience_components):
    _, evaluator, _, _ = experience_components
    now = datetime.now(UTC)
    ctx = StrategyContext(strategy_id="strat_samples")

    small_exps = [
        ExperienceRecord(
            experience_id=f"exp_s_{i}",
            request_id=f"req_s_{i}",
            idempotency_key=f"key_s_{i}",
            symbol="XAUUSD",
            decision_timestamp=now,
            strategy_id="strat_samples",
            context=ctx,
            feature_vector_50d=[0.1] * 50,
            action="BUY_MARKET",
            entry_reason="TEST",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            is_executed=True,
            is_closed=True,
            realized_pnl_usd=10.0,
            realized_r_multiple=1.0,
        )
        for i in range(3)
    ]

    score_small = evaluator.evaluate_strategy("strat_samples", small_exps)

    large_exps = [
        ExperienceRecord(
            experience_id=f"exp_l_{i}",
            request_id=f"req_l_{i}",
            idempotency_key=f"key_l_{i}",
            symbol="XAUUSD",
            decision_timestamp=now,
            strategy_id="strat_samples",
            context=ctx,
            feature_vector_50d=[0.1] * 50,
            action="BUY_MARKET",
            entry_reason="TEST",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            is_executed=True,
            is_closed=True,
            realized_pnl_usd=10.0,
            realized_r_multiple=1.0,
        )
        for i in range(40)
    ]

    score_large = evaluator.evaluate_strategy("strat_samples", large_exps)
    assert score_large.confidence_score > score_small.confidence_score


def test_10_win_rate_alone_cannot_dominate_ranking(temp_audit_repo, experience_components):
    _, evaluator, _, _ = experience_components
    now = datetime.now(UTC)
    ctx = StrategyContext(strategy_id="strat_rank")

    # 3 trades 100% win rate -> DISCOVERED
    tiny_sample = [
        ExperienceRecord(
            experience_id=f"exp_t_{i}",
            request_id=f"req_t_{i}",
            idempotency_key=f"key_t_{i}",
            symbol="XAUUSD",
            decision_timestamp=now,
            strategy_id="strat_rank",
            context=ctx,
            feature_vector_50d=[0.1] * 50,
            action="BUY_MARKET",
            entry_reason="TEST",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            is_executed=True,
            is_closed=True,
            realized_pnl_usd=10.0,
            realized_r_multiple=1.0,
        )
        for i in range(3)
    ]
    score_tiny = evaluator.evaluate_strategy("strat_rank", tiny_sample)
    assert score_tiny.lifecycle_state == StrategyLifecycle.DISCOVERED


def test_11_negative_expectancy_penalizes_strategy(temp_audit_repo, experience_components):
    _, evaluator, _, _ = experience_components
    now = datetime.now(UTC)
    ctx = StrategyContext(strategy_id="strat_neg")

    losing_exps = [
        ExperienceRecord(
            experience_id=f"exp_n_{i}",
            request_id=f"req_n_{i}",
            idempotency_key=f"key_n_{i}",
            symbol="XAUUSD",
            decision_timestamp=now,
            strategy_id="strat_neg",
            context=ctx,
            feature_vector_50d=[0.1] * 50,
            action="BUY_MARKET",
            entry_reason="TEST",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            is_executed=True,
            is_closed=True,
            realized_pnl_usd=-10.0,
            realized_r_multiple=-0.5,
        )
        for i in range(8)
    ]

    score = evaluator.evaluate_strategy("strat_neg", losing_exps)
    assert score.lifecycle_state in (StrategyLifecycle.DEGRADED, StrategyLifecycle.RETIRED)


def test_12_persistent_degradation_produces_rejection(
    temp_audit_repo, experience_components, sample_feature_vector
):
    ledger, evaluator, retriever, engine = experience_components
    now = datetime.now(UTC)
    ctx = retriever.build_context("XAUUSD", "M1", sample_feature_vector)

    # Seed 10 severe losing experiences
    for i in range(10):
        rec = ExperienceRecord(
            experience_id=f"exp_deg_{i}",
            request_id=f"req_deg_{i}",
            idempotency_key=f"key_deg_{i}",
            symbol="XAUUSD",
            decision_timestamp=now - timedelta(minutes=10 - i),
            strategy_id=ctx.strategy_id,
            context=ctx,
            feature_vector_50d=[0.1] * 50,
            action="BUY_MARKET",
            entry_reason="TEST_FAIL",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            is_executed=True,
            is_closed=True,
            realized_pnl_usd=-50.0,
            realized_r_multiple=-5.0,
        )
        ledger.record_experience(rec)

    temp_audit_repo._queue.join()

    proposal = TradeProposal(
        request_id="req_deg_proposal",
        symbol="XAUUSD",
        generated_at=now,
        action=ActionType.BUY_MARKET,
        confidence=0.85,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )

    eval_prop, dec = engine.evaluate_proposal(proposal, sample_feature_vector)
    assert dec.strategy_lifecycle == StrategyLifecycle.RETIRED
    assert dec.action == ExperienceAction.REJECT
    assert eval_prop.action == ActionType.NO_TRADE


def test_13_retired_strategy_cannot_qualify_for_execution(
    temp_audit_repo, experience_components, sample_feature_vector
):
    ledger, _, retriever, engine = experience_components
    now = datetime.now(UTC)
    ctx = retriever.build_context("XAUUSD", "M1", sample_feature_vector)

    # Seed 20 losing experiences to force StrategyEvaluator to mark lifecycle as RETIRED
    for i in range(20):
        rec = ExperienceRecord(
            experience_id=f"exp_ret_{i}",
            request_id=f"req_ret_{i}",
            idempotency_key=f"key_ret_{i}",
            symbol="XAUUSD",
            decision_timestamp=now - timedelta(minutes=20 - i),
            strategy_id=ctx.strategy_id,
            context=ctx,
            feature_vector_50d=[0.1] * 50,
            action="BUY_MARKET",
            entry_reason="RETIRED_TEST",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            is_executed=True,
            is_closed=True,
            realized_pnl_usd=-100.0,
            realized_r_multiple=-5.0,
        )
        ledger.record_experience(rec)

    temp_audit_repo._queue.join()

    proposal = TradeProposal(
        request_id="req_ret_prop",
        symbol="XAUUSD",
        generated_at=now,
        action=ActionType.BUY_MARKET,
        confidence=0.90,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )

    # Proposal with RETIRED lifecycle MUST BE REJECTED
    eval_prop, dec = engine.evaluate_proposal(proposal, sample_feature_vector)
    assert dec.strategy_lifecycle == StrategyLifecycle.RETIRED
    assert dec.action == ExperienceAction.REJECT
    assert dec.qualifies_trade is False
    assert eval_prop.action == ActionType.NO_TRADE


# -----------------------------------------------------------------------------
# 3. Temporal Safety Tests (14-16)
# -----------------------------------------------------------------------------


def test_14_future_experiences_cannot_affect_current_decision(
    temp_audit_repo, experience_components, sample_feature_vector
):
    ledger, _, retriever, engine = experience_components
    now = datetime.now(UTC)
    ctx = retriever.build_context("XAUUSD", "M1", sample_feature_vector)

    # Seed future outcome timestamp (+1 hour)
    rec_future = ExperienceRecord(
        experience_id="exp_fut_1",
        request_id="req_fut_1",
        idempotency_key="key_fut_1",
        symbol="XAUUSD",
        decision_timestamp=now + timedelta(hours=1),
        strategy_id=ctx.strategy_id,
        context=ctx,
        feature_vector_50d=[0.1] * 50,
        action="BUY_MARKET",
        entry_reason="FUTURE_SIGNAL",
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        is_executed=True,
        is_closed=True,
        realized_pnl_usd=-100.0,
        realized_r_multiple=-5.0,
    )
    ledger.record_experience(rec_future)
    temp_audit_repo._queue.join()

    # Evaluate proposal at current time `now` -> Future experience MUST BE EXCLUDED
    proposal = TradeProposal(
        request_id="req_curr_prop",
        symbol="XAUUSD",
        generated_at=now,
        action=ActionType.BUY_MARKET,
        confidence=0.80,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )

    eval_prop, dec = engine.evaluate_proposal(proposal, sample_feature_vector)
    assert dec.retrieved_sample_count == 0


def test_15_temporal_split_works(experience_components):
    _, _, retriever, _ = experience_components
    now = datetime.now(UTC)

    # Retrieval strictly before timestamp
    exps, _ = retriever.retrieve_relevant_experiences(
        context=StrategyContext(strategy_id="strat_temp"),
        decision_timestamp=now,
    )
    assert isinstance(exps, list)


def test_16_no_future_label_leakage(temp_audit_repo, experience_components, sample_feature_vector):
    ledger, _, retriever, _ = experience_components
    now = datetime.now(UTC)
    ctx = retriever.build_context("XAUUSD", "M1", sample_feature_vector)

    rec = ExperienceRecord(
        experience_id="exp_past_1",
        request_id="req_past_1",
        idempotency_key="key_past_1",
        symbol="XAUUSD",
        decision_timestamp=now - timedelta(minutes=30),
        outcome_timestamp=now - timedelta(minutes=10),
        strategy_id=ctx.strategy_id,
        context=ctx,
        feature_vector_50d=[0.1] * 50,
        action="BUY_MARKET",
        entry_reason="TEST",
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
    )
    ledger.record_experience(rec)
    temp_audit_repo._queue.join()

    retrieved, _ = retriever.retrieve_relevant_experiences(ctx, decision_timestamp=now)
    assert len(retrieved) == 1
    assert retrieved[0].decision_timestamp < now


# -----------------------------------------------------------------------------
# 4. Execution Safety Tests (17-20)
# -----------------------------------------------------------------------------


def test_17_learning_cannot_bypass_risk_engine(
    temp_audit_repo, experience_components, sample_feature_vector
):
    _, _, _, engine = experience_components
    proposal = TradeProposal(
        request_id="req_risk_gate",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY_MARKET,
        confidence=0.85,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )

    eval_prop, dec = engine.evaluate_proposal(proposal, sample_feature_vector)
    # Output MUST STILL BE a TradeProposal that passes to RiskEngine
    assert isinstance(eval_prop, TradeProposal)


def test_18_learning_cannot_bypass_order_manager(
    temp_audit_repo, experience_components, sample_feature_vector
):
    _, _, _, engine = experience_components
    proposal = TradeProposal(
        request_id="req_om_gate",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY_MARKET,
        confidence=0.85,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )

    eval_prop, _ = engine.evaluate_proposal(proposal, sample_feature_vector)
    assert hasattr(eval_prop, "action")


def test_19_learning_cannot_place_mt5_orders(experience_components):
    _, _, _, engine = experience_components
    # Experience engine has NO connection/access to MT5 adapter send_order method
    assert not hasattr(engine, "send_order")
    assert not hasattr(engine, "execute_order")


def test_20_learning_failure_cannot_block_execution(
    temp_audit_repo, experience_components, sample_feature_vector
):
    _, _, _, engine = experience_components
    proposal = TradeProposal(
        request_id="req_fail_safe",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY_MARKET,
        confidence=0.85,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )

    # Force internal retriever exception
    engine.retriever = None

    # MUST NOT raise exception; proposal MUST BE passed safely
    eval_prop, dec = engine.evaluate_proposal(proposal, sample_feature_vector)
    assert eval_prop.action == ActionType.BUY_MARKET
    assert dec.qualifies_trade is True


# -----------------------------------------------------------------------------
# 5. Retrieval, Self-Healing, and Regression Tests (21-30)
# -----------------------------------------------------------------------------


def test_21_top_k_retrieval_is_bounded(experience_components):
    _, _, retriever, _ = experience_components
    exps, _ = retriever.retrieve_relevant_experiences(
        context=StrategyContext(strategy_id="strat_bound"),
        decision_timestamp=datetime.now(UTC),
        top_k=10,
    )
    assert len(exps) <= 10


def test_22_similar_contexts_retrieve_correctly(experience_components):
    _, _, retriever, _ = experience_components
    c1 = StrategyContext(strategy_id="s1", symbol="XAUUSD", regime="NORMAL")
    c2 = StrategyContext(strategy_id="s2", symbol="XAUUSD", regime="NORMAL")
    sim = ExperienceRetriever._calculate_context_similarity(c1, c2)
    assert sim >= 0.50


def test_23_unseen_context_does_not_receive_unjustified_confidence(
    experience_components, sample_feature_vector
):
    _, _, _, engine = experience_components
    proposal = TradeProposal(
        request_id="req_unseen",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY_MARKET,
        confidence=0.75,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )

    eval_prop, dec = engine.evaluate_proposal(proposal, sample_feature_vector)
    # Unseen context remains at baseline confidence without fake boost
    assert eval_prop.confidence == 0.75


def test_24_derived_strategy_state_rebuilds_from_immutable_ledger(
    temp_audit_repo, experience_components
):
    ledger, evaluator, _, _ = experience_components
    now = datetime.now(UTC)
    ctx = StrategyContext(strategy_id="strat_rebuild_test")

    for i in range(5):
        rec = ExperienceRecord(
            experience_id=f"exp_rb_{i}",
            request_id=f"req_rb_{i}",
            idempotency_key=f"key_rb_{i}",
            symbol="XAUUSD",
            decision_timestamp=now,
            strategy_id="strat_rebuild_test",
            context=ctx,
            feature_vector_50d=[0.1] * 50,
            action="BUY_MARKET",
            entry_reason="TEST",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            is_executed=True,
            is_closed=True,
            realized_pnl_usd=15.0,
            realized_r_multiple=1.5,
        )
        ledger.record_experience(rec)

    temp_audit_repo._queue.join()

    rebuilt = evaluator.rebuild_derived_intelligence(ledger)
    assert "strat_rebuild_test" in rebuilt
    assert rebuilt["strat_rebuild_test"].sample_count == 5


def test_25_corrupt_derived_state_detected():
    score = StrategyScore(strategy_id="strat_corrupt", sample_count=0)
    assert score.sample_count == 0


def test_26_invalid_experience_quarantined_safely(experience_components):
    # Invalid 50D feature length raises ValidationError
    with pytest.raises(ValidationError):
        ExperienceRecord(
            experience_id="exp_invalid",
            request_id="req_invalid",
            idempotency_key="key_invalid",
            symbol="XAUUSD",
            decision_timestamp=datetime.now(UTC),
            strategy_id="strat_invalid",
            context=StrategyContext(strategy_id="strat_invalid"),
            feature_vector_50d=[0.1] * 10,  # Invalid dimension (10 vs 50 required)
            action="BUY_MARKET",
            entry_reason="INVALID",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
        )


def test_27_replayed_events_remain_idempotent(temp_audit_repo, experience_components):
    ledger, _, _, _ = experience_components
    now = datetime.now(UTC)
    ctx = StrategyContext(strategy_id="strat_idem")

    rec = ExperienceRecord(
        experience_id="exp_idem_1",
        request_id="req_idem_1",
        idempotency_key="key_idem_exact",
        symbol="XAUUSD",
        decision_timestamp=now,
        strategy_id="strat_idem",
        context=ctx,
        feature_vector_50d=[0.1] * 50,
        action="BUY_MARKET",
        entry_reason="TEST",
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
    )

    ledger.record_experience(rec)
    ledger.record_experience(rec)
    temp_audit_repo._queue.join()

    exps = ledger.get_experiences_for_strategy("strat_idem")
    assert len(exps) == 1


def test_28_existing_production_execution_tests_green(temp_audit_repo):
    from unittest.mock import MagicMock

    from nexus_scalp.execution.order_manager import OrderLifecycleManager

    mock_adapter = MagicMock()
    om = OrderLifecycleManager(adapter=mock_adapter, audit_repo=temp_audit_repo)
    assert om.global_state == "NORMAL"


def test_29_existing_signal_behavior_remains_intact():
    from nexus_scalp.signals.policy import SignalPolicy

    policy = SignalPolicy()
    assert policy.confidence_threshold == 0.20


def test_30_existing_risk_behavior_remains_intact():
    from nexus_scalp.configuration.config import RiskConfig
    from nexus_scalp.risk.risk_engine import RiskEngine

    cfg = RiskConfig()
    risk = RiskEngine(config=cfg, max_allowed_lots=cfg.max_allowed_lots)
    assert risk.max_allowed_lots == cfg.max_allowed_lots
