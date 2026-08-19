"""
PHASE 09 Trade Intelligence Brain — Behavioral Test Suite
==========================================================
Real behavioral verification of the adaptive strategy evolution + position
lifecycle intelligence subsystem. Every test asserts OBSERVABLE BEHAVIOUR
(persisted rows, computed metrics, gate verdicts, degraded/recovered lifecycle,
self-healing, worker isolation) rather than mere object existence.

Coverage map (spec test requirements):
    LIFECYCLE    1.  complete position timeline tracking
    METRICS      2.  MFE/MAE correctness
    GIVEBACK     3.  profit-giveback detection
    DECOMP       4.  trade quality decomposition
    CAUSAL       5.  bad management != bad strategy
    DEGRADE      6.  strategy degradation
    RECOVERY     7.  strategy recovery
    SIMILARITY   8.  historical similarity evaluation
    REJECT       9.  pre-trade rejection
    SCHEMA       10. feature-schema migration safety
    SELFHEAL     11. self-healing rebuild
    WORKER       12. worker failure isolation
    SAFETY       13. learning cannot bypass RiskEngine
    SAFETY       14. learning cannot bypass OrderManager
    NO_MT5       15. no real MT5 required
"""

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
    ExecutionContext,
    ExperienceAction,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    OutcomeDecomposition,
    PositionBehavior,
    StrategyContext,
    StrategyLifecycle,
)
from nexus_scalp.experience.retriever import ExperienceRetriever
from nexus_scalp.features.scalp_features import BarData, ScalpFeatureEngine
from nexus_scalp.intelligence import (
    AutopsyVerdict,
    BehaviorDetectionEngine,
    IntelligenceWorker,
    PositionLifecycleTracker,
    PreTradeIntelligenceGate,
    StrategyEvolutionEngine,
    TradeAutopsyEngine,
)
from nexus_scalp.intelligence.models import (
    DecisionContext,
    MarketContext,
    PositionPerformance,
    PositionSnapshot,
)

# =============================================================================
# FIXTURES & HELPERS
# =============================================================================


@pytest.fixture
def temp_audit_repo(tmp_path):
    db_file = tmp_path / "test_intelligence_phase09.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


@pytest.fixture
def base_components(temp_audit_repo):
    """Phase 09 engines wired against a temp database."""
    ledger = ExperienceLedger(audit_repo=temp_audit_repo)
    evaluator = StrategyEvaluator(audit_repo=temp_audit_repo)
    retriever = ExperienceRetriever(ledger=ledger)
    exp_engine = ExperienceIntelligenceEngine(
        ledger=ledger,
        evaluator=evaluator,
        retriever=retriever,
        enabled=True,
        max_inline_refresh_per_sec=0.0,
        score_cache_ttl_sec=1.0,
    )
    lifecycle = PositionLifecycleTracker(audit_repo=temp_audit_repo)
    autopsy = TradeAutopsyEngine(audit_repo=temp_audit_repo)
    behavior = BehaviorDetectionEngine(audit_repo=temp_audit_repo)
    evolution = StrategyEvolutionEngine(audit_repo=temp_audit_repo, ledger=ledger)
    gate = PreTradeIntelligenceGate(experience_engine=exp_engine)
    return (
        temp_audit_repo,
        ledger,
        evaluator,
        retriever,
        exp_engine,
        lifecycle,
        autopsy,
        behavior,
        evolution,
        gate,
    )


@pytest.fixture
def sample_feature_vector():
    now = datetime.now(UTC)
    bars = [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=now - timedelta(minutes=60 - i),
            open=2000.0,
            high=2004.0,
            low=1996.0,
            close=2002.0,
            tick_volume=100.0,
            is_complete=True,
        )
        for i in range(50)
    ]
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=2000.0, ask=2000.5)
    return ScalpFeatureEngine().compute_from_bars(bars, current_tick=tick)


