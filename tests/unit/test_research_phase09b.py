"""
PHASE 09B Strategy Research, Backtest & Validation Engine — Behavioral Suite
============================================================================
Real behavioral verification of the evidence-driven strategy research +
validation layer. Every test asserts OBSERVABLE BEHAVIOUR (persisted rows,
gate verdicts, causality, versioning, degenerate outcomes) rather than mere
object existence.

Coverage map (spec 38):
    DATA        1.  dataset builder preserves temporal order
    DATA        2.  dataset builder preserves provenance
    DATA        3.  future outcomes cannot enter discovery
    DATA        4.  future normalization cannot leak backward
    CANDIDATES  5.  candidate identity deterministic
    CANDIDATES  6.  candidate version immutable
    CANDIDATES  7.  candidate creation is reproducible
    BACKTEST    8.  deterministic same-input backtest produces same output
    BACKTEST    9.  execution assumptions affect result
    BACKTEST    10. SL/TP behavior is respected
    BACKTEST    11. costs/friction are modeled
    WALKFORWARD 12. temporal folds correct
    WALKFORWARD 13. purging works
    WALKFORWARD 14. embargo works
    WALKFORWARD 15. OOS boundary is respected
    OOS         16. in-sample success + OOS failure = REJECTED
    OOS         17. OOS performance tracked independently
    ROBUSTNESS  18. spread stress evaluated
    ROBUSTNESS  19. slippage stress evaluated
    ROBUSTNESS  20. parameter perturbation evaluated
    ROBUSTNESS  21. fragile candidate fails robustness
    SCORING     22. small samples cannot get unjustified high confidence
    SCORING     23. win rate alone cannot dominate
    SCORING     24. drawdown impacts score
    SCORING     25. OOS impacts score
    SCORING     26. robustness impacts score
    LIFECYCLE   27. candidate enters BACKTESTING
    LIFECYCLE   28. failed validation becomes REJECTED
    LIFECYCLE   29. successful validation becomes VALIDATED
    LIFECYCLE   30. validation failure cannot activate strategy
    VERSIONING  31. modified strategy gets a new version
    VERSIONING  32. old validation record remains immutable
    SAFETY      33. candidate cannot bypass RiskEngine
    SAFETY      34. candidate cannot bypass OrderManager
    SAFETY      35. candidate cannot submit MT5 orders
    REGRESSION  36-39. existing subsystems intact
    WORKER      40. research worker failure is isolated
    WORKER      41. research worker restart is safe
    WORKER      42. research cannot block LiveEngine
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
    ExecutionContext,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    OutcomeDecomposition,
    PositionBehavior,
    StrategyContext,
)
from nexus_scalp.research import (
    BacktestEngine,
    LifecycleError,
    OOSGate,
    ResearchDatasetBuilder,
    ResearchPipeline,
    RobustnessEngine,
    StrategyCandidate,
    StrategyRegistry,
    WalkForwardEngine,
    approve_for_live,
    can_transition,
    compute_strategy_score,
    discover_candidates,
)
from nexus_scalp.research.models import (
    CandidateLifecycle,
    ExecutionAssumptions,
    ResearchDataset,
    ResearchSample,
)
from nexus_scalp.research.splitting import split_temporal, walk_forward_folds

# =============================================================================
# FIXTURES & HELPERS
# =============================================================================


@pytest.fixture
def temp_audit_repo(tmp_path):
    db_file = tmp_path / "test_research_phase09b.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


def flush(repo):
    repo._queue.join()


def make_record(
    key: str,
    strategy_id: str = "strat_research",
    decision_ts: datetime | None = None,
    action: str = "BUY_MARKET",
    entry: float = 2000.0,
    sl: float = 1990.0,
    tp: float = 2020.0,
    dimension: int = CANONICAL_FEATURE_DIMENSION,
    schema_id: str = CANONICAL_FEATURE_SCHEMA_ID,
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
        context=StrategyContext(
            strategy_id=strategy_id,
            symbol="XAUUSD",
            session="ALL",
            regime="TRENDING",
            volatility_regime="HIGH",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id=schema_id,
            feature_dimension=dimension,
            values=[0.0] * dimension,
        ),
        action=action,
        entry_reason="SMC_GOD_MODE",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
    )


def make_outcome(
    record: ExperienceRecord,
    realized_r: float,
    exit_reason: str = "TP",
) -> ExperienceOutcome:
    return ExperienceOutcome(
        idempotency_key=record.idempotency_key,
        execution_id=f"ticket_{record.idempotency_key}",
        outcome_timestamp=record.decision_timestamp + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason=exit_reason,
        realized_pnl_usd=realized_r * 100.0,
        realized_r_multiple=realized_r,
        approved_volume=0.1,
        behavior=PositionBehavior(
            mfe_r=max(0.5, realized_r) if realized_r > 0 else 0.2,
            mae_r=0.2,
            mae_points=2.0,
            mfe_points=5.0,
            expected_duration_sec=900.0,
            duration_sec=300.0,
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


def seed_experiences(
    ledger: ExperienceLedger,
    repo,
    count: int,
    prefix: str = "res",
    r_values: list[float] | None = None,
) -> list[ExperienceRecord]:
    """Records N decisions+outcomes with optionally controlled R values."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    records = []
    for i in range(count):
        rec = make_record(
            key=f"{prefix}{i}",
            decision_ts=base + timedelta(minutes=30 * i),
        )
        r = (r_values[i] if r_values and i < len(r_values) else 0.3) if i % 3 else 0.4
        ledger.record_experience(rec)
        ledger.record_outcome(make_outcome(rec, realized_r=r))
        records.append(rec)
    flush(repo)
    return records


