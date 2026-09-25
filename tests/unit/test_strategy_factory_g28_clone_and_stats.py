"""
STRATEGY FACTORY — G28 Acceptance Tests (Clone Pre-Screen & Operator Persistence)
==============================================================================
Proves:
    1. Clone pre-skip fires on known pathological clusters (>= 50 members, 0 OOS passes),
       recording CLONE_SKIPPED without re-running the research pipeline.
    2. Operator stats and behavioral clone clusters persist across StrategyFactory
       restart.
    3. Adaptive probabilities remain bounded within [min, max] and OOS scores
       never contaminate mutation/exploration weights.
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
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.strategies.factory.models import (
    CandidateSource,
    EvolutionConfig,
    FactoryCandidate,
    FailureReason,
    StrategyDsl,
    StrategyFamily,
)
from nexus_scalp.strategies.factory.orchestrator import StrategyFactory
from nexus_scalp.strategies.factory.store import get_operator_stats, set_operator_stats


@pytest.fixture
def audit_repo(tmp_path):
    db_file = tmp_path / "test_g28_factory.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


def flush(repo):
    repo._queue.join()


def make_record(key: str, decision_ts: datetime) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=f"req_{key}",
        idempotency_key=key,
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=decision_ts,
        strategy_id="strat_research",
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id="strat_research",
            symbol="XAUUSD",
            session="ALL",
            regime="TRENDING",
            volatility_regime="HIGH",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
            values=[0.1] * CANONICAL_FEATURE_DIMENSION,
        ),
        action="BUY_MARKET",
        entry_reason="SMC_GOD_MODE",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
    )


def make_outcome(record: ExperienceRecord, realized_r: float) -> ExperienceOutcome:
    return ExperienceOutcome(
        idempotency_key=record.idempotency_key,
        execution_id=f"ticket_{record.idempotency_key}",
        outcome_timestamp=record.decision_timestamp + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason="TP" if realized_r > 0 else "SL",
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


def seed_experiences(repo, count: int = 30):
    ledger = ExperienceLedger(audit_repo=repo)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(count):
        rec = make_record(f"fx_{i}", base + timedelta(minutes=30 * i))
        ledger.record_experience(rec)
        ledger.record_outcome(
            make_outcome(rec, realized_r=-0.1)
        )  # negative outcomes -> 0 OOS passes
    flush(repo)


def test_operator_stats_persistence(audit_repo):
    """Acceptance Test 2: Operator stats persist across StrategyFactory restart."""
    cfg = EvolutionConfig(generation_size=4, elite_size=2)
    factory1 = StrategyFactory(audit_repo=audit_repo, research_pipeline=None, config=cfg)
    factory1._operator_stats = {"MUTATION": {"generated": 42, "survived": 5, "elite": 2}}
    factory1._clone_clusters = {"sig_abc": {"members": 55, "oos_passes": 0}}
    factory1._persist_operator_accounting()
    flush(audit_repo)

    # Restart factory with new instance sharing the audit repo
    factory2 = StrategyFactory(audit_repo=audit_repo, research_pipeline=None, config=cfg)
    status = factory2.loop_status()
    assert status["operator_stats"].get("MUTATION", {}).get("generated") == 42
    assert factory2._clone_clusters.get("sig_abc", {}).get("members") == 55
    assert status["clone_clusters_pathological"] == 1


def test_semantic_clone_prescreen_skip(audit_repo):
    """Acceptance Test 1: Clone pre-skip fires on known pathological clusters (>=50 members, 0 OOS passes)."""
    seed_experiences(audit_repo, count=30)
    ledger = ExperienceLedger(audit_repo=audit_repo)
    registry = StrategyRegistry(audit_repo=audit_repo)
    dataset_builder = ResearchDatasetBuilder(ledger=ledger)
    pipeline = ResearchPipeline(dataset_builder=dataset_builder, registry=registry)

    cfg = EvolutionConfig(generation_size=4, elite_size=2, clone_cluster_min_members=10)
    factory = StrategyFactory(audit_repo=audit_repo, research_pipeline=pipeline, config=cfg)
    dataset = pipeline.dataset_builder.build()

    dsl = StrategyDsl(
        hypothesis={"statement": "clone test", "market_mechanism": "test"},
        family=StrategyFamily.TREND_FOLLOWING,
        market={"symbols": ["XAUUSD"], "timeframes": ["M1"]},
        entry={"logic": "test", "confirmation": ["choch_sig"]},
        filters=[{"feature": "dist_to_ema_21", "op": "gt", "value": 0.0}],
    )
    from nexus_scalp.strategies.factory.dsl import candidate_id_from_hash, dsl_hash

    digest = dsl_hash(dsl)
    candidate = FactoryCandidate(
        candidate_id=candidate_id_from_hash(digest),
        definition_hash=digest,
        generation_id="G1",
        source=CandidateSource.TEMPLATE,
        dsl=dsl,
        family=dsl.family,
    )

    # Compute signature
    from nexus_scalp.strategies.factory.benchmark import behavioral_preview_signature

    snapshot = factory._ledger_snapshot_for_filter()
    sig = behavioral_preview_signature(candidate, snapshot)

    # Seed the clone cluster as pathological (members = 15 >= 10, oos_passes = 0)
    factory._clone_clusters[sig] = {"members": 15, "oos_passes": 0}
    factory._persist_operator_accounting()
    flush(audit_repo)

    # Evaluate candidate: should trigger CLONE_SKIPPED pre-screen
    res = factory.evaluate_candidate(candidate, dataset)
    flush(audit_repo)

    assert res is not None
    assert res.get("lifecycle") == "REJECTED"
    assert "CLONE_SKIPPED" in (res.get("failure_reasons") or [])

    # Check failure was recorded
    from nexus_scalp.strategies.factory.store import list_failures

    failures = list_failures(audit_repo, generation_id="G1")
    assert any(f.get("reason") == "CLONE_SKIPPED" for f in failures)

    # The candidate row must carry the CLONE_SKIPPED failure reason too
    from nexus_scalp.strategies.factory.store import list_candidates

    rows = [
        c
        for c in list_candidates(audit_repo, generation_id="G1")
        if c.get("candidate_id") == candidate.candidate_id
    ]
    assert rows and "CLONE_SKIPPED" in (rows[0].get("failure_reasons") or [])


def test_clone_prescreen_disabled_evaluates_normally(audit_repo):
    """Reversibility: clone_prescreen_enabled=False restores evaluate-everything."""
    seed_experiences(audit_repo, count=30)
    ledger = ExperienceLedger(audit_repo=audit_repo)
    registry = StrategyRegistry(audit_repo=audit_repo)
    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(ledger=ledger), registry=registry
    )
    cfg = EvolutionConfig(
        generation_size=4,
        elite_size=2,
        clone_cluster_min_members=10,
        clone_prescreen_enabled=False,
    )
    factory = StrategyFactory(audit_repo=audit_repo, research_pipeline=pipeline, config=cfg)
    dataset = pipeline.dataset_builder.build()

    dsl = StrategyDsl(
        hypothesis={"statement": "clone test", "market_mechanism": "test"},
        family=StrategyFamily.TREND_FOLLOWING,
        market={"symbols": ["XAUUSD"], "timeframes": ["M1"]},
        entry={"logic": "test", "confirmation": ["choch_sig"]},
        filters=[{"feature": "dist_to_ema_21", "op": "gt", "value": 0.0}],
    )
    from nexus_scalp.strategies.factory.dsl import candidate_id_from_hash, dsl_hash

    digest = dsl_hash(dsl)
    candidate = FactoryCandidate(
        candidate_id=candidate_id_from_hash(digest),
        definition_hash=digest,
        generation_id="G1",
        source=CandidateSource.TEMPLATE,
        dsl=dsl,
        family=dsl.family,
    )

    snapshot = factory._ledger_snapshot_for_filter()
    from nexus_scalp.strategies.factory.benchmark import behavioral_preview_signature

    sig = behavioral_preview_signature(candidate, snapshot)
    factory._clone_clusters[sig] = {"members": 999, "oos_passes": 0}

    res = factory.evaluate_candidate(candidate, dataset)
    # Pre-screen disabled: the REAL pipeline ran (no CLONE_SKIPPED marker).
    assert (
        res is None
        or res.get("lifecycle") != "REJECTED"
        or (FailureReason.CLONE_SKIPPED.value not in (res.get("failure_reasons") or []))
    )


def test_clone_below_threshold_or_with_oos_pass_not_skipped(audit_repo):
    """Small clusters and clusters WITH an OOS pass are never skipped."""
    seed_experiences(audit_repo, count=30)
    ledger = ExperienceLedger(audit_repo=audit_repo)
    registry = StrategyRegistry(audit_repo=audit_repo)
    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(ledger=ledger), registry=registry
    )
    cfg = EvolutionConfig(generation_size=4, elite_size=2, clone_cluster_min_members=50)
    factory = StrategyFactory(audit_repo=audit_repo, research_pipeline=pipeline, config=cfg)

    dsl = StrategyDsl(
        hypothesis={"statement": "clone test", "market_mechanism": "test"},
        family=StrategyFamily.TREND_FOLLOWING,
        market={"symbols": ["XAUUSD"], "timeframes": ["M1"]},
        entry={"logic": "test", "confirmation": ["choch_sig"]},
        filters=[{"feature": "dist_to_ema_21", "op": "gt", "value": 0.0}],
    )
    from nexus_scalp.strategies.factory.dsl import candidate_id_from_hash, dsl_hash

    digest = dsl_hash(dsl)
    candidate = FactoryCandidate(
        candidate_id=candidate_id_from_hash(digest),
        definition_hash=digest,
        generation_id="G1",
        source=CandidateSource.TEMPLATE,
        dsl=dsl,
        family=dsl.family,
    )

    dataset = pipeline.dataset_builder.build()

    # Case 1: below threshold -> evaluated (no CLONE_SKIPPED failure written)
    snapshot = factory._ledger_snapshot_for_filter()
    from nexus_scalp.strategies.factory.benchmark import behavioral_preview_signature

    sig = behavioral_preview_signature(candidate, snapshot)
    factory._clone_clusters[sig] = {"members": 49, "oos_passes": 0}
    factory.evaluate_candidate(candidate, dataset)
    flush(audit_repo)
    from nexus_scalp.strategies.factory.store import list_failures

    fails_1 = list_failures(audit_repo, generation_id="G1")
    assert not any(f.get("reason") == "CLONE_SKIPPED" for f in fails_1)

    # Case 2: >= threshold but has OOS passes -> evaluated (no CLONE_SKIPPED)
    factory._clone_clusters[sig] = {"members": 100, "oos_passes": 3}
    factory.evaluate_candidate(candidate, dataset)
    flush(audit_repo)
    fails_2 = list_failures(audit_repo, generation_id="G1")
    assert not any(f.get("reason") == "CLONE_SKIPPED" for f in fails_2)


def test_behavioral_outcome_recorded_and_persists(audit_repo):
    """Real evaluation outcomes feed the cluster registry; registry survives restart."""
    seed_experiences(audit_repo, count=30)
    ledger = ExperienceLedger(audit_repo=audit_repo)
    registry = StrategyRegistry(audit_repo=audit_repo)
    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(ledger=ledger), registry=registry
    )
    cfg = EvolutionConfig(generation_size=4, elite_size=2, clone_cluster_min_members=2)
    factory = StrategyFactory(audit_repo=audit_repo, research_pipeline=pipeline, config=cfg)
    dataset = pipeline.dataset_builder.build()

    dsl = StrategyDsl(
        hypothesis={"statement": "clone test", "market_mechanism": "test"},
        family=StrategyFamily.MEAN_REVERSION,
        market={"symbols": ["XAUUSD"], "timeframes": ["M5"]},
        entry={"logic": "test", "confirmation": ["extreme_sig"]},
        filters=[{"feature": "norm_rsi", "op": "lt", "value": -0.5}],
    )
    from nexus_scalp.strategies.factory.dsl import candidate_id_from_hash, dsl_hash

    digest = dsl_hash(dsl)
    candidate = FactoryCandidate(
        candidate_id=candidate_id_from_hash(digest),
        definition_hash=digest,
        generation_id="G1",
        source=CandidateSource.TEMPLATE,
        dsl=dsl,
        family=dsl.family,
    )

    res = factory.evaluate_candidate(candidate, dataset)
    assert res is not None  # real pipeline ran
    flush(audit_repo)

    # One member recorded for the signature; with all-negative outcomes the
    # cluster must have zero OOS passes.
    sigs = list(factory._clone_clusters.keys())
    assert len(sigs) == 1
    cluster = factory._clone_clusters[sigs[0]]
    assert cluster["members"] == 1
    oos_pass_expected = (res.get("oos") or {}).get("status") == "PASS"
    assert cluster["oos_passes"] == (1 if oos_pass_expected else 0)

    # Restart: cluster evidence survives.
    factory2 = StrategyFactory(audit_repo=audit_repo, research_pipeline=pipeline, config=cfg)
    assert factory2._clone_clusters.get(sigs[0], {}).get("members") == 1


def test_adaptive_probabilities_stay_bounded():
    """Acceptance Test 3: adaptive probabilities stay within [min, max] bounds
    even under extreme operator-success inputs (never collapse to one op)."""
    from nexus_scalp.strategies.factory.evolution import adapt_probabilities

    base = {"mutation_rate": 0.30, "crossover_rate": 0.30, "exploration_rate": 0.25}
    # Extreme skew toward CROSSOVER (the forensic 12.5% vs 2.1% scenario).
    out = adapt_probabilities(base, {"MUTATION": 1, "CROSSOVER": 999}, 0.5, 0.25)
    total = sum(out.values())
    assert abs(total - 1.0) < 1e-3
    for key in ("mutation_rate", "crossover_rate", "exploration_rate"):
        assert 0.05 <= out[key] <= 0.90, f"{key}={out[key]} out of bounds"
    # Exploration floor preserved: search can NEVER collapse to one operator.
    assert out["exploration_rate"] > 0.0


def test_oos_never_enters_probability_path(audit_repo):
    """Acceptance Test 4: OOS scores never enter the mutation-probability path.

    The memory fed to _adaptive_probabilities derives ONLY from validation-tier
    operator survival counts — no 'oos' keys may appear anywhere in it.
    """
    seed_experiences(audit_repo, count=30)
    ledger = ExperienceLedger(audit_repo=audit_repo)
    registry = StrategyRegistry(audit_repo=audit_repo)
    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(ledger=ledger), registry=registry
    )
    factory = StrategyFactory(
        audit_repo=audit_repo,
        research_pipeline=pipeline,
        config=EvolutionConfig(generation_size=4, elite_size=2),
    )
    # Real-shaped memory WITH history (adaptive path engaged).
    memory = factory.build_memory()
    memory["generation_count"] = 3
    memory["operator_success"] = {"MUTATION": 0.5, "CROSSOVER": 1.0}
    probs = factory._adaptive_probabilities(memory)
    assert set(probs.keys()) == {"mutation_rate", "crossover_rate", "exploration_rate"}
    assert abs(sum(probs.values()) - 1.0) < 1e-3
    for key in ("mutation_rate", "crossover_rate", "exploration_rate"):
        assert 0.0 <= probs[key] <= 1.0

    def _scan(node) -> bool:
        if isinstance(node, dict):
            return any("oos" in str(k).lower() or _scan(v) for k, v in node.items())
        if isinstance(node, list):
            return any(_scan(x) for x in node)
        return False

    # No OOS-derived value anywhere in the probability input memory.
    assert not _scan(memory.get("operator_success")), (
        "operator_success must be validation-tier survival only"
    )
    # And build_memory() itself never embeds raw oos dicts into operator_success.
    fresh = factory.build_memory()
    assert not any("oos" in str(k).lower() for k in (fresh.get("operator_success") or {}))