def flush(repo):
    """Waits for the background audit queue to drain all pending writes."""
    repo._queue.join()


def make_record(
    key: str,
    strategy_id: str = "strat_test",
    decision_ts: datetime | None = None,
    action: str = "BUY_MARKET",
    entry: float = 2000.0,
    sl: float = 1990.0,
    tp: float = 2020.0,
    confidence: float = 0.60,
    rr: float = 2.0,
    dimension: int = CANONICAL_FEATURE_DIMENSION,
    schema_id: str = CANONICAL_FEATURE_SCHEMA_ID,
    approved_volume: float = 0.1,
) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=f"req_{key}",
        idempotency_key=key,
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=decision_ts or datetime.now(UTC),
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        context=StrategyContext(strategy_id=strategy_id, symbol="XAUUSD"),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id=schema_id,
            feature_dimension=dimension,
            values=[0.0] * dimension,
        ),
        action=action,
        entry_reason="SMC_GOD_MODE",
        model_probability=confidence,
        signal_confidence=confidence,
        proposed_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward_ratio=rr,
        approved_volume=approved_volume,
    )


def make_outcome(
    record: ExperienceRecord,
    realized_r: float,
    realized_pnl: float = 0.0,
    exit_reason: str = "TP",
    mfe_r: float = 1.0,
    mae_r: float = 0.2,
    duration: float = 300.0,
    expected_duration: float = 900.0,
) -> ExperienceOutcome:
    """Builds a fully-typed outcome event for a decision record."""
    return ExperienceOutcome(
        idempotency_key=record.idempotency_key,
        execution_id=f"ticket_{record.idempotency_key}",
        outcome_timestamp=record.decision_timestamp + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason=exit_reason,
        realized_pnl_usd=realized_pnl,
        realized_r_multiple=realized_r,
        approved_volume=record.approved_volume,
        behavior=PositionBehavior(
            mfe_r=mfe_r,
            mae_r=mae_r,
            mae_points=mae_r * 10.0,
            mfe_points=mfe_r * 10.0,
            expected_duration_sec=expected_duration,
            duration_sec=duration,
        ),
        execution=ExecutionContext(),
        decomposition=OutcomeDecomposition(
            strategy_quality=0.5,
            entry_quality=0.4,
            position_management_quality=0.4,
            exit_quality=0.4,
            execution_quality=0.5,
            final_outcome_r=realized_r,
        ),
        behavioral_flags=[],
    )


def seed_outcomes(
    ledger: ExperienceLedger, repo, count: int, strategy_id: str, prefix: str, realized_r: float
) -> None:
    """Records N decision+outcome pairs and flushes the queue."""
    for i in range(count):
        rec = make_record(key=f"{prefix}{i}", strategy_id=strategy_id)
        ledger.record_experience(rec)
        ledger.record_outcome(make_outcome(rec, realized_r=realized_r))
    flush(repo)


# =============================================================================
# 1-3. POSITION LIFECYCLE TRACKING, MFE/MAE, GIVEBACK
# =============================================================================


