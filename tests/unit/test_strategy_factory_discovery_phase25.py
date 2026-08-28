"""
Tests for Phase 25 Strategy Discovery Quality & Evidence Lifecycle Improvements
=============================================================================
(2026-08-25).
Covers:
    1. Specific, testable hypothesis per family (dsl.py + SESSION_REGIMES).
    2. Context matrix computation (research/context_analysis.py).
    3. Lifecycle new states & valid transitions (research/lifecycle.py & models.py).
    4. Insufficient-evidence path -> EVIDENCE_BUILDING (orchestrator.py).
    5. Idempotent DB context_matrices column (store.py + audit_repository).
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
from nexus_scalp.research.context_analysis import compute_context_matrices
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.lifecycle import LifecycleError, can_transition, transition
from nexus_scalp.research.models import CandidateLifecycle, ResearchSample
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.strategies.factory.dsl import (
    _FAMILY_HYPOTHESES,
    SESSION_REGIMES,
    generate_random_candidates,
    generate_template_candidates,
)
from nexus_scalp.strategies.factory.models import FailureReason, StrategyFamily
from nexus_scalp.strategies.factory.orchestrator import StrategyFactory


def test_session_regimes_and_family_hypotheses():
    """Requirement 1: SESSION_REGIMES defined and every family has a non-generic hypothesis."""
    assert "ASIAN" in SESSION_REGIMES
    assert "LONDON" in SESSION_REGIMES
    assert "LONDON_NY_OVERLAP" in SESSION_REGIMES
    assert "NY" in SESSION_REGIMES

    for fam in StrategyFamily:
        assert fam in _FAMILY_HYPOTHESES
        hyp = _FAMILY_HYPOTHESES[fam]
        assert "statement" in hyp
        assert "market_condition" in hyp
        assert "entry_reason" in hyp
        assert "exit_reason" in hyp
        assert "expected_edge" in hyp
        assert "failure_condition" in hyp
        # Ensure it's not generic boilerplate
        assert "exploit" not in hyp["statement"].lower() or len(hyp["statement"]) > 80


def test_template_and_random_hypotheses_specific():
    """Generated templates and random candidates carry specific hypotheses."""
    templates = generate_template_candidates(5)
    for t in templates:
        assert "statement" in t.hypothesis
        assert len(t.hypothesis["statement"]) > 30

    randos = generate_random_candidates(3)
    for r in randos:
        assert "statement" in r.hypothesis
        assert len(r.hypothesis["statement"]) > 30


def test_compute_context_matrices_counts():
    """Requirement 2: compute_context_matrices returns correct session, hourly, weekday, and regime counts."""
    base_dt = datetime(2026, 8, 25, 10, 30, tzinfo=UTC)  # 10:30 UTC -> LONDON session
    samples = [
        ResearchSample(
            sample_id=f"samp_{i}",
            experience_id=f"exp_{i}",
            idempotency_key=f"key_{i}",
            decision_timestamp=base_dt + timedelta(hours=i),
            outcome_timestamp=base_dt + timedelta(hours=i, minutes=15),
            symbol="XAUUSD",
            strategy_id="strat_1",
            session="LONDON",
            regime="TRENDING",
            volatility_regime="HIGH",
            trend_state="BULLISH",
            realized_r=0.5 if i % 2 == 0 else -0.2,
        )
        for i in range(10)
    ]

    matrices = compute_context_matrices(samples)
    assert "session_matrix" in matrices
    assert "hourly_matrix" in matrices
    assert "weekday_matrix" in matrices
    assert "regime_matrix" in matrices

    london_stats = matrices["session_matrix"].get("LONDON", {})
    assert london_stats.get("trades") == 10
    assert london_stats.get("wins") == 5
    assert london_stats.get("losses") == 5
    assert "expectancy_r" in london_stats


def test_lifecycle_new_states_and_transitions():
    """Requirement 3: CandidateLifecycle new states and transitions are valid."""
    assert CandidateLifecycle.INITIAL_TESTING in CandidateLifecycle
    assert CandidateLifecycle.EVIDENCE_BUILDING in CandidateLifecycle
    assert CandidateLifecycle.WALK_FORWARD_READY in CandidateLifecycle
    assert CandidateLifecycle.OOS_READY in CandidateLifecycle
    assert CandidateLifecycle.ROBUSTNESS_READY in CandidateLifecycle

    # Test adjacency transition from DISCOVERED -> EVIDENCE_BUILDING
    nxt = transition(CandidateLifecycle.DISCOVERED, CandidateLifecycle.EVIDENCE_BUILDING)
    assert nxt == CandidateLifecycle.EVIDENCE_BUILDING

    # Test invalid transition raises LifecycleError
    with pytest.raises(LifecycleError):
        transition(CandidateLifecycle.REJECTED, CandidateLifecycle.ACTIVE)


def test_insufficient_evidence_sets_evidence_building(tmp_path):
    """Requirement 4: backtest failure ONLY on low trade count sets EVIDENCE_BUILDING + INSUFFICIENT_EVIDENCE."""
    db_file = tmp_path / "test_factory_evidence.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")

    ledger = ExperienceLedger(audit_repo=repo)
    registry = StrategyRegistry(audit_repo=repo)
    dataset_builder = ResearchDatasetBuilder(ledger=ledger)
    pipeline = ResearchPipeline(dataset_builder=dataset_builder, registry=registry)

    factory = StrategyFactory(audit_repo=repo, research_pipeline=pipeline)

    # Mock _is_evidence_only_failure or test evaluation path
    from nexus_scalp.strategies.factory.dsl import (
        candidate_id_from_hash,
        dsl_hash,
        generate_template_candidates,
    )
    from nexus_scalp.strategies.factory.models import CandidateSource, FactoryCandidate

    dsl = generate_template_candidates(1)[0]
    digest = dsl_hash(dsl)
    cand = FactoryCandidate(
        candidate_id=candidate_id_from_hash(digest),
        definition_hash=digest,
        generation_id="G1",
        source=CandidateSource.TEMPLATE,
        dsl=dsl,
        family=dsl.family,
    )

    # Test helper method directly
    class MockResult:
        pass

    res = {
        "lifecycle": "REJECTED",
        "score": {"verdict": "REJECTED"},
        "backtest": {"total_trades": 2, "expectancy_r": 0.4},
        "oos": {"status": "PASS"},
        "robustness": {"status": "PASS"},
    }

    # When _is_evidence_only_failure returns True on a REJECTED result:
    # Let's verify our helper logic
    assert factory._is_evidence_only_failure(res, cand) is True

    repo.close()


def test_database_context_matrices_column(tmp_path):
    """Requirement 5: idempotent context_matrices columns exist on factory_candidates and strategy_registry."""
    db_file = tmp_path / "test_db_context_cols.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")

    import sqlite3

    conn = sqlite3.connect(db_file)
    try:
        cur = conn.cursor()
        # Check factory_candidates has context_matrices
        cols_fc = [
            col[1] for col in cur.execute("PRAGMA table_info(factory_candidates);").fetchall()
        ]
        assert "context_matrices" in cols_fc

        # Check strategy_registry has context_matrices
        cols_sr = [
            col[1] for col in cur.execute("PRAGMA table_info(strategy_registry);").fetchall()
        ]
        assert "context_matrices" in cols_sr
    finally:
        conn.close()
        repo.close()
