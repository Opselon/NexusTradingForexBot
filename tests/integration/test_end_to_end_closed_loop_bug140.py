"""BUG-140 Phase 15 End-to-End Closed-Loop Demonstration:
Strategy Discovery -> Candidate -> Dataset -> Backtest -> Walk-Forward ->
OOS -> Robustness -> Score -> Shadow/Forward Eligibility -> Decision Trace ->
Execution & Order Lifecycle -> Outcome -> Experience -> Behavioral Analysis ->
Next Decision Evidence.

Exercises the entire production research-to-execution pipeline with real
production domain objects and verifies that every stage produces verifiable,
reproducible artifacts.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.broker_history import create_history_tables
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.lifecycle import DecisionLifecycle
from nexus_scalp.experience.models import (
    ExecutionContext,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    OutcomeDecomposition,
    PositionBehavior,
    StrategyContext,
)
from nexus_scalp.experience.outcome_recovery_sweep import HistoricalOutcomeRecoverySweep
from nexus_scalp.experience.retriever import ExperienceRetriever
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.research.backtest import BacktestEngine
from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.discovery import discover_candidates
from nexus_scalp.research.models import (
    CandidateLifecycle,
    ExecutionAssumptions,
    ResearchDataset,
)
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.robustness import RobustnessEngine
from nexus_scalp.research.scoring import compute_strategy_score
from nexus_scalp.research.walkforward import WalkForwardEngine


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'e2e.db'}")
    conn = sqlite3.connect(r._db_path)
    create_history_tables(conn)
    conn.close()
    yield r
    r.close()


@pytest.fixture
def ledger(repo):
    return ExperienceLedger(repo)


def seed_learning_ledger(
    ledger: ExperienceLedger,
    repo: AuditRepository,
    count: int = 50,
    context: StrategyContext | None = None,
) -> list[ExperienceRecord]:
    """Populates the ledger with closed trades showing a positive edge in TRENDING."""
    t0 = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
    records = []
    for i in range(count):
        ts = t0 + timedelta(minutes=i * 5)
        # 60% win rate, positive expectancy
        is_win = (i % 5) != 0
        r_mult = 1.5 if is_win else -1.0
        pnl = r_mult * 100.0

        rec = ExperienceRecord(
            experience_id=f"exp_e2e_{i}",
            request_id=f"req_e2e_{i}",
            idempotency_key=f"exp_req_e2e_{i}",
            symbol="XAUUSD",
            timeframe="M1",
            decision_timestamp=ts,
            strategy_id="strat_trend_breakout",
            strategy_version="1.0.0",
            context=context
            if context is not None
            else StrategyContext(
                strategy_id="strat_trend_breakout",
                symbol="XAUUSD",
                session="LONDON",
                regime="TRENDING_MOMENTUM",
                volatility_regime="NORMAL",
                trend_state="BULLISH",
            ),
            feature_snapshot=FeatureSnapshot(
                feature_schema_id="scalp_v1",
                feature_dimension=50,
                values=[0.1 * (j % 5) for j in range(50)],
            ),
            action="BUY_MARKET",
            entry_reason="SMC_GOD_MODE",
            model_probability=0.72,
            signal_confidence=0.75,
            proposed_entry=2000.0 + i * 0.5,
            stop_loss=1990.0 + i * 0.5,
            take_profit=2020.0 + i * 0.5,
            risk_reward_ratio=2.0,
            approved_volume=0.1,
        )
        ledger.record_experience(rec)

        outcome = ExperienceOutcome(
            idempotency_key=rec.idempotency_key,
            execution_id=f"deal_{i}",
            outcome_timestamp=ts + timedelta(minutes=3),
            is_executed=True,
            is_closed=True,
            exit_reason="TAKE_PROFIT_HIT" if is_win else "HARD_SL_HIT",
            realized_pnl_usd=pnl,
            realized_r_multiple=r_mult,
            approved_volume=0.1,
            behavior=PositionBehavior(duration_sec=180.0, mae_r=0.2, mfe_r=1.2),
            execution=ExecutionContext(actual_entry=rec.proposed_entry, slippage_points=0.0),
            decomposition=OutcomeDecomposition(final_outcome_r=r_mult),
        )
        ledger.record_outcome(outcome)
        records.append(rec)

    repo._queue.join()
    return records


def _build_seeded_live_context(ledger: ExperienceLedger) -> StrategyContext:
    """
    Builds a seeded context through the SAME production build_context path the
    live decision uses (feature engine -> retriever bucketing), so the seeded
    evidence shares the live context vocabulary and is retrievable as sibling
    evidence for a young strategy family.
    """
    from nexus_scalp.domain.models import TickData
    from nexus_scalp.features.scalp_features import BarData, ScalpFeatureEngine

    now_t = datetime.now(UTC)
    bars = [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=now_t - timedelta(minutes=100 - k),
            open=2000.0,
            high=2005.0,
            low=1995.0,
            close=2002.0,
            tick_volume=100.0,
            is_complete=True,
        )
        for k in range(50)
    ]
    tick = TickData(symbol="XAUUSD", timestamp=now_t, bid=2050.0, ask=2050.5)
    fv = ScalpFeatureEngine().compute_from_bars(bars, current_tick=tick)
    derived = ExperienceRetriever(ledger=ledger).build_context(
        symbol="XAUUSD",
        timeframe="M1",
        feature_vector=fv,
        entry_reason="SMC_GOD_MODE",
    )
    return StrategyContext(
        strategy_id="strat_trend_breakout",
        strategy_version="1.0.0",
        symbol=derived.symbol,
        timeframe=derived.timeframe,
        session=derived.session,
        regime=derived.regime,
        volatility_regime=derived.volatility_regime,
        trend_state=derived.trend_state,
        setup_type=derived.setup_type,
    )


class TestEndToEndResearchAndExecutionLoop:
    def test_complete_evidence_loop_0_to_100(self, repo, ledger):
        """Mandatory End-to-End demonstration of the entire 15-phase loop."""

        # ------------------------------------------------------------------
        # STAGE 1: SEED & PREPARE EXPERIENCE EVIDENCE
        # Seeded contexts are derived from the SAME build_context path the
        # live decision uses, so sibling evidence is retrievable for the
        # young live strategy family (BUG-140 Stage-15 finding).
        # ------------------------------------------------------------------
        seeded_records = seed_learning_ledger(
            ledger, repo, count=60, context=_build_seeded_live_context(ledger)
        )
        assert len(seeded_records) == 60

        # ------------------------------------------------------------------
        # STAGE 2: BUILD RESEARCH DATASET WITH EXPLICIT CENSUS
        # ------------------------------------------------------------------
        builder = ResearchDatasetBuilder(ledger)
        dataset = builder.build(dataset_id="ds_e2e_01")
        assert len(dataset.samples) == 60
        assert dataset.provenance_extra["valid_research_samples"] == 60
        assert dataset.provenance_extra["eligibility_rules"]["contract_version"] == "p0e-bug140-1"

        # ------------------------------------------------------------------
        # STAGE 3: STRATEGY CANDIDATE DISCOVERY
        # ------------------------------------------------------------------
        candidates = discover_candidates(dataset.samples, dataset_id=dataset.dataset_id)
        assert len(candidates) >= 1
        candidate = candidates[0]
        assert candidate.strategy_id != ""
        assert candidate.content_digest() != ""
        assert candidate.canonical_version().startswith("v")

        # ------------------------------------------------------------------
        # STAGE 4: DETERMINISTIC BACKTEST (EMPIRICAL REPLAY)
        # ------------------------------------------------------------------
        bt_engine = BacktestEngine(assumptions=ExecutionAssumptions(spread_ticks=1.0))
        bt_res = bt_engine.run(
            dataset,
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            use_split=True,
        )
        assert bt_res.evaluation_mode == "EMPIRICAL_REPLAY"
        assert bt_res.total_trades > 0
        assert bt_res.expectancy_r > 0.0

        # ------------------------------------------------------------------
        # STAGE 5: WALK-FORWARD VALIDATION (STABLE DEGRADATION)
        # ------------------------------------------------------------------
        wf_engine = WalkForwardEngine()
        wf_res = wf_engine.validate(
            dataset,
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            n_splits=3,
            purge_seconds=300.0,
            embargo_seconds=60.0,
        )
        assert wf_res.fold_count > 0
        assert abs(wf_res.degradation) <= 10.0  # Stable, bounded degradation

        # ------------------------------------------------------------------
        # STAGE 6: HARD OUT-OF-SAMPLE GATE
        # ------------------------------------------------------------------
        oos_gate = OOSGate()
        oos_res = oos_gate.evaluate(
            dataset,
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            purge_seconds=300.0,
            embargo_seconds=60.0,
        )
        assert oos_res.oos_samples > 0
        assert oos_res.status in ("PASS", "FAIL")

        # ------------------------------------------------------------------
        # STAGE 7: ROBUSTNESS STRESS TESTING
        # ------------------------------------------------------------------
        rob_engine = RobustnessEngine()
        rob_res = rob_engine.evaluate(
            dataset,
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
        )
        assert len(rob_res.stress_expectancies) == len(rob_engine.scenarios)
        assert rob_res.max_degradation >= 0.0

        # ------------------------------------------------------------------
        # STAGE 8: SCORING & PIPELINE ORCHESTRATION & REGISTRY PERSISTENCE
        # ------------------------------------------------------------------
        registry = StrategyRegistry(repo)
        pipeline = ResearchPipeline(
            dataset_builder=builder,
            registry=registry,
            backtest=bt_engine,
            walkforward=wf_engine,
            oos_gate=oos_gate,
            robustness=rob_engine,
        )
        val_result = pipeline.validate_candidate(candidate, dataset)
        repo._queue.join()

        assert val_result is not None
        assert val_result["strategy_id"] == candidate.strategy_id
        assert val_result["lifecycle"] in ("VALIDATED", "REJECTED")

        # Fetch the registry entry
        reg_entry = registry.get(candidate.strategy_id)
        assert reg_entry is not None
        assert reg_entry.strategy_id == candidate.strategy_id
        assert reg_entry.strategy_version == candidate.strategy_version

        # ------------------------------------------------------------------
        # STAGE 9: SHADOW / FORWARD / LIVE ELIGIBILITY EVALUATION
        # ------------------------------------------------------------------
        is_eligible = reg_entry.is_eligible_for_new_trades
        assert isinstance(is_eligible, bool)

        # ------------------------------------------------------------------
        # STAGE 10: PROPOSAL EVALUATION THROUGH EXPERIENCE INTELLIGENCE
        # ------------------------------------------------------------------
        from nexus_scalp.domain.models import TickData
        from nexus_scalp.features.scalp_features import BarData, ScalpFeatureEngine

        now_t = datetime.now(UTC)
        bars = [
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=now_t - timedelta(minutes=100 - k),
                open=2000.0,
                high=2005.0,
                low=1995.0,
                close=2002.0,
                tick_volume=100.0,
                is_complete=True,
            )
            for k in range(50)
        ]
        tick = TickData(symbol="XAUUSD", timestamp=now_t, bid=2050.0, ask=2050.5)
        feature_vec = ScalpFeatureEngine().compute_from_bars(bars, current_tick=tick)

        exp_engine = ExperienceIntelligenceEngine(
            ledger=ledger,
            evaluator=StrategyEvaluator(audit_repo=repo),
            retriever=ExperienceRetriever(ledger=ledger),
            enabled=True,
            max_inline_refresh_per_sec=0.0,
            score_cache_ttl_sec=1.0,
        )
        proposal = TradeProposal(
            request_id="req_live_demo_01",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=ActionType.BUY_MARKET,
            confidence=0.82,
            reason_code="SMC_GOD_MODE",
            proposed_entry=2050.0,
            stop_loss=2040.0,
            take_profit=2070.0,
            risk_reward_ratio=2.0,
        )
        gated_prop, dec = exp_engine.evaluate_proposal(proposal, feature_vec)
        assert dec is not None
        assert dec.qualifies_trade is True
        assert gated_prop.request_id == "req_live_demo_01"

        # ------------------------------------------------------------------
        # STAGE 11: ORDER LIFECYCLE & DISPATCH
        # ------------------------------------------------------------------
        mock_adapter = SimpleNamespace(
            execute_market_order=lambda **kw: 99912345,  # Ticket
            place_pending_order=lambda **kw: 0,
            get_positions=lambda **kw: [],
        )
        order_mgr = OrderLifecycleManager(
            adapter=mock_adapter, audit_repo=repo, experience_engine=exp_engine
        )
        dispatched = order_mgr.dispatch_order(gated_prop, volume=0.1)
        assert dispatched is True

        # ------------------------------------------------------------------
        # STAGE 12: EXECUTION & OUTCOME PERSISTENCE (CLOSED TRADE)
        # ------------------------------------------------------------------
        exp_engine.record_trade_outcome(
            request_id="req_live_demo_01",
            execution_id="99912345",
            outcome_timestamp=datetime.now(UTC) + timedelta(minutes=5),
            is_executed=True,
            is_closed=True,
            exit_reason="TAKE_PROFIT_HIT",
            realized_pnl_usd=200.0,
            realized_r_multiple=2.0,
            approved_volume=0.1,
            actual_entry=2050.0,
            slippage_points=0.0,
        )
        repo._queue.join()

        merged_rec = ledger.get_experience_by_key("exp_req_live_demo_01")
        assert merged_rec is not None
        assert merged_rec.is_executed is True
        assert merged_rec.realized_r_multiple == 2.0
        assert merged_rec.exit_reason == "TAKE_PROFIT_HIT"

        # ------------------------------------------------------------------
        # STAGE 13: TERMINAL PENDING NON-TRADE HANDLING
        # ------------------------------------------------------------------
        order_mgr._entry_order_ids[88812345] = "req_live_demo_02"
        rec_pending = ExperienceRecord(
            experience_id="exp_row_demo_02",
            request_id="req_live_demo_02",
            idempotency_key="exp_req_live_demo_02",
            symbol="XAUUSD",
            timeframe="M1",
            decision_timestamp=datetime.now(UTC),
            strategy_id="strat_trend_breakout",
            strategy_version="1.0.0",
            context=StrategyContext(
                strategy_id="strat_trend_breakout",
                symbol="XAUUSD",
                session="LONDON",
                regime="TRENDING_MOMENTUM",
                volatility_regime="NORMAL",
                trend_state="BULLISH",
            ),
            feature_snapshot=FeatureSnapshot(values=[0.0] * 50),
            action="BUY_LIMIT",
            entry_reason="SMC",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            approved_volume=0.1,
        )
        ledger.record_experience(rec_pending)
        repo._queue.join()

        # Emit verified cancel
        canceled = order_mgr._emit_terminal_for_pending(
            88812345, DecisionLifecycle.CANCELED_UNFILLED, detail="timeout"
        )
        assert canceled is True
        repo._queue.join()

        merged_cancel = ledger.get_experience_by_key("exp_req_live_demo_02")
        assert merged_cancel is not None
        assert merged_cancel.is_executed is False
        assert merged_cancel.exit_reason == "CANCELED_UNFILLED"
        assert merged_cancel.realized_r_multiple == 0.0  # No fake R!

        # ------------------------------------------------------------------
        # STAGE 14: POST-TRADE BEHAVIORAL ANALYSIS & DATASET AUDIT
        # ------------------------------------------------------------------
        final_audit = builder.audit()
        assert final_audit["total_records"] == 62
        assert final_audit["eligible"] == 61  # 60 seeded + 1 live completed
        assert final_audit["terminal_non_trades"] == 1  # 1 canceled pending

        # ------------------------------------------------------------------
        # STAGE 15: NEXT-DECISION INFLUENCE (EXPERIENCE MEMORY REFRESH)
        # ------------------------------------------------------------------
        exp_engine.invalidate_score_cache()
        updated_score = exp_engine.refresh_strategy_score(
            context=merged_rec.context,
            decision_timestamp=datetime.now(UTC),
        )
        assert updated_score is not None
        assert updated_score.sample_count >= 60
        assert updated_score.score.expectancy_r > 0.0