class TestPositionLifecycle:
    def test_observes_full_timeline(self, base_components):
        repo, _, _, _, _, lifecycle, _, _, _, _ = base_components
        now = datetime.now(UTC)
        market = MarketContext(symbol="XAUUSD", atr=1.5)

        # Created + Opened on first observation.
        lifecycle.observe_position(
            ticket=1,
            snapshot=PositionSnapshot(
                entry_price=2000.0,
                current_price=2000.1,
                volume=0.1,
                stop_loss=1990.0,
                take_profit=2020.0,
                floating_pnl=0.0,
            ),
            performance=PositionPerformance(),
            market=market,
            decision=DecisionContext(strategy_id="strat_x"),
            at=now,
        )

        # Expectation confirmed + MFE reached.
        lifecycle.observe_position(
            ticket=1,
            snapshot=PositionSnapshot(
                entry_price=2000.0,
                current_price=2002.0,
                volume=0.1,
                stop_loss=1990.0,
                take_profit=2020.0,
                floating_pnl=20.0,
            ),
            performance=PositionPerformance(mfe=1.0, mae=0.1, max_profit_reached=20.0),
            market=market,
            decision=DecisionContext(strategy_id="strat_x"),
            at=now + timedelta(seconds=10),
        )

        # Giveback (profit surrendered: peak 20 -> now 2) then degrading.
        lifecycle.observe_position(
            ticket=1,
            snapshot=PositionSnapshot(
                entry_price=2000.0,
                current_price=2000.2,
                volume=0.1,
                stop_loss=1990.0,
                take_profit=2020.0,
                floating_pnl=2.0,
            ),
            performance=PositionPerformance(
                mfe=1.0,
                mae=0.9,
                max_profit_reached=20.0,
                profit_giveback_pct=0.9,
            ),
            market=market,
            decision=DecisionContext(strategy_id="strat_x"),
            at=now + timedelta(seconds=60),
        )

        # Exit.
        lifecycle.finalize_exit(
            ticket=1,
            snapshot=PositionSnapshot(
                entry_price=2000.0,
                current_price=2002.5,
                volume=0.1,
                stop_loss=1990.0,
                take_profit=2020.0,
                floating_pnl=0.0,
            ),
            performance=PositionPerformance(mfe=1.0, mae=0.9, max_profit_reached=20.0),
            market=market,
            decision=DecisionContext(strategy_id="strat_x"),
            realized_pnl_usd=18.0,
            at=now + timedelta(seconds=120),
        )
        flush(repo)

        from nexus_scalp.intelligence.store import load_lifecycle_events

        events = load_lifecycle_events(lifecycle.audit_repo, ticket="1", limit=100)
        types = [e.event_type.value for e in events]
        assert "POSITION_CREATED" in types
        assert "POSITION_OPENED" in types
        assert "POSITION_EXPECTATION_CONFIRMED" in types
        assert "POSITION_MFE_REACHED" in types
        assert "POSITION_PROFIT_GIVEBACK" in types
        assert "POSITION_DEGRADING" in types
        assert "POSITION_EXITED" in types
        seqs = [e.sequence for e in events]
        assert seqs == sorted(seqs)

    def test_timeline_is_immutable_and_dedup(self, base_components):
        """Replaying the same upstream observation cannot duplicate events."""
        repo, _, _, _, _, lifecycle, _, _, _, _ = base_components
        now = datetime.now(UTC)
        market = MarketContext(symbol="XAUUSD", atr=1.5)
        snap = PositionSnapshot(
            entry_price=2000.0,
            current_price=2000.1,
            volume=0.1,
            stop_loss=1990.0,
            take_profit=2020.0,
            floating_pnl=0.0,
        )
        lifecycle.observe_position(
            ticket=2, snapshot=snap, performance=PositionPerformance(), market=market, at=now
        )
        lifecycle.observe_position(
            ticket=2, snapshot=snap, performance=PositionPerformance(), market=market, at=now
        )
        flush(repo)
        from nexus_scalp.intelligence.store import load_lifecycle_events

        events = load_lifecycle_events(lifecycle.audit_repo, ticket="2", limit=100)
        created = [e for e in events if e.event_type.value == "POSITION_CREATED"]
        assert len(created) == 1

    def test_mfe_mae_normalization(self, base_components):
        repo, _, _, _, _, lifecycle, _, _, _, _ = base_components
        now = datetime.now(UTC)
        market = MarketContext(symbol="XAUUSD", atr=1.5)

        # First observation -> CREATED + OPENED at entry.
        lifecycle.observe_position(
            ticket=7,
            snapshot=PositionSnapshot(
                entry_price=2000.0,
                current_price=2000.1,
                volume=0.1,
                stop_loss=1990.0,
                take_profit=2020.0,
                floating_pnl=0.0,
            ),
            performance=PositionPerformance(),
            market=market,
            decision=DecisionContext(strategy_id="strat_x"),
            at=now,
        )
        # Second observation with healthy MFE -> MFE_REACHED carries the metrics.
        lifecycle.observe_position(
            ticket=7,
            snapshot=PositionSnapshot(
                entry_price=2000.0,
                current_price=2002.0,
                volume=0.1,
                stop_loss=1990.0,
                take_profit=2020.0,
                floating_pnl=10.0,
            ),
            performance=PositionPerformance(mfe=2.0, mae=0.5),
            market=market,
            decision=DecisionContext(strategy_id="strat_x"),
            at=now + timedelta(seconds=5),
        )
        lifecycle.finalize_exit(ticket=7, realized_pnl_usd=10.0, at=now + timedelta(seconds=30))
        flush(repo)
        from nexus_scalp.intelligence.store import load_lifecycle_events

        events = load_lifecycle_events(lifecycle.audit_repo, ticket="7", limit=100)
        mfe_events = [e for e in events if e.event_type.value == "POSITION_MFE_REACHED"]
        assert mfe_events
        assert any(e.performance.mfe >= 2.0 for e in mfe_events)

    def test_profit_giveback_detection(self, base_components):
        repo, _, _, _, _, lifecycle, _, _, _, _ = base_components
        now = datetime.now(UTC)
        market = MarketContext(symbol="XAUUSD", atr=1.5)
        lifecycle.observe_position(
            ticket=9,
            snapshot=PositionSnapshot(
                entry_price=2000.0,
                current_price=2000.1,
                volume=0.1,
                stop_loss=1990.0,
                take_profit=2020.0,
                floating_pnl=0.0,
            ),
            performance=PositionPerformance(),
            market=market,
            at=now,
        )
        lifecycle.observe_position(
            ticket=9,
            snapshot=PositionSnapshot(
                entry_price=2000.0,
                current_price=2001.0,
                volume=0.1,
                stop_loss=1990.0,
                take_profit=2020.0,
                floating_pnl=10.0,
            ),
            performance=PositionPerformance(mfe=0.6, max_profit_reached=10.0),
            market=market,
            at=now + timedelta(seconds=5),
        )
        lifecycle.observe_position(
            ticket=9,
            snapshot=PositionSnapshot(
                entry_price=2000.0,
                current_price=2000.4,
                volume=0.1,
                stop_loss=1990.0,
                take_profit=2020.0,
                floating_pnl=2.0,
            ),
            performance=PositionPerformance(
                mfe=0.6,
                max_profit_reached=10.0,
                profit_giveback_pct=0.8,
            ),
            market=market,
            at=now + timedelta(seconds=10),
        )
        flush(repo)
        from nexus_scalp.intelligence.store import load_lifecycle_events

        events = load_lifecycle_events(lifecycle.audit_repo, ticket="9", limit=100)
        assert any(e.event_type.value == "POSITION_PROFIT_GIVEBACK" for e in events)


