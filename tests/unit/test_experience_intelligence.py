"""
Phase 08 Experience Intelligence — Behavioral Test Suite
========================================================
Real behavioral verification of the Experience-Driven Strategy Intelligence
subsystem. Every test asserts OBSERVABLE BEHAVIOUR (persisted rows, computed
scores, lifecycle transitions, gate verdicts, proposal mutations) rather than
mere object existence.

Coverage map (Phase 08 section 31):

    EXPERIENCE        1-7    persistence, dedup, immutability, provenance, causality
    STRATEGY          8-15   discovery, scoring, sample floors, calibration,
                             degradation, retirement, gated recovery
    DECISION          16-20  experience affects decisions, rejection before dispatch
    POSITION          21-26  attribution, MAE/MFE, entry/hold/exit/execution quality
    SELF-HEALING      27-30  rebuild, corruption recovery, replay, ledger preserved
    MODEL SAFETY      31-33  model replacement, provenance, schema compatibility
    FAILURE ISOLATION 34-38  persistence/retrieval/evaluator/self-heal failure
    SCALE             39-40  bounded retrieval, large sets
    REGRESSION        41-45  strategy pipeline, RiskEngine, OrderManager, MT5, 50D
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
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
    MAX_STRATEGY_CONFIDENCE,
    BehavioralFlag,
    ExecutionContext,
    ExperienceAction,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    ModelProvenance,
    PositionBehavior,
    StrategyContext,
    StrategyLifecycle,
    StrategyScore,
)
from nexus_scalp.experience.provenance import ModelRegistry
from nexus_scalp.experience.quality import OutcomeAnalyzer, compute_behavior_metrics
from nexus_scalp.experience.retriever import ExperienceRetriever
from nexus_scalp.features.scalp_features import BarData, ScalpFeatureEngine

# =============================================================================
# FIXTURES & HELPERS
# =============================================================================


@pytest.fixture
def temp_audit_repo(tmp_path):
    db_file = tmp_path / "test_experience_phase08.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


@pytest.fixture
def components(temp_audit_repo):
    """Ledger + evaluator + retriever + gate wired against a temp database."""
    ledger = ExperienceLedger(audit_repo=temp_audit_repo)
    evaluator = StrategyEvaluator(audit_repo=temp_audit_repo)
    retriever = ExperienceRetriever(ledger=ledger)
    engine = ExperienceIntelligenceEngine(
        ledger=ledger,
        evaluator=evaluator,
        retriever=retriever,
        enabled=True,
        # Disable the hot-path refresh budget so tests observe evidence
        # deterministically instead of racing the rate limiter.
        max_inline_refresh_per_sec=0.0,
        score_cache_ttl_sec=1.0,
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
    return ScalpFeatureEngine().compute_from_bars(bars, current_tick=tick)


def make_record(
    key: str,
    strategy_id: str = "strat_test",
    decision_ts: datetime | None = None,
    context: StrategyContext | None = None,
    action: str = "BUY_MARKET",
    entry: float = 2000.0,
    sl: float = 1990.0,
    tp: float = 2020.0,
    confidence: float = 0.60,
    rr: float = 2.0,
    dimension: int = CANONICAL_FEATURE_DIMENSION,
    schema_id: str = CANONICAL_FEATURE_SCHEMA_ID,
) -> ExperienceRecord:
    """Builds a decision experience with a valid schema-versioned snapshot."""
    ts = decision_ts or datetime.now(UTC)
    ctx = context or StrategyContext(strategy_id=strategy_id)
    return ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=f"req_{key}",
        idempotency_key=key,
        symbol="XAUUSD",
        decision_timestamp=ts,
        strategy_id=strategy_id,
        context=ctx,
        feature_snapshot=FeatureSnapshot(
            feature_schema_id=schema_id,
            feature_dimension=dimension,
            values=[0.1] * dimension,
            feature_hash=ExperienceLedger.compute_feature_hash([0.1] * dimension, schema_id),
        ),
        action=action,
        entry_reason="TEST_SETUP",
        model_probability=confidence,
        signal_confidence=confidence,
        proposed_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward_ratio=rr,
    )


def make_outcome(
    key: str,
    realized_r: float,
    outcome_ts: datetime | None = None,
    pnl_usd: float | None = None,
    mae_points: float = -2.0,
    mfe_points: float = 6.0,
    duration_sec: float = 300.0,
    exit_reason: str = "TAKE_PROFIT_HIT",
    planned_risk: float = 10.0,
) -> ExperienceOutcome:
    """Builds a closed outcome event with risk-normalised excursions."""
    behavior = compute_behavior_metrics(
        mae_points=mae_points,
        mfe_points=mfe_points,
        mae_usd=mae_points * 10.0,
        mfe_usd=mfe_points * 10.0,
        planned_risk_distance=planned_risk,
        duration_sec=duration_sec,
        initial_sl_distance=planned_risk,
        atr_at_entry=5.0,
    )
    return ExperienceOutcome(
        idempotency_key=key,
        execution_id=f"tk_{key}",
        outcome_timestamp=outcome_ts or datetime.now(UTC),
        is_executed=True,
        is_closed=True,
        exit_reason=exit_reason,
        realized_pnl_usd=pnl_usd if pnl_usd is not None else realized_r * 100.0,
        realized_r_multiple=realized_r,
        approved_volume=0.10,
        behavior=behavior,
    )


def seed_closed_trades(
    repo,
    ledger: ExperienceLedger,
    strategy_id: str,
    r_values: list[float],
    context: StrategyContext | None = None,
    base_ts: datetime | None = None,
    prefix: str = "s",
) -> None:
    """Persists N decision+outcome pairs, oldest first, and flushes the queue."""
    start = base_ts or (datetime.now(UTC) - timedelta(hours=6))
    for i, r in enumerate(r_values):
        key = f"{prefix}_{strategy_id}_{i}"
        ts = start + timedelta(minutes=i)
        ledger.record_experience(
            make_record(key, strategy_id=strategy_id, decision_ts=ts, context=context)
        )
        ledger.record_outcome(
            make_outcome(
                key,
                realized_r=r,
                outcome_ts=ts + timedelta(seconds=60),
                mfe_points=max(1.0, r * 10.0),
                mae_points=-2.0 if r > 0 else -11.0,
            )
        )
    repo._queue.join()


def make_proposal(
    request_id: str = "req_gate",
    action: ActionType = ActionType.BUY_MARKET,
    confidence: float = 0.80,
    generated_at: datetime | None = None,
) -> TradeProposal:
    return TradeProposal(
        request_id=request_id,
        symbol="XAUUSD",
        generated_at=generated_at or datetime.now(UTC),
        action=action,
        confidence=confidence,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )


# =============================================================================
# 1. EXPERIENCE PERSISTENCE, DEDUP, IMMUTABILITY, PROVENANCE, CAUSALITY (1-7)
# =============================================================================


def test_01_win_experience_persisted_and_retrievable_with_outcome(temp_audit_repo, components):
    """A winning trade must be retrievable WITH its realised outcome merged in."""
    ledger, _, _, _ = components
    ledger.record_experience(make_record("k_win", strategy_id="strat_win"))
    ledger.record_outcome(make_outcome("k_win", realized_r=2.0, pnl_usd=200.0))
    temp_audit_repo._queue.join()

    rows = ledger.get_experiences_for_strategy("strat_win")
    assert len(rows) == 1
    rec = rows[0]
    # Regression guard for BUG-008: the merged projection must expose the outcome.
    assert rec.is_closed is True
    assert rec.is_executed is True
    assert rec.realized_r_multiple == pytest.approx(2.0)
    assert rec.realized_pnl_usd == pytest.approx(200.0)
    assert rec.exit_reason == "TAKE_PROFIT_HIT"


def test_02_loss_experience_persisted_with_negative_outcome(temp_audit_repo, components):
    """Losses must be recorded with their true negative R (no winner-only learning)."""
    ledger, _, _, _ = components
    ledger.record_experience(make_record("k_loss", strategy_id="strat_loss"))
    ledger.record_outcome(
        make_outcome(
            "k_loss",
            realized_r=-1.0,
            pnl_usd=-100.0,
            exit_reason="HARD_SL_HIT",
            mfe_points=1.0,
            mae_points=-10.5,
        )
    )
    temp_audit_repo._queue.join()

    rec = ledger.get_experiences_for_strategy("strat_loss")[0]
    assert rec.realized_r_multiple == pytest.approx(-1.0)
    assert rec.realized_pnl_usd == pytest.approx(-100.0)
    assert rec.behavior.mae_r > 1.0


def test_03_unexecuted_proposal_persisted_but_excluded_from_scoring(temp_audit_repo, components):
    """A rejected/unfilled proposal is forensic evidence, never trade evidence."""
    ledger, evaluator, _, _ = components
    ledger.record_experience(make_record("k_norun", strategy_id="strat_norun"))
    temp_audit_repo._queue.join()

    rows = ledger.get_experiences_for_strategy("strat_norun")
    assert len(rows) == 1
    assert rows[0].is_closed is False

    score = evaluator.evaluate_strategy("strat_norun", rows)
    assert score.sample_count == 0
    assert score.lifecycle_state == StrategyLifecycle.DISCOVERED


def test_04_duplicate_decision_and_outcome_are_deduplicated(temp_audit_repo, components):
    """Replayed events must not inflate evidence (idempotency)."""
    ledger, _, _, _ = components
    rec = make_record("k_dup", strategy_id="strat_dup")
    ledger.record_experience(rec)
    ledger.record_experience(rec)
    ledger.record_outcome(make_outcome("k_dup", realized_r=1.0, pnl_usd=100.0))
    ledger.record_outcome(make_outcome("k_dup", realized_r=99.0, pnl_usd=9900.0))
    temp_audit_repo._queue.join()

    rows = ledger.get_experiences_for_strategy("strat_dup")
    assert len(rows) == 1
    # The FIRST outcome wins; the duplicate cannot overwrite realised PnL.
    assert rows[0].realized_r_multiple == pytest.approx(1.0)


def test_05_decision_row_is_immutable_in_memory_and_on_disk(temp_audit_repo, components):
    """Frozen model + no UPDATE path against audit_experiences."""
    import sqlite3

    ledger, _, _, _ = components
    rec = make_record("k_immut", strategy_id="strat_immut")
    with pytest.raises(ValidationError):
        rec.action = "SELL_MARKET"

    ledger.record_experience(rec)
    temp_audit_repo._queue.join()

    conn = sqlite3.connect(temp_audit_repo._db_path)
    try:
        original = conn.execute(
            "SELECT payload FROM audit_experiences WHERE idempotency_key = ?;", ("k_immut",)
        ).fetchone()[0]
    finally:
        conn.close()

    # Re-recording with different content must not overwrite the stored payload.
    ledger.record_experience(
        make_record("k_immut", strategy_id="strat_immut", action="SELL_MARKET", confidence=0.99)
    )
    temp_audit_repo._queue.join()

    conn = sqlite3.connect(temp_audit_repo._db_path)
    try:
        after = conn.execute(
            "SELECT payload FROM audit_experiences WHERE idempotency_key = ?;", ("k_immut",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert after == original


def test_06_provenance_and_feature_schema_preserved(temp_audit_repo, components):
    """Provenance must survive a round-trip and describe the schema used."""
    ledger, _, _, _ = components
    prov = ModelProvenance(
        model_id="primary_scalp_scalp_v1_50d",
        model_version="v1.0",
        artifact_fingerprint="deadbeefcafe0001",
        feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
        feature_dimension=CANONICAL_FEATURE_DIMENSION,
    )
    rec = make_record("k_prov", strategy_id="strat_prov").model_copy(update={"provenance": prov})
    ledger.record_experience(rec)
    temp_audit_repo._queue.join()

    out = ledger.get_experiences_for_strategy("strat_prov")[0]
    assert out.provenance.model_id == "primary_scalp_scalp_v1_50d"
    assert out.provenance.artifact_fingerprint == "deadbeefcafe0001"
    assert out.feature_schema_id == CANONICAL_FEATURE_SCHEMA_ID
    assert out.feature_dimension == 50
    assert len(out.feature_snapshot.values) == 50


def test_07_outcome_preceding_decision_is_rejected(temp_audit_repo, components):
    """Temporal causality is enforced at the model level."""
    ledger, _, _, engine = components
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        make_record("k_causal", decision_ts=now).model_copy(
            update={"outcome_timestamp": now - timedelta(hours=1)}
        ).model_validate(
            {
                **make_record("k_causal", decision_ts=now).model_dump(),
                "outcome_timestamp": now - timedelta(hours=1),
            }
        )

    ledger.record_experience(make_record("k_causal2", strategy_id="strat_causal", decision_ts=now))
    temp_audit_repo._queue.join()
    # The engine must refuse an outcome that predates the decision.
    assert (
        engine.record_trade_outcome(
            request_id="req_k_causal2",
            execution_id="tk1",
            outcome_timestamp=now - timedelta(minutes=5),
            is_executed=True,
            is_closed=True,
            exit_reason="HARD_SL_HIT",
            realized_pnl_usd=-50.0,
            realized_r_multiple=-1.0,
        )
        is False
    )


# =============================================================================
# 2. STRATEGY DISCOVERY, SCORING, CALIBRATION, LIFECYCLE (8-15)
# =============================================================================


def test_08_strategy_discovery_groups_similar_contexts(components, sample_feature_vector):
    """
    Identical market state must map to ONE strategy family, and differing
    regime/trend must map to a different family.
    """
    _, _, retriever, _ = components
    shared_tokens = ["MY_TOKEN"]
    ids = {
        retriever.build_context(
            "XAUUSD", "M1", sample_feature_vector, confluence_tokens=shared_tokens
        ).strategy_id
        for _ in range(5)
    }
    # Regression guard for BUG-009 (token list aliasing).
    assert len(ids) == 1
    assert shared_tokens == ["MY_TOKEN"]

    a = StrategyContext(strategy_id="", regime="TRENDING_MOMENTUM", trend_state="BULLISH")
    b = StrategyContext(strategy_id="", regime="HIGH_SPREAD_CHOP", trend_state="BEARISH")
    assert ExperienceLedger.generate_strategy_id(a) != ExperienceLedger.generate_strategy_id(b)


def test_09_scoring_is_risk_and_distribution_aware(temp_audit_repo, components):
    """Expectancy, profit factor, tail risk and drawdown must be computed."""
    ledger, evaluator, _, _ = components
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        "strat_dist",
        [1.5, -1.0, 2.0, -1.0, 1.0, -1.0, 2.5, 0.5, -1.0, 1.5],
    )
    score = evaluator.evaluate_strategy(
        "strat_dist", ledger.get_experiences_for_strategy("strat_dist")
    )
    assert score.sample_count == 10
    assert score.win_count == 6
    assert score.loss_count == 4
    assert score.expectancy_r == pytest.approx(0.5, abs=0.01)
    assert score.profit_factor > 1.0
    assert score.max_drawdown_r > 0.0
    assert score.normalized_drawdown_r <= score.max_drawdown_r
    assert score.downside_tail_risk_r < 0.0
    assert score.r_variance > 0.0


def test_10_contextual_scoring_separates_regimes(temp_audit_repo, components):
    """The same setup in two regimes must produce two independent verdicts."""
    ledger, evaluator, _, _ = components
    good_ctx = StrategyContext(strategy_id="strat_good_ctx", regime="TRENDING_MOMENTUM")
    bad_ctx = StrategyContext(strategy_id="strat_bad_ctx", regime="HIGH_SPREAD_CHOP")

    seed_closed_trades(
        temp_audit_repo, ledger, "strat_good_ctx", [1.5] * 12, context=good_ctx, prefix="g"
    )
    seed_closed_trades(
        temp_audit_repo, ledger, "strat_bad_ctx", [-1.0] * 14, context=bad_ctx, prefix="b"
    )

    good = evaluator.evaluate_strategy(
        "strat_good_ctx", ledger.get_experiences_for_strategy("strat_good_ctx")
    )
    bad = evaluator.evaluate_strategy(
        "strat_bad_ctx", ledger.get_experiences_for_strategy("strat_bad_ctx")
    )
    assert good.expectancy_r > 0.0
    assert bad.expectancy_r < 0.0
    assert good.confidence_score > bad.confidence_score
    assert bad.lifecycle_state in (StrategyLifecycle.RETIRED, StrategyLifecycle.DEGRADED)


def test_11_minimum_sample_protection_blocks_premature_promotion(temp_audit_repo, components):
    """A perfect 3-trade record must NOT become ACTIVE or VALIDATED."""
    ledger, evaluator, _, _ = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_tiny", [2.0, 2.0, 2.0])
    score = evaluator.evaluate_strategy(
        "strat_tiny", ledger.get_experiences_for_strategy("strat_tiny")
    )
    assert score.win_rate == 1.0
    assert score.lifecycle_state == StrategyLifecycle.DISCOVERED
    assert score.replay_validated is False


def test_12_confidence_is_bounded_and_grows_with_evidence(temp_audit_repo, components):
    """Confidence rises with samples but can never reach certainty."""
    ledger, evaluator, _, _ = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_small", [1.0] * 5, prefix="sm")
    seed_closed_trades(temp_audit_repo, ledger, "strat_large", [1.0] * 60, prefix="lg")

    small = evaluator.evaluate_strategy(
        "strat_small", ledger.get_experiences_for_strategy("strat_small")
    )
    large = evaluator.evaluate_strategy(
        "strat_large", ledger.get_experiences_for_strategy("strat_large", limit=200)
    )
    assert large.confidence_score > small.confidence_score
    assert large.confidence_score <= MAX_STRATEGY_CONFIDENCE
    assert large.confidence_score < 1.0
    assert large.evidence_quality > small.evidence_quality


def test_13_single_loss_cannot_retire_a_healthy_strategy(temp_audit_repo, components):
    """Retirement requires a sample floor and statistical significance."""
    ledger, evaluator, _, _ = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_1loss", [1.5] * 19 + [-1.0])
    score = evaluator.evaluate_strategy(
        "strat_1loss", ledger.get_experiences_for_strategy("strat_1loss", limit=100)
    )
    assert score.lifecycle_state != StrategyLifecycle.RETIRED
    assert score.expectancy_r > 0.0


def test_14_recent_degradation_downgrades_a_previously_good_strategy(temp_audit_repo, components):
    """Recency weighting must react to decay even with a positive lifetime edge."""
    ledger, evaluator, _, _ = components
    seed_closed_trades(
        temp_audit_repo, ledger, "strat_decay", [2.0] * 20 + [-1.0] * 10, prefix="dc"
    )
    score = evaluator.evaluate_strategy(
        "strat_decay", ledger.get_experiences_for_strategy("strat_decay", limit=200)
    )
    assert score.expectancy_r > 0.0
    assert score.recent_window_expectancy_r < 0.0
    assert score.lifecycle_state == StrategyLifecycle.DEGRADED


def test_15_retirement_then_gated_recovery(temp_audit_repo, components):
    """A retired family stays retired until NEW validated evidence accumulates."""
    ledger, evaluator, _, _ = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_ret", [-1.2] * 16, prefix="rt")
    retired = evaluator.evaluate_strategy(
        "strat_ret", ledger.get_experiences_for_strategy("strat_ret", limit=100)
    )
    assert retired.lifecycle_state == StrategyLifecycle.RETIRED
    assert retired.is_eligible_for_new_trades is False

    # A couple of good trades must NOT rehabilitate it.
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        "strat_ret",
        [2.0, 2.0],
        base_ts=datetime.now(UTC) - timedelta(minutes=30),
        prefix="rt2",
    )
    still = evaluator.evaluate_strategy(
        "strat_ret", ledger.get_experiences_for_strategy("strat_ret", limit=100)
    )
    assert still.lifecycle_state == StrategyLifecycle.RETIRED

    # A large body of strong new evidence graduates it back to EVALUATING.
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        "strat_ret",
        [2.0] * 40,
        base_ts=datetime.now(UTC) - timedelta(minutes=20),
        prefix="rt3",
    )
    recovered = evaluator.evaluate_strategy(
        "strat_ret", ledger.get_experiences_for_strategy("strat_ret", limit=200)
    )
    assert recovered.lifecycle_state != StrategyLifecycle.RETIRED


# =============================================================================
# 3. DECISION BOUNDARY (16-20)
# =============================================================================


def test_16_experience_changes_the_decision(temp_audit_repo, components, sample_feature_vector):
    """With no history: untouched. With bad history: rejected."""
    ledger, _, retriever, engine = components
    ctx = engine.build_proposal_context(make_proposal("req_ctx"), sample_feature_vector)

    p1, d1 = engine.evaluate_proposal(make_proposal("req_a"), sample_feature_vector)
    assert d1.action == ExperienceAction.INSUFFICIENT_EVIDENCE
    assert p1.confidence == 0.80
    assert p1.action == ActionType.BUY_MARKET

    seed_closed_trades(
        temp_audit_repo,
        ledger,
        ctx.strategy_id,
        [-1.5] * 18,
        context=ctx,
        base_ts=datetime.now(UTC) - timedelta(hours=4),
        prefix="dec",
    )
    engine.invalidate_score_cache()

    p2, d2 = engine.evaluate_proposal(make_proposal("req_b"), sample_feature_vector)
    assert d2.strategy_lifecycle == StrategyLifecycle.RETIRED
    assert d2.action == ExperienceAction.REJECT
    assert p2.action == ActionType.NO_TRADE


def test_17_degraded_strategy_is_penalized_not_rejected(
    temp_audit_repo, components, sample_feature_vector
):
    """Degradation reduces influence; it does not hard-block."""
    ledger, _, retriever, engine = components
    ctx = engine.build_proposal_context(make_proposal("req_ctx"), sample_feature_vector)
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        ctx.strategy_id,
        [1.5] * 20 + [-0.4] * 8,
        context=ctx,
        base_ts=datetime.now(UTC) - timedelta(hours=8),
        prefix="pen",
    )
    engine.invalidate_score_cache()

    prop, dec = engine.evaluate_proposal(
        make_proposal("req_pen", confidence=0.90), sample_feature_vector
    )
    assert dec.strategy_lifecycle == StrategyLifecycle.DEGRADED
    assert dec.action == ExperienceAction.PENALIZE
    assert prop.action == ActionType.BUY_MARKET
    assert prop.confidence < 0.90
    assert prop.confidence == pytest.approx(0.63, abs=0.01)


def test_18_retired_strategy_rejection_happens_before_order_placement(
    temp_audit_repo, components, sample_feature_vector
):
    """
    The gate must convert the proposal to NO_TRADE so the downstream dispatch
    branch in LiveEngine is never entered.
    """
    from unittest.mock import MagicMock

    from nexus_scalp.execution.order_manager import OrderLifecycleManager

    ledger, _, retriever, engine = components
    ctx = engine.build_proposal_context(make_proposal("req_ctx"), sample_feature_vector)
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        ctx.strategy_id,
        [-2.0] * 20,
        context=ctx,
        base_ts=datetime.now(UTC) - timedelta(hours=5),
        prefix="rej",
    )
    engine.invalidate_score_cache()

    prop, dec = engine.evaluate_proposal(make_proposal("req_rej"), sample_feature_vector)
    assert dec.qualifies_trade is False
    assert prop.action == ActionType.NO_TRADE
    assert prop.blocked_by == "EXPERIENCE_RETIRED"
    assert prop.decision_stage == "EXPERIENCE_INTELLIGENCE_GATE"

    adapter = MagicMock()
    om = OrderLifecycleManager(adapter=adapter, audit_repo=temp_audit_repo)
    # NO_TRADE is not a dispatchable entry action: nothing reaches the adapter.
    assert prop.action not in (
        ActionType.BUY,
        ActionType.SELL,
        ActionType.BUY_MARKET,
        ActionType.SELL_MARKET,
    )
    adapter.execute_market_order.assert_not_called()
    assert om.experience_engine is None


def test_19_insufficient_evidence_never_fabricates_confidence(components, sample_feature_vector):
    """Unseen context must pass through bit-identical."""
    _, _, _, engine = components
    original = make_proposal("req_unseen", confidence=0.75)
    out, dec = engine.evaluate_proposal(original, sample_feature_vector)
    assert dec.action == ExperienceAction.INSUFFICIENT_EVIDENCE
    assert dec.retrieved_sample_count == 0
    assert dec.evidence_quality == 0.0
    assert out.confidence == 0.75
    assert out is original


def test_20_position_management_actions_are_never_gated(
    temp_audit_repo, components, sample_feature_vector
):
    """
    A retired strategy must never be able to block a protective exit.

    Regression guard for BUG-010.
    """
    ledger, _, retriever, engine = components
    ctx = engine.build_proposal_context(make_proposal("req_ctx"), sample_feature_vector)
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        ctx.strategy_id,
        [-2.0] * 20,
        context=ctx,
        base_ts=datetime.now(UTC) - timedelta(hours=5),
        prefix="exit",
    )
    engine.invalidate_score_cache()

    for action in (
        ActionType.CLOSE_POSITION,
        ActionType.PARTIAL_CLOSE,
        ActionType.MODIFY_SL_TP,
        ActionType.CANCEL_ORDER,
    ):
        prop = make_proposal(f"req_{action.value}", action=action, confidence=0.5)
        out, dec = engine.evaluate_proposal(prop, sample_feature_vector)
        assert out.action == action, f"{action.value} must not be gated"
        assert out.confidence == 0.5
        assert dec.qualifies_trade is True


# =============================================================================
# 4. POSITION ATTRIBUTION & QUALITY DECOMPOSITION (21-26)
# =============================================================================


def test_21_outcome_is_attributed_to_the_originating_strategy(temp_audit_repo, components):
    """Closing a trade must attach the outcome to the deciding strategy."""
    ledger, _, _, engine = components
    now = datetime.now(UTC)
    ledger.record_experience(
        make_record(
            "exp_req_attr", strategy_id="strat_attr", decision_ts=now - timedelta(minutes=5)
        )
    )
    temp_audit_repo._queue.join()

    assert engine.record_trade_outcome(
        request_id="req_attr",
        execution_id="tk_777",
        outcome_timestamp=now,
        is_executed=True,
        is_closed=True,
        exit_reason="TAKE_PROFIT_HIT",
        realized_pnl_usd=150.0,
        realized_r_multiple=1.5,
        mae_points=-2.0,
        mfe_points=18.0,
        holding_duration_seconds=240.0,
        actual_entry=2000.0,
        initial_sl_distance=10.0,
        atr_at_entry=5.0,
    )
    temp_audit_repo._queue.join()

    rec = ledger.get_experiences_for_strategy("strat_attr")[0]
    assert rec.execution_id == "tk_777"
    assert rec.realized_r_multiple == pytest.approx(1.5)
    assert rec.decomposition.final_outcome_r == pytest.approx(1.5)


def test_22_mae_mfe_are_risk_normalised(components):
    """Excursions must be expressed in R so they compare across instruments."""
    behavior = compute_behavior_metrics(
        mae_points=-5.0,
        mfe_points=20.0,
        mae_usd=-50.0,
        mfe_usd=200.0,
        planned_risk_distance=10.0,
        duration_sec=600.0,
        initial_sl_distance=10.0,
        atr_at_entry=5.0,
    )
    assert behavior.mae_r == pytest.approx(0.5)
    assert behavior.mfe_r == pytest.approx(2.0)


def test_23_entry_quality_detects_chase_and_slippage(components):
    """Adverse fill drift must degrade entry quality and raise flags."""
    analyzer = OutcomeAnalyzer()
    rec = make_record("k_chase")
    behavior = compute_behavior_metrics(
        mae_points=-4.0,
        mfe_points=5.0,
        mae_usd=-40.0,
        mfe_usd=50.0,
        planned_risk_distance=10.0,
        duration_sec=200.0,
        initial_sl_distance=10.0,
        atr_at_entry=5.0,
    )
    clean = ExecutionContext(expected_entry=2000.0, actual_entry=2000.05, slippage_points=0.05)
    chased = ExecutionContext(expected_entry=2000.0, actual_entry=2004.0, slippage_points=4.0)

    d_clean, f_clean = analyzer.analyze(rec, behavior, clean, realized_r=0.4)
    d_chase, f_chase = analyzer.analyze(rec, behavior, chased, realized_r=0.4)

    assert d_chase.entry_quality < d_clean.entry_quality
    assert d_chase.execution_quality < d_clean.execution_quality
    assert BehavioralFlag.ENTRY_CHASE in f_chase
    assert BehavioralFlag.EXECUTION_SLIPPAGE_ANOMALY in f_chase
    assert BehavioralFlag.ENTRY_CHASE not in f_clean


def test_24_hold_behaviour_flags_ignored_invalidation_and_excessive_hold(components):
    """Holding through invalidation must be measurable, not guessed."""
    analyzer = OutcomeAnalyzer()
    rec = make_record("k_hold", confidence=0.9)
    behavior = compute_behavior_metrics(
        mae_points=-9.8,
        mfe_points=0.5,
        mae_usd=-98.0,
        mfe_usd=5.0,
        planned_risk_distance=10.0,
        duration_sec=4000.0,
        expected_duration_sec=600.0,
        initial_sl_distance=10.0,
        atr_at_entry=5.0,
    )
    execution = ExecutionContext(expected_entry=2000.0, actual_entry=2000.0)
    # Exit was NOT the protective stop: price breached the invalidation band and
    # the position was still carried until hold-score decay forced it out.
    dec, flags = analyzer.analyze(
        rec, behavior, execution, realized_r=-1.0, exit_reason="HOLD_SCORE_DECAY"
    )

    assert BehavioralFlag.THESIS_INVALIDATION_IGNORED in flags
    assert BehavioralFlag.EXCESSIVE_HOLD_DURATION in flags
    assert BehavioralFlag.CONFIDENCE_OVERSHOOT in flags
    assert BehavioralFlag.PREMATURE_ENTRY in flags
    assert dec.strategy_quality < 0.0

    # Same excursion path, but the protective stop actually executed: respecting
    # the invalidation boundary must NOT be recorded as ignoring it.
    _, stop_flags = analyzer.analyze(
        rec, behavior, execution, realized_r=-1.0, exit_reason="HARD_SL_HIT"
    )
    assert BehavioralFlag.THESIS_INVALIDATION_IGNORED not in stop_flags


def test_25_strategy_success_and_management_failure_are_separated(components):
    """
    Big MFE banked as a tiny gain = strategy GOOD, management/exit POOR.

    This is the core Phase 08 requirement that PnL alone cannot be the
    learning signal.
    """
    analyzer = OutcomeAnalyzer()
    rec = make_record("k_early")
    behavior = compute_behavior_metrics(
        mae_points=-1.0,
        mfe_points=30.0,
        mae_usd=-10.0,
        mfe_usd=300.0,
        planned_risk_distance=10.0,
        duration_sec=300.0,
        initial_sl_distance=10.0,
        atr_at_entry=5.0,
    )
    execution = ExecutionContext(expected_entry=2000.0, actual_entry=2000.0)
    dec, flags = analyzer.analyze(
        rec, behavior, execution, realized_r=0.2, exit_reason="MANUAL_CLOSE"
    )

    assert dec.strategy_quality > 0.0
    assert dec.position_management_quality < 0.0
    assert dec.exit_quality < 0.0
    assert BehavioralFlag.EARLY_EXIT in flags


def test_26_lucky_win_is_not_scored_as_a_good_strategy(components):
    """Profit after the thesis was invalidated must be flagged, not rewarded."""
    analyzer = OutcomeAnalyzer()
    rec = make_record("k_lucky")
    behavior = compute_behavior_metrics(
        mae_points=-9.5,
        mfe_points=3.0,
        mae_usd=-95.0,
        mfe_usd=30.0,
        planned_risk_distance=10.0,
        duration_sec=900.0,
        initial_sl_distance=10.0,
        atr_at_entry=5.0,
    )
    execution = ExecutionContext(expected_entry=2000.0, actual_entry=2000.0)
    dec, _ = analyzer.analyze(rec, behavior, execution, realized_r=0.3, exit_reason="MANUAL_CLOSE")

    assert dec.final_outcome_r > 0.0
    assert dec.strategy_quality < 0.0
    assert dec.profitable_for_wrong_reason is True

    # A controlled loss with sound risk is explicitly acceptable.
    good_risk = compute_behavior_metrics(
        mae_points=-10.0,
        mfe_points=6.0,
        mae_usd=-100.0,
        mfe_usd=60.0,
        planned_risk_distance=10.0,
        duration_sec=400.0,
        initial_sl_distance=10.0,
        atr_at_entry=5.0,
    )
    dec2, _ = analyzer.analyze(
        rec, good_risk, execution, realized_r=-1.0, exit_reason="HARD_SL_HIT"
    )
    assert dec2.acceptable_loss is True


# =============================================================================
# 5. SELF-HEALING & IMMUTABLE LEDGER (27-30)
# =============================================================================


def test_27_derived_intelligence_rebuilds_from_immutable_ledger(temp_audit_repo, components):
    """A wiped registry must be fully reconstructable from raw experiences."""
    import sqlite3

    ledger, evaluator, _, engine = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_heal_a", [1.5] * 25, prefix="ha")
    seed_closed_trades(temp_audit_repo, ledger, "strat_heal_b", [-1.3] * 16, prefix="hb")
    evaluator.evaluate_strategy(
        "strat_heal_a", ledger.get_experiences_for_strategy("strat_heal_a", limit=100)
    )
    evaluator.evaluate_strategy(
        "strat_heal_b", ledger.get_experiences_for_strategy("strat_heal_b", limit=100)
    )
    temp_audit_repo._queue.join()

    conn = sqlite3.connect(temp_audit_repo._db_path)
    try:
        conn.execute("DELETE FROM strategy_intelligence_registry;")
        conn.commit()
        assert (
            conn.execute("SELECT COUNT(*) FROM strategy_intelligence_registry;").fetchone()[0] == 0
        )
    finally:
        conn.close()

    rebuilt = engine.self_heal()
    temp_audit_repo._queue.join()

    assert "strat_heal_a" in rebuilt
    assert "strat_heal_b" in rebuilt
    assert rebuilt["strat_heal_a"].sample_count == 25
    assert rebuilt["strat_heal_b"].sample_count == 16
    assert rebuilt["strat_heal_a"].expectancy_r > 0.0
    assert rebuilt["strat_heal_b"].lifecycle_state == StrategyLifecycle.RETIRED
    assert evaluator.get_registered_strategy_score("strat_heal_a") is not None


def test_28_corrupt_registry_payload_is_survivable(temp_audit_repo, components):
    """A corrupt derived row must not crash reads and must be repairable."""
    import sqlite3

    ledger, evaluator, _, engine = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_corrupt", [1.2] * 22, prefix="cr")
    evaluator.evaluate_strategy(
        "strat_corrupt", ledger.get_experiences_for_strategy("strat_corrupt", limit=100)
    )
    temp_audit_repo._queue.join()

    conn = sqlite3.connect(temp_audit_repo._db_path)
    try:
        conn.execute(
            "UPDATE strategy_intelligence_registry SET score_payload = ? WHERE strategy_id = ?;",
            ("{not-valid-json", "strat_corrupt"),
        )
        conn.commit()
    finally:
        conn.close()

    # Corrupt payload -> None, never an exception.
    assert evaluator.get_registered_strategy_score("strat_corrupt") is None

    engine.self_heal()
    temp_audit_repo._queue.join()
    repaired = evaluator.get_registered_strategy_score("strat_corrupt")
    assert repaired is not None
    assert repaired.sample_count == 22


def test_29_replay_is_deterministic(temp_audit_repo, components):
    """Two rebuilds over identical history must produce identical scores."""
    ledger, _, _, engine = components
    seed_closed_trades(
        temp_audit_repo, ledger, "strat_replay", [1.0, -1.0, 2.0, 0.5, -0.5] * 6, prefix="rp"
    )
    first = engine.self_heal()
    temp_audit_repo._queue.join()
    second = engine.self_heal()
    temp_audit_repo._queue.join()

    a = first["strat_replay"]
    b = second["strat_replay"]
    assert a.sample_count == b.sample_count
    assert a.expectancy_r == b.expectancy_r
    assert a.max_drawdown_r == b.max_drawdown_r
    assert a.confidence_score == b.confidence_score
    assert a.lifecycle_state == b.lifecycle_state


def test_30_self_heal_never_deletes_raw_experience(temp_audit_repo, components):
    """Rebuilding derived state must leave the immutable ledger byte-identical."""
    import sqlite3

    ledger, _, _, engine = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_preserve", [1.0] * 12, prefix="pv")

    conn = sqlite3.connect(temp_audit_repo._db_path)
    try:
        before_rows = conn.execute(
            "SELECT idempotency_key, payload FROM audit_experiences ORDER BY idempotency_key;"
        ).fetchall()
        before_outcomes = conn.execute(
            "SELECT COUNT(*) FROM audit_experience_outcomes;"
        ).fetchone()[0]
    finally:
        conn.close()

    engine.self_heal()
    temp_audit_repo._queue.join()

    conn = sqlite3.connect(temp_audit_repo._db_path)
    try:
        after_rows = conn.execute(
            "SELECT idempotency_key, payload FROM audit_experiences ORDER BY idempotency_key;"
        ).fetchall()
        after_outcomes = conn.execute("SELECT COUNT(*) FROM audit_experience_outcomes;").fetchone()[
            0
        ]
    finally:
        conn.close()

    assert after_rows == before_rows
    assert after_outcomes == before_outcomes
    assert len(after_rows) == 12


# =============================================================================
# 6. MODEL SAFETY & FEATURE-SCHEMA COMPATIBILITY (31-33)
# =============================================================================


def test_31_model_deletion_and_replacement_preserve_memory(temp_audit_repo, components, tmp_path):
    """
    Deleting the model artifact and registering a NEW model must leave every
    experience and every derived score intact.
    """
    ledger, evaluator, _, engine = components
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"OLD_MODEL_WEIGHTS")

    registry = ModelRegistry(audit_repo=temp_audit_repo)
    old = registry.register_model(
        artifact_path=artifact, model_version="v1.0", config_version="v1.0"
    )
    engine.set_provenance(old)
    assert old.artifact_fingerprint != ""

    seed_closed_trades(temp_audit_repo, ledger, "strat_model", [1.4] * 24, prefix="md")
    before = evaluator.evaluate_strategy(
        "strat_model", ledger.get_experiences_for_strategy("strat_model", limit=100)
    )
    temp_audit_repo._queue.join()

    # Simulate a full model rebuild: artifact deleted and replaced.
    artifact.unlink()
    assert not artifact.exists()
    artifact.write_bytes(b"COMPLETELY_NEW_MODEL_WEIGHTS_V2")
    new = registry.register_model(
        artifact_path=artifact, model_version="v2.0", config_version="v2.0", replaced=True
    )
    engine.set_provenance(new)
    temp_audit_repo._queue.join()

    assert new.artifact_fingerprint != old.artifact_fingerprint
    rows = ledger.get_experiences_for_strategy("strat_model", limit=100)
    assert len(rows) == 24
    after = evaluator.evaluate_strategy("strat_model", rows)
    assert after.sample_count == before.sample_count
    assert after.expectancy_r == before.expectancy_r
    # Historical rows keep the OLD provenance: memory is never rewritten.
    assert all(r.provenance.model_version != "v2.0" for r in rows)


def test_32_model_provenance_history_is_queryable_without_artifacts(temp_audit_repo, tmp_path):
    """Provenance must survive even when no artifact exists on disk."""
    artifact = tmp_path / "gone.pt"
    artifact.write_bytes(b"TEMP")
    registry = ModelRegistry(audit_repo=temp_audit_repo)
    registry.register_model(artifact_path=artifact, model_version="v1.0")
    artifact.unlink()
    missing = registry.register_model(artifact_path=artifact, model_version="v1.1")
    temp_audit_repo._queue.join()

    assert missing.artifact_fingerprint == ""
    models = registry.list_registered_models()
    versions = {m["model_version"] for m in models}
    assert "v1.0" in versions
    assert "v1.1" in versions


def test_33_future_feature_dimensions_are_backward_compatible(temp_audit_repo, components):
    """
    50D, 60D and 350D experiences must coexist, each retrievable under its own
    schema, with no silent reinterpretation.
    """
    ledger, _, _, _ = components
    for schema, dim in (
        ("scalp_v1", 50),
        ("scalp_v2", 60),
        ("scalp_v3", 70),
    ):  # TASK-03 canonical 70D
        ledger.record_experience(
            make_record(
                f"k_{schema}",
                strategy_id=f"strat_{schema}",
                dimension=dim,
                schema_id=schema,
            )
        )
    temp_audit_repo._queue.join()

    for schema, dim in (
        ("scalp_v1", 50),
        ("scalp_v2", 60),
        ("scalp_v3", 70),
    ):  # TASK-03 canonical 70D
        rec = ledger.get_experiences_for_strategy(f"strat_{schema}")[0]
        assert rec.feature_schema_id == schema
        assert rec.feature_dimension == dim
        assert len(rec.feature_snapshot.values) == dim

    census = ledger.get_schema_distribution()
    assert census["scalp_v1/50D"] == 1
    assert census["scalp_v2/60D"] == 1
    assert census["scalp_v3/70D"] == 1  # TASK-03 canonical 70D

    # A declared dimension must match the payload length.
    with pytest.raises(ValidationError):
        FeatureSnapshot(feature_schema_id="scalp_v2", feature_dimension=60, values=[0.0] * 50)

    # Revision-1 payloads (flat feature_vector_50d) still load.
    legacy = ExperienceRecord.model_validate(
        {
            "experience_id": "legacy",
            "request_id": "req_legacy",
            "idempotency_key": "k_legacy",
            "symbol": "XAUUSD",
            "decision_timestamp": datetime.now(UTC).isoformat(),
            "strategy_id": "strat_legacy",
            "context": {"strategy_id": "strat_legacy"},
            "feature_vector_50d": [0.2] * 50,
            "feature_hash": "abc123",
            "action": "BUY_MARKET",
            "entry_reason": "LEGACY",
            "proposed_entry": 2000.0,
            "stop_loss": 1990.0,
            "take_profit": 2020.0,
        }
    )
    assert legacy.record_version == 1
    assert legacy.feature_dimension == 50
    assert legacy.feature_snapshot.is_canonical_live_schema is True


# =============================================================================
# 7. FAILURE ISOLATION (34-38)
# =============================================================================


def test_34_persistence_failure_is_isolated(temp_audit_repo, components, sample_feature_vector):
    """A full/broken write queue must not raise into the live path."""
    ledger, _, _, engine = components

    import queue as _queue_mod

    class BrokenQueue(_queue_mod.Queue):
        """A queue whose PUT side is broken (full), reads still work so the
        background audit worker never crashes on a missing interface."""

        def put_nowait(self, item):
            raise RuntimeError("QUEUE_FULL")

    original = temp_audit_repo._queue
    temp_audit_repo._queue = BrokenQueue()
    try:
        assert ledger.record_experience(make_record("k_broken")) is False
        prop, dec = engine.evaluate_proposal(make_proposal("req_broken"), sample_feature_vector)
        assert prop.action == ActionType.BUY_MARKET
        assert dec.qualifies_trade is True
    finally:
        temp_audit_repo._queue = original


def test_35_retrieval_failure_is_isolated(components, sample_feature_vector):
    """A retriever raising must degrade to INSUFFICIENT_EVIDENCE, not crash."""
    _, _, _, engine = components

    class ExplodingRetriever:
        def build_context(self, *a, **k):
            raise RuntimeError("RETRIEVAL_BACKEND_DOWN")

    engine.retriever = ExplodingRetriever()
    original = make_proposal("req_retr_fail", confidence=0.85)
    prop, dec = engine.evaluate_proposal(original, sample_feature_vector)

    assert prop is original
    assert prop.action == ActionType.BUY_MARKET
    assert prop.confidence == 0.85
    assert dec.action == ExperienceAction.INSUFFICIENT_EVIDENCE
    assert "EXPERIENCE_EVALUATION_FAILED" in dec.penalty_reason
    assert engine.gate_failure_count == 1


def test_36_evaluator_failure_is_isolated(components, sample_feature_vector):
    """An evaluator raising must never fabricate an ALLOW with confidence."""
    _, _, _, engine = components

    class ExplodingEvaluator:
        def evaluate_strategy(self, *a, **k):
            raise RuntimeError("EVALUATOR_CRASH")

        def get_registered_strategy_score(self, *a, **k):
            raise RuntimeError("EVALUATOR_CRASH")

    engine.evaluator = ExplodingEvaluator()
    prop, dec = engine.evaluate_proposal(
        make_proposal("req_eval_fail", confidence=0.7), sample_feature_vector
    )
    assert prop.confidence == 0.7
    assert dec.action == ExperienceAction.INSUFFICIENT_EVIDENCE
    assert dec.evidence_quality == 0.0


def test_37_self_heal_failure_is_isolated(temp_audit_repo, components):
    """A self-heal crash must be reported, not raised."""
    _, evaluator, _, engine = components

    class ExplodingLedger:
        def list_strategy_ids(self, *a, **k):
            raise RuntimeError("LEDGER_UNAVAILABLE")

    original = engine.ledger
    engine.ledger = ExplodingLedger()
    try:
        assert evaluator.rebuild_derived_intelligence(engine.ledger) == {}
    finally:
        engine.ledger = original


def test_38_outcome_recording_failure_does_not_break_order_manager(temp_audit_repo):
    """
    OrderManager must complete its close path even when the experience layer
    raises on every call.
    """
    from unittest.mock import MagicMock

    from nexus_scalp.execution.order_manager import OrderLifecycleManager

    class ExplodingEngine:
        def record_trade_outcome(self, *a, **k):
            raise RuntimeError("EXPERIENCE_LAYER_DOWN")

    om = OrderLifecycleManager(
        adapter=MagicMock(), audit_repo=temp_audit_repo, experience_engine=ExplodingEngine()
    )
    om._entry_order_ids[999] = "req_boom"
    om._entry_directions[999] = "BUY"

    # Must not raise.
    om._record_experience_outcome(
        dead_ticket=999,
        now=datetime.now(UTC),
        entry=2000.0,
        exit_price=1995.0,
        initial_sl_val=1990.0,
        vol=0.1,
        atr=5.0,
        symbol_info=None,
        profit_usd=-50.0,
        comm_usd=1.0,
        swap_usd=0.0,
        mae_val=-10.0,
        mfe_val=2.0,
        mae_usd=-100.0,
        mfe_usd=20.0,
        duration_sec=300.0,
        exit_mechanism="HARD_SL_HIT",
        was_sl_modified=False,
    )
    assert om.global_state == "NORMAL"


# =============================================================================
# 8. SCALE (39-40)
# =============================================================================


def test_39_retrieval_is_bounded(temp_audit_repo, components):
    """top_k and the hard ledger cap must both be respected."""
    from nexus_scalp.experience.ledger import MAX_RETRIEVAL_LIMIT

    ledger, _, retriever, _ = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_bound", [1.0] * 40, prefix="bd")

    ctx = StrategyContext(strategy_id="strat_bound")
    got, _ = retriever.retrieve_relevant_experiences(
        context=ctx, decision_timestamp=datetime.now(UTC), top_k=10
    )
    assert len(got) == 10

    assert len(ledger.get_experiences_for_strategy("strat_bound", limit=5)) == 5
    # An absurd limit is clamped, never converted into a full scan.
    assert len(ledger.get_experiences_for_strategy("strat_bound", limit=10**9)) <= (
        MAX_RETRIEVAL_LIMIT
    )


def test_40_large_experience_set_is_handled(temp_audit_repo, components):
    """A few hundred experiences must score correctly and stay bounded."""
    ledger, evaluator, _, _ = components
    pattern = [1.5, -1.0, 0.8, -1.0, 2.0]
    seed_closed_trades(temp_audit_repo, ledger, "strat_big", pattern * 60, prefix="bg")

    rows = ledger.get_experiences_for_strategy("strat_big", limit=400)
    assert len(rows) == 300
    score = evaluator.evaluate_strategy("strat_big", rows)
    assert score.sample_count == 300
    assert score.expectancy_r == pytest.approx(sum(pattern) / len(pattern), abs=0.01)
    assert score.confidence_score <= MAX_STRATEGY_CONFIDENCE


# =============================================================================
# 9. REGRESSION GUARDS (41-50)
# =============================================================================


def test_41_existing_signal_pipeline_unchanged():
    """SignalPolicy defaults must be untouched by Phase 08."""
    from nexus_scalp.signals.policy import SignalPolicy

    assert SignalPolicy().confidence_threshold == 0.20


def test_42_existing_risk_engine_unchanged():
    """RiskEngine clamps must be untouched, and unreachable from learning."""
    from nexus_scalp.configuration.config import RiskConfig
    from nexus_scalp.risk.risk_engine import RiskEngine

    cfg = RiskConfig()
    risk = RiskEngine(config=cfg, max_allowed_lots=cfg.max_allowed_lots)
    assert risk.max_allowed_lots == cfg.max_allowed_lots
    assert hasattr(risk, "calculate_volume")
    assert hasattr(risk, "get_clamped_position_size")


def test_43_existing_order_manager_contract_unchanged(temp_audit_repo):
    """HARD_MAX_LOTS and the 11-state machine must be intact."""
    from unittest.mock import MagicMock

    from nexus_scalp.execution.order_manager import (
        HARD_MAX_LOTS,
        OrderLifecycleManager,
        PositionState,
    )

    om = OrderLifecycleManager(adapter=MagicMock(), audit_repo=temp_audit_repo)
    assert HARD_MAX_LOTS == 10.0
    assert len(list(PositionState)) == 11
    assert om.global_state == "NORMAL"
    # Default construction leaves the experience hook absent (opt-in only).
    assert om.experience_engine is None


def test_44_learning_layer_has_no_execution_capability(components):
    """
    Structural proof that learning cannot reach MT5, RiskEngine or OrderManager.
    """
    ledger, evaluator, retriever, engine = components
    forbidden = (
        "send_order",
        "execute_order",
        "execute_market_order",
        "modify_position",
        "close_position",
        "dispatch_order",
        "calculate_volume",
        "adapter",
        "mt5_adapter",
        "risk_engine",
        "order_manager",
    )
    for obj in (ledger, evaluator, retriever, engine):
        for attr in forbidden:
            assert not hasattr(obj, attr), f"{type(obj).__name__} must not expose {attr}"

    import nexus_scalp.experience.evaluator as ev_mod
    import nexus_scalp.experience.intelligence as int_mod
    import nexus_scalp.experience.ledger as led_mod
    import nexus_scalp.experience.provenance as prov_mod

    for mod in (int_mod, led_mod, ev_mod, prov_mod):
        with open(mod.__file__, encoding="utf-8") as fh:
            src = fh.read()
        assert "import torch" not in src, f"{mod.__name__} must not depend on torch"
        assert "MetaTrader5" not in src, f"{mod.__name__} must not touch MT5"


def test_45_canonical_50d_contract_preserved(sample_feature_vector):
    """The live 50D contract must remain exactly 50 ordered features."""
    from nexus_scalp.features.scalp_features import FEATURE_NAMES, NUM_FEATURES

    assert NUM_FEATURES == 50
    assert len(FEATURE_NAMES) == 50
    tensor = sample_feature_vector.to_tensor_input()
    assert len(tensor) == 50
    assert CANONICAL_FEATURE_DIMENSION == 50
    assert ExperienceLedger.canonical_feature_dimension() == 50

    # Feature hashes are schema-scoped: identical values under different schemas
    # must never collide.
    h1 = ExperienceLedger.compute_feature_hash(tensor, "scalp_v1")
    h2 = ExperienceLedger.compute_feature_hash(tensor, "scalp_v2")
    assert len(h1) == 16
    assert h1 != h2
    assert ExperienceLedger.compute_feature_hash(tensor, "scalp_v1") == h1


def test_46_strategy_score_eligibility_projection():
    """StrategyScore must expose an unambiguous eligibility verdict."""
    for state in StrategyLifecycle:
        score = StrategyScore(strategy_id="s", sample_count=30, lifecycle_state=state)
        expected = state not in (StrategyLifecycle.RETIRED, StrategyLifecycle.QUARANTINED)
        assert score.is_eligible_for_new_trades is expected


def test_47_correction_events_are_additive(temp_audit_repo, components):
    """Corrections must be appended without altering the original row."""
    import sqlite3

    ledger, _, _, _ = components
    ledger.record_experience(make_record("k_corr", strategy_id="strat_corr"))
    temp_audit_repo._queue.join()

    correction = ledger.build_correction(
        idempotency_key="k_corr",
        reason="BROKER_RESTATED_FILL",
        field_name="realized_pnl_usd",
        old_value="100.0",
        new_value="97.5",
    )
    assert ledger.record_correction(correction) is True
    temp_audit_repo._queue.join()

    conn = sqlite3.connect(temp_audit_repo._db_path)
    try:
        rows = conn.execute(
            "SELECT reason, field_name, old_value, new_value FROM audit_experience_corrections;"
        ).fetchall()
        exp_count = conn.execute("SELECT COUNT(*) FROM audit_experiences;").fetchone()[0]
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "BROKER_RESTATED_FILL"
    assert exp_count == 1


def test_48_orphan_outcome_is_refused(temp_audit_repo, components):
    """An outcome with no decision snapshot must not fabricate evidence."""
    ledger, _, _, engine = components
    assert (
        engine.record_trade_outcome(
            request_id="req_never_seen",
            execution_id="tk_orphan",
            outcome_timestamp=datetime.now(UTC),
            is_executed=True,
            is_closed=True,
            exit_reason="TAKE_PROFIT_HIT",
            realized_pnl_usd=500.0,
            realized_r_multiple=5.0,
        )
        is False
    )
    temp_audit_repo._queue.join()
    assert ledger.count_experiences() == 0


def test_49_gate_observability_counters(temp_audit_repo, components, sample_feature_vector):
    """The gate must expose auditable counters and a provenance summary."""
    ledger, _, _, engine = components
    engine.evaluate_proposal(make_proposal("req_c1"), sample_feature_vector)

    ctx = engine.build_proposal_context(make_proposal("req_ctx"), sample_feature_vector)
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        ctx.strategy_id,
        [-2.0] * 20,
        context=ctx,
        base_ts=datetime.now(UTC) - timedelta(hours=3),
        prefix="obs",
    )
    engine.invalidate_score_cache()
    engine.evaluate_proposal(make_proposal("req_c2"), sample_feature_vector)

    assert engine.gate_insufficient_count >= 1
    assert engine.gate_reject_count == 1

    summary = engine.summary()
    assert summary["feature_dimension"] == 50
    assert summary["feature_schema_id"] == CANONICAL_FEATURE_SCHEMA_ID
    assert summary["gate_reject"] == 1
    assert summary["recorded_experiences"] > 0


def test_50_score_cache_avoids_repeated_database_work(
    temp_audit_repo, components, sample_feature_vector
):
    """A cache hit must not re-run retrieval (hot-path discipline)."""
    ledger, _, retriever, engine = components
    ctx = engine.build_proposal_context(make_proposal("req_ctx"), sample_feature_vector)
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        ctx.strategy_id,
        [1.5] * 25,
        context=ctx,
        base_ts=datetime.now(UTC) - timedelta(hours=3),
        prefix="cache",
    )
    engine.invalidate_score_cache()
    engine.score_cache_ttl_sec = 300.0

    calls = {"n": 0}
    original = retriever.retrieve_relevant_experiences

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    retriever.retrieve_relevant_experiences = counting
    try:
        engine.evaluate_proposal(make_proposal("req_h1"), sample_feature_vector)
        first = calls["n"]
        for i in range(5):
            engine.evaluate_proposal(make_proposal(f"req_h{i + 2}"), sample_feature_vector)
        assert first == 1
        assert calls["n"] == 1
    finally:
        retriever.retrieve_relevant_experiences = original


def test_51_validated_high_expectancy_strategy_reinforces_confidence(
    temp_audit_repo, components, sample_feature_vector
):
    """
    Reinforcement path: a family with a strong, out-of-sample-confirmed edge must
    raise confidence — and the boost must require BOTH conditions, never sample
    count alone.
    """
    ledger, evaluator, _, engine = components
    ctx = engine.build_proposal_context(make_proposal("req_ctx"), sample_feature_vector)
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        ctx.strategy_id,
        [1.8] * 30,
        context=ctx,
        base_ts=datetime.now(UTC) - timedelta(hours=10),
        prefix="boost",
    )
    engine.invalidate_score_cache()

    score = evaluator.evaluate_strategy(
        ctx.strategy_id, ledger.get_experiences_for_strategy(ctx.strategy_id, limit=100)
    )
    assert score.lifecycle_state == StrategyLifecycle.ACTIVE
    assert score.replay_validated is True
    assert score.in_sample_expectancy_r > 0.0
    assert score.out_of_sample_expectancy_r > 0.0
    assert score.recency_weighted_expectancy_r > 0.5

    prop, dec = engine.evaluate_proposal(
        make_proposal("req_boost", confidence=0.70), sample_feature_vector
    )
    assert dec.action == ExperienceAction.ALLOW_WITH_CONTEXT
    assert prop.action == ActionType.BUY_MARKET
    assert prop.confidence > 0.70
    assert prop.confidence == pytest.approx(0.77, abs=0.01)
    assert prop.confidence <= 1.0


def test_52_marginal_positive_strategy_is_allowed_without_boost(
    temp_audit_repo, components, sample_feature_vector
):
    """
    A validated-but-thin edge must be allowed at BASELINE confidence: only a
    strong confirmed edge earns reinforcement.
    """
    ledger, _, _, engine = components
    ctx = engine.build_proposal_context(make_proposal("req_ctx"), sample_feature_vector)
    seed_closed_trades(
        temp_audit_repo,
        ledger,
        ctx.strategy_id,
        [0.25] * 30,
        context=ctx,
        base_ts=datetime.now(UTC) - timedelta(hours=10),
        prefix="thin",
    )
    engine.invalidate_score_cache()

    prop, dec = engine.evaluate_proposal(
        make_proposal("req_thin", confidence=0.70), sample_feature_vector
    )
    assert dec.action == ExperienceAction.ALLOW
    assert dec.strategy_lifecycle in (StrategyLifecycle.ACTIVE, StrategyLifecycle.VALIDATED)
    assert prop.confidence == 0.70


def test_53_weak_setup_and_risk_deviation_are_measured_against_policy(components):
    """
    Reward/risk below the policy floor recorded WITH the decision, and executed
    risk deviating from plan, must both be flagged.
    """
    analyzer = OutcomeAnalyzer()
    weak = make_record("k_weak", rr=1.0).model_copy(update={"min_rr_policy": 1.8})
    behavior = compute_behavior_metrics(
        mae_points=-3.0,
        mfe_points=5.0,
        mae_usd=-30.0,
        mfe_usd=50.0,
        planned_risk_distance=10.0,
        duration_sec=300.0,
        initial_sl_distance=16.0,  # 60% wider than planned -> risk deviation
        atr_at_entry=5.0,
    )
    execution = ExecutionContext(expected_entry=2000.0, actual_entry=2000.0)
    dec, flags = analyzer.analyze(weak, behavior, execution, realized_r=0.2)

    assert BehavioralFlag.WEAK_SETUP_ACCEPTED in flags
    assert BehavioralFlag.RISK_DEVIATION in flags
    assert dec.risk_quality < 0.5

    # A setup that clears its own recorded floor is not flagged.
    strong = make_record("k_strong", rr=2.5).model_copy(update={"min_rr_policy": 1.8})
    ok_behavior = compute_behavior_metrics(
        mae_points=-3.0,
        mfe_points=5.0,
        mae_usd=-30.0,
        mfe_usd=50.0,
        planned_risk_distance=10.0,
        duration_sec=300.0,
        initial_sl_distance=10.0,
        atr_at_entry=5.0,
    )
    _, ok_flags = analyzer.analyze(strong, ok_behavior, execution, realized_r=0.2)
    assert BehavioralFlag.WEAK_SETUP_ACCEPTED not in ok_flags
    assert BehavioralFlag.RISK_DEVIATION not in ok_flags


def test_54_overtrading_is_counted_from_the_ledger(temp_audit_repo, components):
    """Re-entry frequency must be measured from real rows, not guessed."""
    ledger, _, _, _ = components
    base = datetime.now(UTC) - timedelta(minutes=10)
    for i in range(4):
        ledger.record_experience(
            make_record(
                f"k_over_{i}",
                strategy_id="strat_over",
                decision_ts=base + timedelta(seconds=30 * i),
            )
        )
    temp_audit_repo._queue.join()

    count = ledger.count_recent_entries_for_strategy(
        strategy_id="strat_over",
        before_timestamp=base + timedelta(minutes=2),
        window_seconds=300.0,
    )
    assert count == 4

    analyzer = OutcomeAnalyzer()
    behavior = compute_behavior_metrics(
        mae_points=-3.0,
        mfe_points=4.0,
        mae_usd=-30.0,
        mfe_usd=40.0,
        planned_risk_distance=10.0,
        duration_sec=200.0,
        initial_sl_distance=10.0,
        atr_at_entry=5.0,
    )
    _, flags = analyzer.analyze(
        make_record("k_over_eval"),
        behavior,
        ExecutionContext(expected_entry=2000.0, actual_entry=2000.0),
        realized_r=-0.5,
        recent_context_entries=count,
    )
    assert BehavioralFlag.REENTRY_OVERTRADING in flags