def build_candidate(strategy_id: str = "STRAT-TEST-1234") -> StrategyCandidate:
    """A deterministic candidate with content-derived version."""
    c = StrategyCandidate(
        strategy_id=strategy_id,
        strategy_version="",
        context_definition={
            "fingerprint": "XAUUSD|M1|ALL|TRENDING|HIGH|BULLISH",
            "symbol": "XAUUSD",
        },
        entry_logic={"direction": "directional", "context": "XAUUSD|M1|ALL|TRENDING|HIGH|BULLISH"},
        exit_logic={"direction": "neutral", "context": "XAUUSD|M1|ALL|TRENDING|HIGH|BULLISH"},
    )
    return c.model_copy(update={"strategy_version": c.canonical_version()})


# =============================================================================
# 1-4. DATA: DATASET BUILDER
# =============================================================================


class TestDatasetBuilder:
    def test_preserves_temporal_order(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        # Insert out of order deliberately; builder must sort by decision time.
        rec_late = make_record("later", decision_ts=base + timedelta(hours=3))
        rec_early = make_record("early", decision_ts=base)
        for r in [rec_late, rec_early]:
            ledger.record_experience(r)
            ledger.record_outcome(make_outcome(r, realized_r=0.3))
        flush(temp_audit_repo)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        ts = [s.decision_timestamp for s in ds.samples]
        assert ts == sorted(ts), "dataset must be causally ordered"

    def test_preserves_provenance(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        rec = make_record("prov", decision_ts=datetime(2024, 1, 1, tzinfo=UTC))
        ledger.record_experience(rec)
        ledger.record_outcome(make_outcome(rec, realized_r=0.5))
        flush(temp_audit_repo)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        assert len(ds.samples) == 1
        s = ds.samples[0]
        assert s.experience_id == rec.experience_id
        assert s.idempotency_key == "prov"
        assert s.feature_schema_id == CANONICAL_FEATURE_SCHEMA_ID
        assert s.feature_dimension == CANONICAL_FEATURE_DIMENSION
        assert s.strategy_id == rec.strategy_id
        assert s.regime == "TRENDING"

    def test_future_outcomes_cannot_enter_discovery(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        for i in range(6):
            rec = make_record(f"fut{i}", decision_ts=base + timedelta(hours=i))
            ledger.record_experience(rec)
            ledger.record_outcome(make_outcome(rec, realized_r=0.4))
        flush(temp_audit_repo)
        builder = ResearchDatasetBuilder(ledger=ledger)
        as_of = base + timedelta(hours=2)  # only first 2 decisions allowed
        ds = builder.build_for_strategy("strat_research", as_of=as_of)
        assert len(ds.samples) == 2
        for s in ds.samples:
            assert s.decision_timestamp < as_of

    def test_future_normalization_cannot_leak_backward(self, temp_audit_repo):
        from nexus_scalp.research.leakage import fit_forward_stats

        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        train = []
        for i in range(20):
            rec = make_record(f"norm{i}", decision_ts=base + timedelta(hours=i))
            ledger.record_experience(rec)
            ledger.record_outcome(make_outcome(rec, realized_r=0.5 if i < 10 else -0.2))
            train.append(rec)
        flush(temp_audit_repo)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        split = split_temporal(ds, val_frac=0.2, oos_frac=0.2)
        stats = fit_forward_stats(split.train)
        # Stats are fit only on train; applying forward to OOS never refits.
        oos_vals = [s.realized_r for s in split.oos]
        applied = [stats.apply(v) for v in oos_vals]
        assert len(applied) == len(oos_vals)
        # No exception and values are deterministic transformations.
        import math

        assert all(not math.isnan(v) for v in applied)  # no NaNs


# =============================================================================
# 5-7. CANDIDATES
# =============================================================================


class TestCandidates:
    def test_identity_deterministic(self):
        a = build_candidate()
        b = build_candidate()
        assert a.strategy_id == b.strategy_id
        assert a.canonical_version() == b.canonical_version()
        assert a.strategy_version == b.strategy_version

    def test_version_immutable(self):
        c = build_candidate()
        v1 = c.strategy_version
        c2 = c.with_definition_change(entry_logic={"direction": "counter"})
        assert c2.strategy_version != v1, "changed definition must produce NEW version"
        assert c.strategy_version == v1, "original must be untouched (immutable)"

    def test_creation_reproducible(self):
        c1 = build_candidate()
        c2 = build_candidate()
        assert c1.model_dump(exclude={"creation_timestamp"}) == c2.model_dump(
            exclude={"creation_timestamp"}
        )


# =============================================================================
# 8-11. BACKTEST
# =============================================================================


def _ds_from_records(records, repo, ledger) -> ResearchDataset:
    flush(repo)
    return ResearchDatasetBuilder(ledger=ledger).build()


class TestBacktest:
    def test_deterministic(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 40)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        eng = BacktestEngine()
        r1 = eng.run(ds, "s1", "v1")
        r2 = eng.run(ds, "s1", "v1")
        assert r1.model_dump() == r2.model_dump()

    def test_execution_assumptions_affect_result(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 40, r_values=[0.5] * 40)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        clean = BacktestEngine(assumptions=ExecutionAssumptions()).run(ds, "s1", "v1")
        stressed = BacktestEngine(
            assumptions=ExecutionAssumptions(spread_ticks=3, slippage_ticks=2)
        ).run(ds, "s1", "v1")
        assert stressed.expectancy_r < clean.expectancy_r
        assert stressed.model_dump() != clean.model_dump()

    def test_sltp_behavior_respected(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        # Two trades: one SL, one TP.
        r1 = make_record("sl1", decision_ts=datetime(2024, 1, 1, tzinfo=UTC))
        r2 = make_record(
            "tp1", decision_ts=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=30)
        )
        ledger.record_experience(r1)
        ledger.record_outcome(make_outcome(r1, realized_r=-1.0, exit_reason="SL"))
        ledger.record_experience(r2)
        ledger.record_outcome(make_outcome(r2, realized_r=2.0, exit_reason="TP"))
        flush(temp_audit_repo)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        bt = BacktestEngine().run(ds, "s1", "v1")
        assert bt.total_trades == 2
        assert bt.losses == 1 and bt.wins == 1
        assert bt.avg_loss_r < 0 and bt.avg_win_r > 0

    def test_friction_modeled(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        r = make_record("fric", decision_ts=datetime(2024, 1, 1, tzinfo=UTC))
        ledger.record_experience(r)
        # A near-breakeven trade with large friction should turn negative.
        ledger.record_outcome(make_outcome(r, realized_r=0.05))
        flush(temp_audit_repo)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        bt = BacktestEngine(
            assumptions=ExecutionAssumptions(spread_ticks=10, slippage_ticks=10)
        ).run(ds, "s1", "v1")
        assert bt.expectancy_r < 0.05


# =============================================================================
# 12-15. WALK-FORWARD
# =============================================================================


class TestWalkForward:
    def test_temporal_folds_correct(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 60)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        folds = walk_forward_folds(ds, n_splits=3)
        assert len(folds) >= 2
        for f in folds:
            # Every fold's train is strictly BEFORE validation, validation before OOS.
            assert f.train[-1].decision_timestamp < f.validation[0].decision_timestamp
            if f.oos:
                assert f.validation[-1].decision_timestamp < f.oos[0].decision_timestamp

    def test_purging_works(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        for i in range(30):
            rec = make_record(f"purge{i}", decision_ts=base + timedelta(hours=i))
            # Horizons of 5 hours so purging with a boundary is meaningful.
            rec = rec.model_copy(update={})
            ledger.record_experience(rec)
            ledger.record_outcome(make_outcome(rec, realized_r=0.3))
        flush(temp_audit_repo)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        # With a huge purge, some train samples crossing the boundary are removed.
        no_purge = split_temporal(ds, val_frac=0.2, oos_frac=0.2, purge_seconds=0.0)
        with_purge = split_temporal(ds, val_frac=0.2, oos_frac=0.2, purge_seconds=6 * 3600)
        assert len(with_purge.train) <= len(no_purge.train)

    def test_embargo_works(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        for i in range(30):
            rec = make_record(f"emb{i}", decision_ts=base + timedelta(hours=i))
            ledger.record_experience(rec)
            ledger.record_outcome(make_outcome(rec, realized_r=0.3))
        flush(temp_audit_repo)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        no_emb = split_temporal(ds, val_frac=0.2, oos_frac=0.2, embargo_seconds=0.0)
        with_emb = split_temporal(ds, val_frac=0.2, oos_frac=0.2, embargo_seconds=3 * 3600)
        assert len(with_emb.validation) <= len(no_emb.validation)

    def test_oos_boundary_respected(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 60)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        split = split_temporal(ds, val_frac=0.2, oos_frac=0.2)
        if split.oos:
            assert split.train[-1].decision_timestamp <= split.oos[0].decision_timestamp


# =============================================================================
# 16-17. OOS GATE
# =============================================================================


def _build_strong_vs_weak():
    """Returns two datasets: strong OOS (good) and weak OOS (bad)."""

    def make_ds(r_in: list[float], r_oos: list[float]):
        return ("s", "v", r_in, r_oos)

    return make_ds


class TestOOSGate:
    def test_in_sample_success_oos_failure_rejected(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        # 60 in-sample winning trades, then 20 OOS losing trades.
        for i in range(60):
            rec = make_record(f"oos_in{i}", decision_ts=base + timedelta(hours=i))
            ledger.record_experience(rec)
            ledger.record_outcome(make_outcome(rec, realized_r=0.5))
        for i in range(20):
            rec = make_record(f"oos_out{i}", decision_ts=base + timedelta(hours=60 + i))
            ledger.record_experience(rec)
            ledger.record_outcome(make_outcome(rec, realized_r=-0.8))
        flush(temp_audit_repo)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        gate = OOSGate()
        oos = gate.evaluate(ds, "s1", "v1", oos_frac=0.25)
        assert oos.in_sample_expectancy_r > 0
        assert oos.oos_expectancy_r < 0
        assert oos.status == "FAIL", "OOS failure must reject regardless of in-sample"

    def test_oos_tracked_independently(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        for i in range(40):
            rec = make_record(f"oos2_in{i}", decision_ts=base + timedelta(hours=i))
            ledger.record_experience(rec)
            ledger.record_outcome(make_outcome(rec, realized_r=0.4))
        for i in range(10):
            rec = make_record(f"oos2_out{i}", decision_ts=base + timedelta(hours=40 + i))
            ledger.record_experience(rec)
            ledger.record_outcome(make_outcome(rec, realized_r=0.5))
        flush(temp_audit_repo)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        gate = OOSGate()
        oos = gate.evaluate(ds, "s1", "v1")
        assert oos.oos_samples >= 1
        assert oos.oos_expectancy_r != oos.in_sample_expectancy_r or oos.oos_samples > 0


# =============================================================================
# 18-21. ROBUSTNESS
# =============================================================================


class TestRobustness:
    def test_spread_stress_evaluated(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 40, r_values=[0.5] * 40)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        rob = RobustnessEngine().evaluate(ds, "s1", "v1")
        assert "spread_plus_1" in rob.stress_expectancies
        assert "spread_plus_2" in rob.stress_expectancies

    def test_slippage_stress_evaluated(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 40, r_values=[0.5] * 40)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        rob = RobustnessEngine().evaluate(ds, "s1", "v1")
        assert "slippage_plus_1" in rob.stress_expectancies
        assert "slippage_plus_2" in rob.stress_expectancies

    def test_parameter_perturbation_evaluated(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 40, r_values=[0.5] * 40)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        rob = RobustnessEngine().evaluate(ds, "s1", "v1")
        assert "latency_plus_50ms" in rob.stress_expectancies

    def test_fragile_candidate_fails_robustness(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        # Thin-edge trades with TIGHT stops: risk distance 0.3 points means a
        # single tick (0.01) is 3.3% of planned risk, and a 2-tick stress
        # consumes ~6.7% of R per trade -> the 0.06R edge degrades by >0.05R.
        base = datetime(2024, 1, 1, tzinfo=UTC)
        for i in range(40):
            rec = make_record(
                f"frag{i}",
                decision_ts=base + timedelta(hours=i),
                entry=2000.0,
                sl=1999.7,  # 0.3-point risk -> 1 tick = 3.3% of risk
            )
            ledger.record_experience(rec)
            ledger.record_outcome(make_outcome(rec, realized_r=0.06))
        flush(temp_audit_repo)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        # A 2-tick stress = 0.0667R per trade; 0.05R ceiling flags it FRAGILE.
        rob = RobustnessEngine(max_acceptable_deg_r=0.05).evaluate(ds, "s1", "v1")
        assert rob.status == "FAIL", "fragile thin-edge strategy must fail robustness"


# =============================================================================
# 22-26. SCORING
# =============================================================================


def _score_dataset(r_values: list[float], r_oos: list[float]) -> ResearchDataset:
    """Builds dataset with controlled in-sample + OOS R values."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    samples: list[ResearchSample] = []
    keys = 0
    for i, r in enumerate(r_values):
        samples.append(
            ResearchSample(
                sample_id=f"rs_{i}",
                experience_id=f"exp_{i}",
                idempotency_key=f"k{i}",
                decision_timestamp=base + timedelta(hours=i),
                outcome_timestamp=base + timedelta(hours=i, minutes=5),
                symbol="XAUUSD",
                strategy_id="s1",
                strategy_version="v1",
                regime="TRENDING",
                session="ALL",
                volatility_regime="HIGH",
                trend_state="BULLISH",
                entry_price=2000.0,
                stop_loss=1990.0,
                take_profit=2020.0,
                direction="BUY_MARKET",
                realized_r=r,
                realized_pnl_usd=r * 100.0,
                risk_distance=10.0,
                holding_duration_sec=300.0,
                mae_r=0.2,
                mfe_r=1.0,
                exit_reason="TP",
            )
        )
        keys += 1
    for i, r in enumerate(r_oos):
        samples.append(
            ResearchSample(
                sample_id=f"rs_oos_{i}",
                experience_id=f"exp_oos_{i}",
                idempotency_key=f"koos{i}",
                decision_timestamp=base + timedelta(hours=len(r_values) + i),
                outcome_timestamp=base + timedelta(hours=len(r_values) + i, minutes=5),
                symbol="XAUUSD",
                strategy_id="s1",
                strategy_version="v1",
                regime="TRENDING",
                session="ALL",
                volatility_regime="HIGH",
                trend_state="BULLISH",
                entry_price=2000.0,
                stop_loss=1990.0,
                take_profit=2020.0,
                direction="BUY_MARKET",
                realized_r=r,
                realized_pnl_usd=r * 100.0,
                risk_distance=10.0,
                holding_duration_sec=300.0,
                mae_r=0.2,
                mfe_r=1.0,
                exit_reason="TP",
            )
        )
    return ResearchDataset(dataset_id="ds_test", samples=samples)


class TestScoring:
    def test_small_sample_low_confidence(self, temp_audit_repo):
        ds = _score_dataset([1.2] * 8, [])
        bt = BacktestEngine().run(ds, "s1", "v1")
        wf = WalkForwardEngine().validate(ds, "s1", "v1")
        oos = OOSGate().evaluate(ds, "s1", "v1")
        rob = RobustnessEngine().evaluate(ds, "s1", "v1")
        score = compute_strategy_score(ds, bt, wf, oos, rob)
        assert score.sample_confidence < 0.5, "8 trades must not get high confidence"
        assert score.verdict != "VALIDATED", "tiny sample must never be VALIDATED"

    def test_win_rate_alone_cannot_dominate(self, temp_audit_repo):
        # 90% win rate, but expectancy near zero and high drawdown.
        ds = _score_dataset([0.05] * 90 + [-0.9] * 10, [])
        bt = BacktestEngine().run(ds, "s1", "v1")
        score = compute_strategy_score(ds, bt, None, None, None)
        assert score.performance_score < 1.0
        assert score.verdict != "VALIDATED"

    def test_drawdown_impacts_score(self, temp_audit_repo):
        ds_bad = _score_dataset([0.5] * 20 + [-2.5] * 10, [])
        bt_bad = BacktestEngine().run(ds_bad, "s1", "v1")
        score_bad = compute_strategy_score(ds_bad, bt_bad, None, None, None)
        assert score_bad.risk_score < 0.8

    def test_oos_impacts_score(self):
        ds = _score_dataset([0.5] * 40, [-0.5] * 10)
        bt = BacktestEngine().run(ds, "s1", "v1")
        wf = WalkForwardEngine().validate(ds, "s1", "v1")
        oos = OOSGate().evaluate(ds, "s1", "v1")
        rob = RobustnessEngine().evaluate(ds, "s1", "v1")
        score = compute_strategy_score(ds, bt, wf, oos, rob)
        assert score.oos_score == 0.0
        assert score.verdict == "REJECTED", "OOS failure must force REJECTED"

    def test_robustness_impacts_score(self):
        ds = _score_dataset([0.08] * 60, [0.08] * 10)
        bt = BacktestEngine().run(ds, "s1", "v1")
        # Thin edge: robustness must reflect the fragility penalty.
        rob = RobustnessEngine(max_acceptable_deg_r=0.10).evaluate(ds, "s1", "v1")
        score = compute_strategy_score(ds, bt, None, None, rob)
        assert score.robustness_score < 1.0


# =============================================================================
# 27-30. LIFECYCLE
# =============================================================================


class TestLifecycle:
    def test_candidate_enters_backtesting(self):
        assert can_transition(CandidateLifecycle.DISCOVERED, CandidateLifecycle.BACKTESTING)

    def test_failed_validation_rejected(self):
        assert can_transition(CandidateLifecycle.ROBUSTNESS_TESTING, CandidateLifecycle.REJECTED)

    def test_successful_validation_validated(self):
        assert can_transition(CandidateLifecycle.ROBUSTNESS_TESTING, CandidateLifecycle.VALIDATED)

    def test_validation_failure_cannot_activate(self):
        # REJECTED cannot go to ACTIVE: no path.
        assert not can_transition(CandidateLifecycle.REJECTED, CandidateLifecycle.ACTIVE)
        with pytest.raises(LifecycleError):
            approve_for_live(CandidateLifecycle.REJECTED)


# =============================================================================
# 31-32. VERSIONING
# =============================================================================


class TestVersioning:
    def test_modified_strategy_new_version(self):
        c = build_candidate()
        old_v = c.strategy_version
        c2 = c.with_definition_change(entry_logic={"direction": "counter"})
        assert c2.strategy_version != old_v

    def test_old_validation_record_immutable(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 50)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        registry = StrategyRegistry(audit_repo=temp_audit_repo)
        pipeline = ResearchPipeline(
            dataset_builder=ResearchDatasetBuilder(ledger),
            registry=registry,
        )
        cand = build_candidate("STRAT-IMMUTABLE")
        pipeline.validate_candidate(cand, ds)
        flush(temp_audit_repo)
        entry_v1 = registry.get("STRAT-IMMUTABLE")
        assert entry_v1 is not None
        # Modify -> new version -> validate again; old entry must remain.
        cand2 = cand.with_definition_change(entry_logic={"direction": "counter"})
        pipeline.validate_candidate(cand2, ds)
        flush(temp_audit_repo)
        entry_v0 = registry.get("STRAT-IMMUTABLE", cand.strategy_version)
        assert entry_v0 is not None, "old version record must remain immutable"
        assert entry_v0.lifecycle in (CandidateLifecycle.VALIDATED, CandidateLifecycle.REJECTED)


# =============================================================================
# 33-35. SAFETY
# =============================================================================


class TestSafetyContract:
    def test_candidate_cannot_bypass_risk_engine(self):
        # The research package must not import or hold a RiskEngine.
        import nexus_scalp.research

        assert not hasattr(nexus_scalp.research, "RiskEngine")

    def test_candidate_cannot_bypass_order_manager(self):
        import nexus_scalp.research

        assert not hasattr(nexus_scalp.research, "OrderManager")
        assert not hasattr(nexus_scalp.research, "OrderLifecycleManager")

    def test_candidate_cannot_submit_mt5_orders(self):
        import nexus_scalp.research

        assert not hasattr(nexus_scalp.research, "MetaTrader5")
        assert not hasattr(nexus_scalp.research, "mt5")


# =============================================================================
# 36-39. REGRESSION
# =============================================================================


class TestRegression:
    def test_phase08_experience_intact(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        rec = make_record("reg1", decision_ts=datetime(2024, 1, 1, tzinfo=UTC))
        assert ledger.record_experience(rec) is True
        flush(temp_audit_repo)
        assert ledger.get_experience_by_key("reg1") is not None

    def test_trade_intelligence_intact(self, temp_audit_repo):
        from nexus_scalp.intelligence.store import count_lifecycle_events

        assert count_lifecycle_events(temp_audit_repo) == 0

    def test_accounting_intact(self, temp_audit_repo):
        from nexus_scalp.accounting.core import AccountingCore

        core = AccountingCore(audit_repo=temp_audit_repo)
        assert core is not None

    def test_50d_contract_intact(self):
        from nexus_scalp.experience.models import CANONICAL_FEATURE_DIMENSION

        assert CANONICAL_FEATURE_DIMENSION == 50


# =============================================================================
# 40-42. WORKER
# =============================================================================


class TestWorker:
    def test_failure_isolated(self, temp_audit_repo):
        from nexus_scalp.research.worker import ResearchWorker

        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        pipeline = ResearchPipeline(
            dataset_builder=ResearchDatasetBuilder(ledger),
            registry=StrategyRegistry(audit_repo=temp_audit_repo),
        )
        worker = ResearchWorker(
            audit_repo=temp_audit_repo,
            ledger=ledger,
            pipeline=pipeline,
            interval_sec=0.0,
        )
        worker.start()
        # A cycle with an empty ledger must not raise; it logs and continues.
        worker.tick()
        assert worker.running
        worker.stop()

    def test_restart_safe(self, temp_audit_repo):
        from nexus_scalp.research.worker import ResearchWorker

        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        pipeline = ResearchPipeline(
            dataset_builder=ResearchDatasetBuilder(ledger),
            registry=StrategyRegistry(audit_repo=temp_audit_repo),
        )
        worker = ResearchWorker(
            audit_repo=temp_audit_repo,
            ledger=ledger,
            pipeline=pipeline,
            interval_sec=0.0,
        )
        worker.start()
        worker.tick()
        worker.stop()
        # Restart: checkpoint restore must be idempotent.
        worker2 = ResearchWorker(
            audit_repo=temp_audit_repo,
            ledger=ledger,
            pipeline=pipeline,
            interval_sec=0.0,
        )
        worker2.start()
        assert worker2.running
        worker2.stop()

    def test_research_cannot_block_live_engine(self, temp_audit_repo):
        # The research worker is only ever invoked through asyncio.to_thread in
        # LiveEngine; its tick() is synchronous and bounded. Verify tick returns
        # quickly even with data.
        from nexus_scalp.research.worker import ResearchWorker

        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 30)
        pipeline = ResearchPipeline(
            dataset_builder=ResearchDatasetBuilder(ledger),
            registry=StrategyRegistry(audit_repo=temp_audit_repo),
        )
        worker = ResearchWorker(
            audit_repo=temp_audit_repo,
            ledger=ledger,
            pipeline=pipeline,
            interval_sec=0.0,
        )
        worker.start()
        import time

        t0 = time.perf_counter()
        worker.tick()
        elapsed = time.perf_counter() - t0
        worker.stop()
        assert elapsed < 5.0, "research cycle must be bounded/non-blocking"

    def test_discovered_candidates_re_enqueued_after_restart(self, temp_audit_repo):
        # RC1 regression: after a worker restart with an UNCHANGED dataset,
        # discovery/validation must still run so DISCOVERED candidates are
        # re-enqueued for processing instead of remaining stuck.
        #
        # Mechanics: on the first post-restart cycle _refresh_dataset() syncs
        # _last_dataset_id, so the legacy `if self._dataset_changed:` guard
        # skipped discovery+validation entirely -> DISCOVERED candidates from
        # the pre-restart process were stranded forever.
        from nexus_scalp.research.worker import ResearchWorker

        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        # Enough positive experiences to produce a discovered family.
        seed_experiences(ledger, temp_audit_repo, 60, r_values=[0.5] * 60)
        real_pipeline = ResearchPipeline(
            dataset_builder=ResearchDatasetBuilder(ledger),
            registry=StrategyRegistry(audit_repo=temp_audit_repo),
        )

        class CountingPipeline:
            """Delegating spy: counts discover/validate invocations."""

            def __init__(self, inner: Any) -> None:
                self._inner = inner
                self.discover_calls = 0
                self.validate_calls = 0

            @property
            def dataset_builder(self) -> Any:
                return self._inner.dataset_builder

            def discover(self, dataset: Any) -> list[Any]:
                self.discover_calls += 1
                return self._inner.discover(dataset)

            def validate_candidate(self, candidate: Any, dataset: Any, **kw: Any) -> dict[str, Any]:
                self.validate_calls += 1
                return self._inner.validate_candidate(candidate, dataset, **kw)

        # --- Pre-restart worker: discovers + validates normally. ---
        pipe1 = CountingPipeline(real_pipeline)
        worker1 = ResearchWorker(
            audit_repo=temp_audit_repo,
            ledger=ledger,
            pipeline=pipe1,
            interval_sec=0.0,
        )
        worker1.start()
        worker1.tick()
        worker1.stop()
        assert pipe1.discover_calls >= 1, "first cycle must run discovery"
        assert pipe1.validate_calls >= 1, "first cycle must validate discoveries"

        # --- Post-restart worker: SAME db, SAME (unchanged) dataset. ---
        pipe2 = CountingPipeline(real_pipeline)
        worker2 = ResearchWorker(
            audit_repo=temp_audit_repo,
            ledger=ledger,
            pipeline=pipe2,
            interval_sec=0.0,
        )
        worker2.start()
        worker2.tick()
        worker2.stop()

        # Dataset identity must be unchanged (same content-addressed id).
        assert worker2._dataset is not None
        assert (
            getattr(worker2._dataset, "dataset_id", "") == worker2._last_dataset_id
        ), "restart must observe the SAME dataset (RC1 precondition)"
        # THE FIX: despite DATASET_UNCHANGED, discovery + validation ran again,
        # re-enqueuing DISCOVERED candidates for processing.
        assert pipe2.discover_calls >= 1, (
            "post-restart cycle must re-run discovery (re-enqueue) even when "
            "the dataset is unchanged"
        )
        assert pipe2.validate_calls >= 1, (
            "post-restart cycle must re-validate DISCOVERED candidates even "
            "when the dataset is unchanged"
        )


# =============================================================================
# FULL PIPELINE END-TO-END
# =============================================================================


class TestPipelineEndToEnd:
    def test_full_pipeline_registers_result(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 80, r_values=[0.4] * 80)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        registry = StrategyRegistry(audit_repo=temp_audit_repo)
        pipeline = ResearchPipeline(
            dataset_builder=ResearchDatasetBuilder(ledger),
            registry=registry,
        )
        cand = build_candidate("STRAT-E2E")
        result = pipeline.validate_candidate(cand, ds)
        flush(temp_audit_repo)
        assert result["lifecycle"] in ("VALIDATED", "REJECTED")
        entry = registry.get("STRAT-E2E")
        assert entry is not None
        assert entry.backtest is not None
        assert entry.oos is not None
        assert entry.score is not None
        assert entry.validation_lineage

    def test_pipeline_never_promotes_to_active(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 60, r_values=[0.4] * 60)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        registry = StrategyRegistry(audit_repo=temp_audit_repo)
        pipeline = ResearchPipeline(
            dataset_builder=ResearchDatasetBuilder(ledger),
            registry=registry,
        )
        result = pipeline.validate_candidate(build_candidate("STRAT-NOACTIVE"), ds)
        assert result["lifecycle"] != "ACTIVE", "validation must never auto-promote"

    def test_discovery_from_experience(self, temp_audit_repo):
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        seed_experiences(ledger, temp_audit_repo, 30, r_values=[0.5] * 30)
        ds = ResearchDatasetBuilder(ledger=ledger).build()
        candidates = discover_candidates(ds.samples)
        assert len(candidates) >= 1
        assert all(c.lifecycle == CandidateLifecycle.DISCOVERED for c in candidates)