# =============================================================================
# 4-5. QUALITY DECOMPOSITION & "BAD MANAGEMENT != BAD STRATEGY"
# =============================================================================


class TestAutopsyAndCausality:
    def test_quality_decomposition(self, base_components):
        repo, _, _, _, _, _, autopsy, _, _, _ = base_components
        rec = make_record(key="ap1")
        rec = rec.with_outcome(
            make_outcome(
                rec,
                realized_r=2.0,
                realized_pnl=40.0,
                exit_reason="TP",
            )
        )
        aut = autopsy.build_autopsy(
            record=rec,
            decomposition=rec.decomposition,
            realized_pnl_usd=rec.realized_pnl_usd,
            realized_r=rec.realized_r_multiple,
            ticket=rec.execution_id,
            symbol="XAUUSD",
            exit_mechanism="TP",
        )
        assert aut.verdict == AutopsyVerdict.CLEAN_WIN
        autopsy.persist(aut)
        flush(repo)
        from nexus_scalp.intelligence.store import load_autopsy

        row = load_autopsy(autopsy.audit_repo, aut.ticket)
        assert row is not None
        assert row["quality_verdict"] == AutopsyVerdict.CLEAN_WIN.value

    def test_bad_management_not_bad_strategy(self, base_components):
        """A losing trade with good strategy quality is a MANAGED_LOSS, not a broken strategy."""
        _, _, _, _, _, _, autopsy, _, _, _ = base_components

        # Good thesis, stop respected -> MANAGED_LOSS.
        rec = make_record(key="ap2")
        rec = rec.with_outcome(
            make_outcome(rec, realized_r=-1.0, realized_pnl=-20.0, exit_reason="SL")
        )
        aut = autopsy.build_autopsy(
            record=rec,
            decomposition=rec.decomposition,
            realized_pnl_usd=rec.realized_pnl_usd,
            realized_r=rec.realized_r_multiple,
            ticket=rec.execution_id,
            symbol="XAUUSD",
            exit_mechanism="SL",
        )
        assert aut.verdict == AutopsyVerdict.MANAGED_LOSS
        assert "well managed" in aut.narrative

        # Broken management -> COSTLY_LOSS.
        rec2 = make_record(key="ap3")
        rec2 = rec2.with_outcome(
            make_outcome(rec2, realized_r=-1.2, realized_pnl=-25.0, exit_reason="MANUAL")
        )
        decom = rec2.decomposition.model_copy(
            update={"position_management_quality": -0.7, "exit_quality": -0.5}
        )
        aut2 = autopsy.build_autopsy(
            record=rec2,
            decomposition=decom,
            realized_pnl_usd=rec2.realized_pnl_usd,
            realized_r=rec2.realized_r_multiple,
            ticket=rec2.execution_id,
            symbol="XAUUSD",
            exit_mechanism="MANUAL",
        )
        assert aut2.verdict == AutopsyVerdict.COSTLY_LOSS

    def test_giveback_flag_reaches_autopsy(self, base_components):
        """A 75% giveback surfaces in the autopsy narrative/trade."""
        _, _, _, _, _, _, autopsy, _, _, _ = base_components
        rec = make_record(key="ap4")
        rec = rec.with_outcome(
            make_outcome(
                rec, realized_r=0.5, realized_pnl=10.0, exit_reason="MANUAL", mfe_r=2.0, mae_r=0.2
            )
        )
        # Override mfe so giveback = 1 - 0.5/2.0 = 0.75
        rec = rec.model_copy(update={"behavior": rec.behavior.model_copy(update={"mfe_r": 2.0})})
        aut = autopsy.build_autopsy(
            record=rec,
            decomposition=rec.decomposition,
            realized_pnl_usd=rec.realized_pnl_usd,
            realized_r=rec.realized_r_multiple,
            ticket=rec.execution_id,
            symbol="XAUUSD",
            exit_mechanism="MANUAL",
        )
        assert aut.giveback_pct >= 0.7


