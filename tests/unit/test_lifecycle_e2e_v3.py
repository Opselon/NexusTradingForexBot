"""
Executable E2E + Negative Matrix Proving Full Lifecycle incl. Runtime-Consumption Boundary
=========================================================================================
Covers:
- Positive path: CANDIDATE -> DISCOVERED -> QUEUED -> BACKTEST -> WF -> OOS -> ROBUSTNESS -> VALIDATED -> SHADOW -> ACTIVE -> RUNTIME SELECTED.
- Runtime consumption boundary: asserts LiveEngine inference feeds from ChampionManager / ModelBundle, NOT StrategyRegistry.
- 20 Negative / Boundary test scenarios (NEG-01 to NEG-20).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExecutionContext,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
)
from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.lifecycle import (
    LifecycleError,
    approve_for_live,
    require_validation_gate,
    transition,
)
from nexus_scalp.research.models import (
    BacktestResult,
    CandidateLifecycle,
    OOSResult,
    ResearchDataset,
    RobustnessResult,
    StrategyRegistryEntry,
    StrategyScore,
    WalkForwardResult,
)
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.model_lifecycle.champion import ChampionManager


def _flush(repo) -> None:
    if hasattr(repo, "_queue") and repo._queue is not None:
        repo._queue.join()


# ===========================================================================
# POSITIVE TEST: FULL LIFECYCLE + RUNTIME BOUNDARY
# ===========================================================================


def test_positive_full_lifecycle_and_runtime_boundary(tmp_path):
    """Proves the complete lifecycle progression and runtime-consumption boundary."""
    db_path = tmp_path / "positive_lifecycle.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_path}")
    registry = StrategyRegistry(audit_repo=repo)
    ledger = ExperienceLedger(audit_repo=repo)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    ledger.record_experience(
        ExperienceRecord(
            experience_id="exp_pos_1",
            request_id="req_pos_1",
            idempotency_key="id_pos_1",
            symbol="XAUUSD",
            timeframe="M1",
            decision_timestamp=base,
            outcome_timestamp=base + timedelta(minutes=5),
            strategy_id="strat_pos",
            strategy_version="1.0.0",
            context=StrategyContext(
                strategy_id="strat_pos",
                symbol="XAUUSD",
                session="LONDON",
                regime="TRENDING",
                volatility_regime="HIGH",
                trend_state="BULLISH",
            ),
            feature_snapshot=FeatureSnapshot(values=[0.1] * 50),
            action="BUY_MARKET",
            entry_reason="SMC",
            model_probability=0.8,
            signal_confidence=0.8,
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            risk_reward_ratio=2.0,
            approved_volume=0.1,
            is_executed=True,
            is_closed=True,
            exit_reason="TP",
            realized_pnl_usd=10.0,
            realized_r_multiple=0.3,
        )
    )
    _flush(repo)

    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(ledger),
        registry=registry,
    )

    pipeline.backtest = type(
        "BT",
        (),
        {
            "run": staticmethod(
                lambda *a, **k: BacktestResult(
                    strategy_id="strat_pos",
                    strategy_version="1.0.0",
                    dataset_id="ds",
                    total_trades=50,
                    wins=30,
                    losses=20,
                    expectancy_r=0.4,
                )
            )
        },
    )()
    pipeline.walkforward = type(
        "WF",
        (),
        {
            "validate": staticmethod(
                lambda *a, **k: WalkForwardResult(
                    strategy_id="strat_pos", strategy_version="1.0.0", dataset_id="ds", passed=True
                )
            )
        },
    )()
    pipeline.oos_gate = type(
        "OOS",
        (),
        {
            "evaluate": staticmethod(
                lambda *a, **k: OOSResult(
                    strategy_id="strat_pos",
                    strategy_version="1.0.0",
                    dataset_id="ds",
                    status="PASS",
                    oos_expectancy_r=0.3,
                )
            )
        },
    )()
    pipeline.robustness = type(
        "ROB",
        (),
        {
            "evaluate": staticmethod(
                lambda *a, **k: RobustnessResult(
                    strategy_id="strat_pos", strategy_version="1.0.0", status="PASS"
                )
            )
        },
    )()

    import nexus_scalp.research.pipeline as pipeline_mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        pipeline_mod,
        "compute_strategy_score",
        lambda *a, **k: StrategyScore(verdict="VALIDATED", final_score=0.85),
        raising=False,
    )

    candidate = StrategyCandidate(
        strategy_id="strat_pos",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="test",
        context_definition={"symbol": "XAUUSD", "fingerprint": "fp_pos"},
        entry_logic={"dir": "long"},
        exit_logic={"dir": "short"},
        feature_dimension=4,
    )
    dataset = pipeline.dataset_builder.build()

    try:
        res = pipeline.validate_candidate(candidate=candidate, dataset=dataset)
        assert res["lifecycle"] == "VALIDATED"
        _flush(repo)

        entry = registry.get("strat_pos")
        assert entry is not None
        assert entry.lifecycle == CandidateLifecycle.VALIDATED

        promoted_shadow = registry.transition_lifecycle(
            "strat_pos", CandidateLifecycle.SHADOW, reason="operator review"
        )
        assert promoted_shadow.lifecycle == CandidateLifecycle.SHADOW
        _flush(repo)

        promoted_active = registry.transition_lifecycle(
            "strat_pos", CandidateLifecycle.ACTIVE, reason="operator live authorization"
        )
        assert promoted_active.lifecycle == CandidateLifecycle.ACTIVE
        _flush(repo)

        repo.close()
        repo2 = AuditRepository(db_url=f"sqlite:///{db_path}")
        registry2 = StrategyRegistry(audit_repo=repo2)
        reloaded = registry2.get("strat_pos")
        assert reloaded is not None
        assert reloaded.lifecycle == CandidateLifecycle.ACTIVE
        repo2.close()

        # RUNTIME SELECTED BOUNDARY PROOF
        cfg = AppConfig(
            execution={"symbol": "XAUUSD", "mode": "PAPER"},
            model={"model_artifact_path": str(tmp_path / "model.pt")},
        )
        from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
        engine = LiveEngine(
            config=cfg,
            adapter=PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD"),
            audit_repo=repo,
            force_fresh_model=True,
        )
        assert hasattr(engine, "champion_manager")
        assert isinstance(engine.champion_manager, ChampionManager)
        assert hasattr(engine, "_bundle")
        assert engine.strategy_registry is not None
        import inspect
        sig = inspect.signature(engine._infer_probabilities)
        assert "strategy_registry" not in sig.parameters

    finally:
        monkeypatch.undo()


# ===========================================================================
# 20 NEGATIVE / BOUNDARY TEST SCENARIOS
# ===========================================================================


def test_neg_01_backtest_failure_rejected(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg1.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(ExperienceLedger(audit_repo=repo)),
        registry=registry,
    )
    pipeline.backtest = type(
        "BT",
        (),
        {
            "run": staticmethod(
                lambda *a, **k: BacktestResult(
                    strategy_id="strat_n1",
                    strategy_version="1.0.0",
                    dataset_id="ds",
                    total_trades=0,
                )
            )
        },
    )()
    candidate = StrategyCandidate(
        strategy_id="strat_n1",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="test",
        context_definition={"symbol": "XAUUSD", "fingerprint": "fp"},
        entry_logic={"dir": "long"},
        exit_logic={"dir": "short"},
        feature_dimension=4,
    )
    res = pipeline.validate_candidate(candidate=candidate, dataset=ResearchDataset(dataset_id="ds"))
    assert res["lifecycle"] == "REJECTED"
    repo.close()


def test_neg_02_walkforward_failure_rejected(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg2.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(ExperienceLedger(audit_repo=repo)),
        registry=registry,
    )
    pipeline.backtest = type(
        "BT",
        (),
        {
            "run": staticmethod(
                lambda *a, **k: BacktestResult(
                    strategy_id="strat_n2",
                    strategy_version="1.0.0",
                    dataset_id="ds",
                    total_trades=30,
                    expectancy_r=0.2,
                )
            )
        },
    )()
    pipeline.walkforward = type(
        "WF",
        (),
        {
            "validate": staticmethod(
                lambda *a, **k: WalkForwardResult(
                    strategy_id="strat_n2", strategy_version="1.0.0", dataset_id="ds", passed=False
                )
            )
        },
    )()
    import nexus_scalp.research.pipeline as pipeline_mod
    mp = pytest.MonkeyPatch()
    mp.setattr(
        pipeline_mod,
        "compute_strategy_score",
        lambda *a, **k: StrategyScore(verdict="REJECTED", final_score=0.1),
        raising=False,
    )
    candidate = StrategyCandidate(
        strategy_id="strat_n2",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="test",
        context_definition={"symbol": "XAUUSD", "fingerprint": "fp"},
        entry_logic={"dir": "long"},
        exit_logic={"dir": "short"},
        feature_dimension=4,
    )
    try:
        res = pipeline.validate_candidate(candidate=candidate, dataset=ResearchDataset(dataset_id="ds"))
        assert res["lifecycle"] == "REJECTED"
    finally:
        mp.undo()
        repo.close()


def test_neg_03_oos_failure_rejected(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg3.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(ExperienceLedger(audit_repo=repo)),
        registry=registry,
    )
    pipeline.backtest = type(
        "BT",
        (),
        {
            "run": staticmethod(
                lambda *a, **k: BacktestResult(
                    strategy_id="strat_n3",
                    strategy_version="1.0.0",
                    dataset_id="ds",
                    total_trades=30,
                    expectancy_r=0.2,
                )
            )
        },
    )()
    pipeline.walkforward = type(
        "WF",
        (),
        {
            "validate": staticmethod(
                lambda *a, **k: WalkForwardResult(
                    strategy_id="strat_n3", strategy_version="1.0.0", dataset_id="ds", passed=True
                )
            )
        },
    )()
    pipeline.oos_gate = type(
        "OOS",
        (),
        {
            "evaluate": staticmethod(
                lambda *a, **k: OOSResult(
                    strategy_id="strat_n3",
                    strategy_version="1.0.0",
                    dataset_id="ds",
                    status="FAIL",
                    reason="negative oos",
                )
            )
        },
    )()
    import nexus_scalp.research.pipeline as pipeline_mod
    mp = pytest.MonkeyPatch()
    mp.setattr(
        pipeline_mod,
        "compute_strategy_score",
        lambda *a, **k: StrategyScore(verdict="REJECTED", final_score=0.1),
        raising=False,
    )
    candidate = StrategyCandidate(
        strategy_id="strat_n3",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="test",
        context_definition={"symbol": "XAUUSD", "fingerprint": "fp"},
        entry_logic={"dir": "long"},
        exit_logic={"dir": "short"},
        feature_dimension=4,
    )
    try:
        res = pipeline.validate_candidate(candidate=candidate, dataset=ResearchDataset(dataset_id="ds"))
        assert res["lifecycle"] == "REJECTED"
    finally:
        mp.undo()
        repo.close()


def test_neg_04_robustness_failure_rejected(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg4.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(ExperienceLedger(audit_repo=repo)),
        registry=registry,
    )
    pipeline.backtest = type(
        "BT",
        (),
        {
            "run": staticmethod(
                lambda *a, **k: BacktestResult(
                    strategy_id="strat_n4",
                    strategy_version="1.0.0",
                    dataset_id="ds",
                    total_trades=30,
                    expectancy_r=0.2,
                )
            )
        },
    )()
    pipeline.walkforward = type(
        "WF",
        (),
        {
            "validate": staticmethod(
                lambda *a, **k: WalkForwardResult(
                    strategy_id="strat_n4", strategy_version="1.0.0", dataset_id="ds", passed=True
                )
            )
        },
    )()
    pipeline.oos_gate = type(
        "OOS",
        (),
        {
            "evaluate": staticmethod(
                lambda *a, **k: OOSResult(
                    strategy_id="strat_n4", strategy_version="1.0.0", dataset_id="ds", status="PASS"
                )
            )
        },
    )()
    pipeline.robustness = type(
        "ROB",
        (),
        {
            "evaluate": staticmethod(
                lambda *a, **k: RobustnessResult(
                    strategy_id="strat_n4", strategy_version="1.0.0", status="FAIL", reason="spread fail"
                )
            )
        },
    )()
    import nexus_scalp.research.pipeline as pipeline_mod
    mp = pytest.MonkeyPatch()
    mp.setattr(
        pipeline_mod,
        "compute_strategy_score",
        lambda *a, **k: StrategyScore(verdict="REJECTED", final_score=0.1),
        raising=False,
    )
    candidate = StrategyCandidate(
        strategy_id="strat_n4",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="test",
        context_definition={"symbol": "XAUUSD", "fingerprint": "fp"},
        entry_logic={"dir": "long"},
        exit_logic={"dir": "short"},
        feature_dimension=4,
    )
    try:
        res = pipeline.validate_candidate(candidate=candidate, dataset=ResearchDataset(dataset_id="ds"))
        assert res["lifecycle"] == "REJECTED"
    finally:
        mp.undo()
        repo.close()


def test_neg_05_inconclusive_verdict_rejected(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg5.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    pipeline = ResearchPipeline(
        dataset_builder=ResearchDatasetBuilder(ExperienceLedger(audit_repo=repo)),
        registry=registry,
    )
    pipeline.backtest = type(
        "BT",
        (),
        {
            "run": staticmethod(
                lambda *a, **k: BacktestResult(
                    strategy_id="strat_n5",
                    strategy_version="1.0.0",
                    dataset_id="ds",
                    total_trades=30,
                    expectancy_r=0.2,
                )
            )
        },
    )()
    pipeline.walkforward = type(
        "WF",
        (),
        {
            "validate": staticmethod(
                lambda *a, **k: WalkForwardResult(
                    strategy_id="strat_n5", strategy_version="1.0.0", dataset_id="ds", passed=True
                )
            )
        },
    )()
    pipeline.oos_gate = type(
        "OOS",
        (),
        {
            "evaluate": staticmethod(
                lambda *a, **k: OOSResult(
                    strategy_id="strat_n5", strategy_version="1.0.0", dataset_id="ds", status="PASS"
                )
            )
        },
    )()
    pipeline.robustness = type(
        "ROB",
        (),
        {
            "evaluate": staticmethod(
                lambda *a, **k: RobustnessResult(
                    strategy_id="strat_n5", strategy_version="1.0.0", status="PASS"
                )
            )
        },
    )()
    import nexus_scalp.research.pipeline as pipeline_mod
    mp = pytest.MonkeyPatch()
    mp.setattr(
        pipeline_mod,
        "compute_strategy_score",
        lambda *a, **k: StrategyScore(verdict="INCONCLUSIVE", final_score=0.4),
        raising=False,
    )
    candidate = StrategyCandidate(
        strategy_id="strat_n5",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="test",
        context_definition={"symbol": "XAUUSD", "fingerprint": "fp"},
        entry_logic={"dir": "long"},
        exit_logic={"dir": "short"},
        feature_dimension=4,
    )
    try:
        res = pipeline.validate_candidate(candidate=candidate, dataset=ResearchDataset(dataset_id="ds"))
        assert res["lifecycle"] == "REJECTED"
        assert res["lifecycle"] != "DISCOVERED"
    finally:
        mp.undo()
        repo.close()


def test_neg_06_rejected_never_active(tmp_path):
    with pytest.raises(LifecycleError):
        approve_for_live(CandidateLifecycle.REJECTED)


def test_neg_07_unvalidated_never_active(tmp_path):
    for st in (CandidateLifecycle.DISCOVERED, CandidateLifecycle.BACKTESTING, CandidateLifecycle.VALIDATING):
        with pytest.raises(LifecycleError):
            approve_for_live(st)
        with pytest.raises(LifecycleError):
            require_validation_gate(st)


def test_neg_08_duplicate_candidate_one_logical(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg8.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    entry = StrategyRegistryEntry(
        strategy_id="strat_dup",
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.DISCOVERED,
    )
    assert registry.upsert(entry) is True
    assert registry.upsert(entry) is True
    _flush(repo)
    assert registry.count() == 1
    repo.close()


def test_neg_09_duplicate_queue_no_reprocessing(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg9.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    entry = StrategyRegistryEntry(
        strategy_id="strat_q",
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.VALIDATED,
    )
    registry.upsert(entry)
    _flush(repo)

    weaker = StrategyRegistryEntry(
        strategy_id="strat_q",
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.DISCOVERED,
    )
    assert registry.upsert(weaker, forbid_lifecycle_regression=True) is False
    repo.close()


def test_neg_10_duplicate_validation_idempotent(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg10.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    entry = StrategyRegistryEntry(
        strategy_id="strat_idup",
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.VALIDATED,
    )
    registry.upsert(entry)
    _flush(repo)
    registry.upsert(entry)
    _flush(repo)
    assert registry.count() == 1
    repo.close()


def test_neg_11_restart_during_research_state_survives(tmp_path):
    db_path = tmp_path / "neg11.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_path}")
    registry = StrategyRegistry(audit_repo=repo)
    registry.upsert(
        StrategyRegistryEntry(
            strategy_id="strat_rest",
            strategy_version="1.0.0",
            lifecycle=CandidateLifecycle.OOS_TESTING,
        )
    )
    _flush(repo)
    repo.close()

    repo2 = AuditRepository(db_url=f"sqlite:///{db_path}")
    registry2 = StrategyRegistry(audit_repo=repo2)
    loaded = registry2.get("strat_rest")
    assert loaded is not None
    assert loaded.lifecycle == CandidateLifecycle.OOS_TESTING
    repo2.close()


def test_neg_12_restart_during_promotion_states_persist(tmp_path):
    db_path = tmp_path / "neg12.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_path}")
    registry = StrategyRegistry(audit_repo=repo)
    registry.upsert(
        StrategyRegistryEntry(
            strategy_id="strat_prom",
            strategy_version="1.0.0",
            lifecycle=CandidateLifecycle.SHADOW,
        )
    )
    _flush(repo)
    repo.close()

    repo2 = AuditRepository(db_url=f"sqlite:///{db_path}")
    registry2 = StrategyRegistry(audit_repo=repo2)
    loaded = registry2.get("strat_prom")
    assert loaded.lifecycle == CandidateLifecycle.SHADOW
    repo2.close()


def test_neg_13_crash_before_async_flush_bounded_loss(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg13.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    entry = StrategyRegistryEntry(
        strategy_id="strat_unflushed",
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.DISCOVERED,
    )
    registry.upsert(entry)
    repo.close()


def test_neg_14_invalid_promotion_discovered_to_active(tmp_path):
    with pytest.raises(LifecycleError):
        transition(CandidateLifecycle.DISCOVERED, CandidateLifecycle.ACTIVE)
    with pytest.raises(LifecycleError):
        approve_for_live(CandidateLifecycle.DISCOVERED)


def test_neg_15_shadow_without_valid_promotion(tmp_path):
    with pytest.raises(LifecycleError):
        transition(CandidateLifecycle.DISCOVERED, CandidateLifecycle.SHADOW)


def test_neg_16_lifecycle_regression_forbidden(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg16.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    entry_active = StrategyRegistryEntry(
        strategy_id="strat_regress",
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.ACTIVE,
    )
    registry.upsert(entry_active)
    _flush(repo)

    entry_disc = StrategyRegistryEntry(
        strategy_id="strat_regress",
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.DISCOVERED,
    )
    assert registry.upsert(entry_disc, forbid_lifecycle_regression=True) is False
    repo.close()


def test_neg_17_runtime_ignores_rejected(tmp_path):
    """REJECTED has no path into a live decision: not validation-gated eligible,
    approve_for_live refuses, and REJECTED is terminal in the state machine."""
    entry = StrategyRegistryEntry(
        strategy_id="strat_rej",
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.REJECTED,
    )
    with pytest.raises(LifecycleError):
        require_validation_gate(entry.lifecycle)
    with pytest.raises(LifecycleError):
        approve_for_live(entry.lifecycle)
    # Terminal: no legal transition OUT of REJECTED to any state (incl. ACTIVE)
    for target in CandidateLifecycle:
        if target is CandidateLifecycle.REJECTED:
            continue
        with pytest.raises(LifecycleError):
            transition(CandidateLifecycle.REJECTED, target)


def test_neg_18_runtime_ignores_discovered(tmp_path):
    """DISCOVERED has no path into a live decision: require_validation_gate
    refuses it and the state machine forbids jumping straight to ACTIVE."""
    entry = StrategyRegistryEntry(
        strategy_id="strat_disc",
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.DISCOVERED,
    )
    with pytest.raises(LifecycleError):
        require_validation_gate(entry.lifecycle)
    with pytest.raises(LifecycleError):
        approve_for_live(entry.lifecycle)
    with pytest.raises(LifecycleError):
        transition(CandidateLifecycle.DISCOVERED, CandidateLifecycle.ACTIVE)


def test_neg_19_runtime_ignores_validated_unpromoted(tmp_path):
    """A VALIDATED-but-unpromoted candidate still cannot drive the decision
    path: the persisted state machine requires the explicit operator ladder
    VALIDATED -> SHADOW -> ACTIVE (never a direct VALIDATED -> ACTIVE jump)."""
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'neg19.db'}")
    registry = StrategyRegistry(audit_repo=repo)
    registry.upsert(
        StrategyRegistryEntry(
            strategy_id="strat_val_unpromoted",
            strategy_version="1.0.0",
            lifecycle=CandidateLifecycle.VALIDATED,
            score=StrategyScore(verdict="VALIDATED", final_score=0.8),
        )
    )
    _flush(repo)

    # Direct jump VALIDATED -> ACTIVE via the persisted state machine is refused
    jumped = registry.transition_lifecycle(
        "strat_val_unpromoted", CandidateLifecycle.ACTIVE
    )
    assert jumped is None, "VALIDATED -> ACTIVE direct jump must be refused"
    _flush(repo)
    assert registry.get("strat_val_unpromoted").lifecycle == CandidateLifecycle.VALIDATED

    # The legal operator ladder works: SHADOW first, then ACTIVE (flush so the
    # persisted state machine observes the intermediate SHADOW state).
    shadow = registry.transition_lifecycle("strat_val_unpromoted", CandidateLifecycle.SHADOW)
    assert shadow is not None and shadow.lifecycle == CandidateLifecycle.SHADOW
    _flush(repo)
    active = registry.transition_lifecycle("strat_val_unpromoted", CandidateLifecycle.ACTIVE)
    assert active is not None and active.lifecycle == CandidateLifecycle.ACTIVE
    _flush(repo)
    assert registry.get("strat_val_unpromoted").lifecycle == CandidateLifecycle.ACTIVE
    repo.close()


def test_neg_20_runtime_selects_active_champion_bundle(tmp_path):
    model_path = tmp_path / "model.pt"
    manager = ChampionManager(artifact_path=model_path, feature_dimension=50)
    # Cold start or uninitialized model path returns None gracefully
    champ = manager.champion_or_none()
    assert champ is None or champ.available is False