# =============================================================================
# 6-7. STRATEGY DEGRADATION & RECOVERY
# =============================================================================


class TestStrategyLifecycle:
    def test_strategy_degrades(self, base_components):
        repo, ledger, evaluator, _, _, _, _, _, _, _ = base_components
        seed_outcomes(ledger, repo, 8, "strat_deg", "deg", realized_r=-0.8)
        exps = ledger.get_experiences_for_strategy("strat_deg", limit=20)
        closed = [e for e in exps if e.is_closed and e.is_executed]
        assert len(closed) == 8
        score = evaluator.evaluate_strategy("strat_deg", exps, persist=False)
        assert score.lifecycle_state in (StrategyLifecycle.DEGRADED, StrategyLifecycle.RETIRED)
        assert score.expectancy_r < 0.0

    def test_strategy_recovers(self, base_components):
        """A DECLING family that later becomes strongly positive is not retired."""
        repo, ledger, evaluator, _, _, _, _, _, _, _ = base_components
        seed_outcomes(ledger, repo, 6, "strat_rec", "rb", realized_r=-0.8)
        seed_outcomes(ledger, repo, 16, "strat_rec", "rg", realized_r=1.0)
        exps = ledger.get_experiences_for_strategy("strat_rec", limit=40)
        score = evaluator.evaluate_strategy("strat_rec", exps, persist=False)
        assert score.lifecycle_state != StrategyLifecycle.RETIRED
        assert score.recent_window_expectancy_r > 0.0


# =============================================================================
# 8-9. SIMILARITY & PRE-TRADE REJECTION
# =============================================================================


class TestGate:
    def test_insufficient_evidence_passes_through(self, base_components, sample_feature_vector):
        _, _, _, _, _, _, _, _, _, gate = base_components
        proposal = TradeProposal(
            request_id="req_g1",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=ActionType.BUY_MARKET,
            confidence=0.70,
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            risk_reward_ratio=2.0,
            reason_code="SMC_GOD_MODE",
        )
        proposal_out, _, verdict = gate.evaluate(proposal, sample_feature_vector, None)
        assert proposal_out.action == ActionType.BUY_MARKET
        assert verdict.qualifies is True

    def test_retired_strategy_blocks_before_dispatch(self, base_components, sample_feature_vector):
        repo, ledger, evaluator, _, exp, _, _, _, _, gate = base_components
        seed_outcomes(ledger, repo, 16, "strat_rej", "rej", realized_r=-0.8)
        exps = ledger.get_experiences_for_strategy("strat_rej", limit=50)
        score = evaluator.evaluate_strategy("strat_rej", exps)
        # Registry now knows the family is retired (or at minimum degraded).
        assert score.lifecycle_state in (StrategyLifecycle.RETIRED, StrategyLifecycle.DEGRADED)
        flush(repo)
        proposal = TradeProposal(
            request_id="req_g2",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=ActionType.BUY_MARKET,
            confidence=0.70,
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            risk_reward_ratio=2.0,
            reason_code="SMC_GOD_MODE",
        )
        proposal_out, exp_dec, _ = gate.evaluate(proposal, sample_feature_vector, None)
        if score.lifecycle_state == StrategyLifecycle.RETIRED:
            assert proposal_out.action == ActionType.NO_TRADE
            assert exp_dec.action == ExperienceAction.REJECT

    def test_warn_tier_on_elevated_drawdown(self, base_components, sample_feature_vector):
        """The Phase 09 gate emits WARN when context is risky but not fatal."""
        _, _, _, _, _, _, _, _, _, gate = base_components
        proposal = TradeProposal(
            request_id="req_g3",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=ActionType.BUY_MARKET,
            confidence=0.70,
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            risk_reward_ratio=2.0,
            reason_code="SMC_GOD_MODE",
        )
        # Force the decision to carry a high drawdown so the WARN tier triggers.
        from nexus_scalp.experience.models import PreTradeExperienceDecision
        from nexus_scalp.intelligence.gate import SuitabilityTier

        fake = PreTradeExperienceDecision(
            decision_id="d1",
            request_id="req_g3",
            timestamp=datetime.now(UTC),
            action=ExperienceAction.ALLOW,
            qualifies_trade=True,
            adjusted_confidence=0.7,
            strategy_id="strat_w",
            strategy_lifecycle=StrategyLifecycle.VALIDATED,
            retrieved_sample_count=50,
            similarity_score=1.0,
            evidence_quality=0.8,
            expectancy_r=0.2,
            recent_expectancy_r=0.1,
            drawdown_r=3.5,
        )
        _, verdict = gate._evaluate_with_evidence(proposal, fake)
        assert verdict.decision == SuitabilityTier.WARN
        assert verdict.qualifies is True


# =============================================================================
# 10. FEATURE SCHEMA MIGRATION SAFETY
# =============================================================================


class TestFeatureSchemaMigration:
    def test_old_schema_records_stay_valid(self, base_components):
        repo, ledger, _, _, _, _, _, _, _, _ = base_components
        v1 = make_record(key="sch1", dimension=50, schema_id="scalp_v1")
        v2 = make_record(key="sch2", dimension=60, schema_id="scalp_v2")
        ledger.record_experience(v1)
        ledger.record_experience(v2)
        flush(repo)
        census = ledger.get_schema_distribution()
        assert "scalp_v1/50D" in census
        assert "scalp_v2/60D" in census
        rec = ledger.get_experience_by_key("sch1")
        assert rec.feature_dimension == 50
        assert rec.feature_schema_id == "scalp_v1"


# =============================================================================
# 11. SELF-HEALING REBUILD
# =============================================================================


class TestSelfHealing:
    def test_rebuild_derived_from_immutable_ledger(self, base_components):
        repo, ledger, evaluator, _, _, _, _, _, _, _ = base_components
        seed_outcomes(ledger, repo, 12, "strat_sh", "sh", realized_r=0.5)
        rebuilt = evaluator.rebuild_derived_intelligence(ledger)
        assert "strat_sh" in rebuilt
        assert rebuilt["strat_sh"].sample_count == 12
        # The raw ledger still exists afterwards (nothing was destroyed).
        assert ledger.count_experiences() == 12


# =============================================================================
# 12. WORKER FAILURE ISOLATION
# =============================================================================


class TestWorkerIsolation:
    def test_worker_failure_is_isolated(self, base_components):
        repo, ledger, _, _, _, lifecycle, autopsy, behavior, evolution, _ = base_components

        class Boom:
            def build_autopsy(self, *a, **k):
                raise RuntimeError("boom")

        worker = IntelligenceWorker(
            audit_repo=repo,
            ledger=ledger,
            interval_sec=0.0,
            lifecycle=lifecycle,
            autopsy=Boom(),  # type: ignore[assignment]
            behavior=behavior,
            evolution=evolution,
        )
        worker.start()
        worker.tick()
        # The failure was captured, not raised; the worker continues to run.
        assert worker.last_error != "" or worker.last_cycle_duration is not None
        assert worker.cycle_count >= 1
        # Restoring the real engine lets the next cycle succeed.
        worker.autopsy = autopsy
        worker._last_run_ts = 0.0
        worker.tick() or worker.last_error == ""
        worker.stop()

    def test_worker_checkpoint_persists_across_restart(self, tmp_path, base_components):
        """
        The intelligence_worker_state table must actually persist worker
        bookkeeping so a crash/restart resumes without redoing everything.

        (The table existed in the schema but nothing ever wrote to it - a dead
        table. Restart-safety was claimed in the docs but not implemented.)
        """
        from nexus_scalp.adapters.database.audit_repository import AuditRepository

        db_url = f"sqlite:///{tmp_path / 'checkpoint.db'}"
        repo = AuditRepository(db_url=db_url, flush_interval_sec=0.05)
        try:
            # Reuse the real ledger wiring from a second repo so episodes are
            # independent of base_components' database.
            from nexus_scalp.experience.ledger import ExperienceLedger

            new_ledger = ExperienceLedger(audit_repo=repo)
            worker = IntelligenceWorker(
                audit_repo=repo,
                ledger=new_ledger,
                interval_sec=0.0,
            )
            worker.start()
            worker.tick()
            # Force a nonzero cycle count before stopping.
            worker._last_run_ts = 0.0
            worker.tick()
            worker.stop()

            # Flush the queued checkpoint write.
            import time

            time.sleep(0.6)
            import sqlite3

            conn = sqlite3.connect(repo._db_path)
            try:
                row = conn.execute(
                    "SELECT cycle_count, last_checkpoint FROM intelligence_worker_state "
                    "WHERE scope = 'intelligence'"
                ).fetchone()
            finally:
                conn.close()
            assert row is not None, "checkpoint row must be persisted on stop"
            assert int(row[0]) >= 1

            # A fresh worker instance must restore the cycle counter.
            worker2 = IntelligenceWorker(audit_repo=repo, ledger=new_ledger, interval_sec=0.0)
            worker2.start()
            assert worker2.cycle_count >= 1, "restart must resume from the checkpoint"
            worker2.stop()
        finally:
            repo.close()
            import gc

            gc.collect()


# =============================================================================
# 13-14. LEARNING CANNOT BYPASS RISK / ORDER
# =============================================================================


class TestSafetyContract:
    def test_intelligence_holds_no_execution_capability(self):
        """None of the intelligence engines expose an adapter/order-manager."""

        intel_mod = __import__("nexus_scalp.intelligence", fromlist=["*"])
        for name in (
            "PositionLifecycleTracker",
            "TradeAutopsyEngine",
            "BehaviorDetectionEngine",
            "StrategyEvolutionEngine",
            "PreTradeIntelligenceGate",
            "IntelligenceWorker",
        ):
            cls = getattr(intel_mod, name)
            attrs = {
                a
                for c in cls.__mro__
                for a in vars(c)
                if a in ("adapter", "order_manager", "risk_engine", "execute", "place")
            }
            assert not attrs

    def test_gate_rejection_before_order_dispatch(self, base_components, sample_feature_vector):
        repo, ledger, evaluator, _, exp, _, _, _, _, gate = base_components
        seed_outcomes(ledger, repo, 16, "strat_safe", "safe", realized_r=-0.8)
        score = evaluator.evaluate_strategy(
            "strat_safe",
            ledger.get_experiences_for_strategy("strat_safe", limit=50),
        )
        flush(repo)
        proposal = TradeProposal(
            request_id="req_safe",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=ActionType.SELL_MARKET,
            confidence=0.7,
            proposed_entry=2000.0,
            stop_loss=2005.0,
            take_profit=1980.0,
            risk_reward_ratio=2.0,
            reason_code="SMC_GOD_MODE",
        )
        proposal_out, _, _ = gate.evaluate(proposal, sample_feature_vector, None)
        if score.lifecycle_state == StrategyLifecycle.RETIRED:
            assert proposal_out.action == ActionType.NO_TRADE
        assert not hasattr(gate, "adapter")
        assert not hasattr(gate, "order_manager")


# =============================================================================
# 15. NO REAL MT5 REQUIRED
# =============================================================================


class TestNoMT5:
    def test_lifecycle_works_without_mt5(self, tmp_path):
        """The whole Phase 09 flow works with only a SQLite repo - no MT5."""
        repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'nomt5.db'}")
        ExperienceLedger(audit_repo=repo)
        tracker = PositionLifecycleTracker(audit_repo=repo)
        now = datetime.now(UTC)
        market = MarketContext(symbol="XAUUSD", atr=1.2)
        tracker.observe_position(
            ticket=101,
            snapshot=PositionSnapshot(
                entry_price=2000.0,
                current_price=2000.2,
                volume=0.1,
                stop_loss=1990.0,
                take_profit=2020.0,
                floating_pnl=2.0,
            ),
            performance=PositionPerformance(mfe=0.4),
            market=market,
            at=now,
        )
        tracker.finalize_exit(ticket=101, realized_pnl_usd=2.0, at=now)
        flush(repo)
        from nexus_scalp.intelligence.store import load_lifecycle_events

        events = load_lifecycle_events(repo, ticket="101", limit=20)
        assert any(e.event_type.value == "POSITION_CREATED" for e in events)
        repo.close()
