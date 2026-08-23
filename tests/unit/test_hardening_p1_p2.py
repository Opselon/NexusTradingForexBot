"""
Hardening Regression Tests (P1 & P2)
=====================================
P1: Stale RUNNING generation sweeper (AutonomousLoopWorker.recover / sweep_stale_generations).
P2: StrategyRegistry.upsert lifecycle regression protection default (forbid_lifecycle_regression=True).
"""
from __future__ import annotations

import pytest
from datetime import datetime, UTC, timedelta

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.models import StrategyRegistryEntry, CandidateLifecycle
from nexus_scalp.strategies.factory.store import (
    upsert_generation,
    get_generation,
    sweep_stale_generations,
    set_loop_state,
    list_candidates,
)
from nexus_scalp.strategies.factory.worker import AutonomousLoopWorker
from nexus_scalp.strategies.factory.orchestrator import StrategyFactory
from nexus_scalp.strategies.factory.models import EvolutionConfig
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.experience.ledger import ExperienceLedger


def _make_factory(audit: AuditRepository, size: int = 4) -> tuple[StrategyFactory, ResearchPipeline]:
    """Builds a real StrategyFactory over the shared audit DB (LLM boundary unused here)."""
    ledger = ExperienceLedger(audit_repo=audit)
    registry = StrategyRegistry(audit_repo=audit)
    dataset_builder = ResearchDatasetBuilder(ledger=ledger)
    pipeline = ResearchPipeline(dataset_builder=dataset_builder, registry=registry)
    cfg = EvolutionConfig(generation_size=size, elite_size=2, max_generations=2)
    factory = StrategyFactory(audit_repo=audit, research_pipeline=pipeline, config=cfg)
    return factory, pipeline


_PENDING_LIFECYCLES = ("GENERATED", None, "", "DISCOVERED", "RUNNING")


def test_p1_stale_generation_sweeper(tmp_path):
    """P1: Stale RUNNING generations are marked FAILED while recent RUNNING ones are untouched."""
    db_path = tmp_path / "hardening.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()

    # Create an old RUNNING generation (40 minutes old)
    old_time = (datetime.now(UTC) - timedelta(minutes=40)).isoformat()
    upsert_generation(audit, {
        "generation_id": "gen_old_running",
        "number": 1,
        "mode": "MANUAL",
        "status": "RUNNING",
        "created_at": old_time,
    })

    # Create a fresh RUNNING generation (5 minutes old)
    fresh_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    upsert_generation(audit, {
        "generation_id": "gen_fresh_running",
        "number": 2,
        "mode": "MANUAL",
        "status": "RUNNING",
        "created_at": fresh_time,
    })

    # Create a completed generation (old)
    upsert_generation(audit, {
        "generation_id": "gen_old_completed",
        "number": 3,
        "mode": "MANUAL",
        "status": "COMPLETED",
        "created_at": old_time,
    })
    audit._queue.join()

    # Run sweeper (max_age_minutes=30)
    result = sweep_stale_generations(audit, max_age_minutes=30)
    audit._queue.join()

    assert "gen_old_running" in result["swept"]
    assert "gen_fresh_running" not in result["swept"]
    assert "gen_old_completed" not in result["swept"]

    # Verify states in DB
    old_gen = get_generation(audit, "gen_old_running")
    assert old_gen["status"] == "FAILED"

    fresh_gen = get_generation(audit, "gen_fresh_running")
    assert fresh_gen["status"] == "RUNNING"

    comp_gen = get_generation(audit, "gen_old_completed")
    assert comp_gen["status"] == "COMPLETED"


def test_p2_upsert_lifecycle_regression_protection_default(tmp_path):
    """P2: StrategyRegistry.upsert defaults to forbid_lifecycle_regression=True, refusing downgrades."""
    db_path = tmp_path / "regression.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()

    registry = StrategyRegistry(audit_repo=audit)

    # Insert a VALIDATED entry
    entry = StrategyRegistryEntry(
        strategy_id="strat_p2",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="w1",
        lifecycle=CandidateLifecycle.VALIDATED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert registry.upsert(entry) is True
    audit._queue.join()

    assert registry.get("strat_p2", "1.0.0").lifecycle == CandidateLifecycle.VALIDATED

    # Attempt to overwrite with DISCOVERED using default (forbid_lifecycle_regression=True)
    downgrade_entry = entry.model_copy(update={"lifecycle": CandidateLifecycle.DISCOVERED})
    assert registry.upsert(downgrade_entry) is False
    audit._queue.join()

    # Lifecycle must remain VALIDATED
    assert registry.get("strat_p2", "1.0.0").lifecycle == CandidateLifecycle.VALIDATED

    # Explicit administrative override (forbid_lifecycle_regression=False) must succeed
    assert registry.upsert(downgrade_entry, forbid_lifecycle_regression=False) is True
    audit._queue.join()

    assert registry.get("strat_p2", "1.0.0").lifecycle == CandidateLifecycle.DISCOVERED


# =========================================================================
# Additional QA Matrix Scenarios (1-7)
# =========================================================================

def test_scenario_1_crash_restart_recovery(tmp_path):
    """1. CRASH-RESTART-RECOVERY: stale RUNNING generation is swept to FAILED by
    AutonomousLoopWorker.recover() and resume_generation on it does NOT re-execute;
    a concurrent fresh generation is untouched."""
    db_path = tmp_path / "scenario_1.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()

    factory, _pipeline = _make_factory(audit)
    worker = AutonomousLoopWorker(factory=factory)

    # Crash simulation: a RUNNING generation created 45 min ago, loop state
    # still points at it with a STALE heartbeat (process died 45 min ago) —
    # then a restart triggers recover() which must sweep it to FAILED.
    old_time = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
    upsert_generation(audit, {
        "generation_id": "gen_crashed_stale",
        "number": 1,
        "mode": "AUTONOMOUS",
        "status": "RUNNING",
        "created_at": old_time,
    })
    set_loop_state(audit, {
        "state": "RUNNING",
        "generation_id": "gen_crashed_stale",
        "cycle_count": 5,
        "updated_at": old_time,  # stale heartbeat => genuinely crashed
    })

    # A second, genuinely-alive generation started 1 minute ago.
    fresh_time = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    upsert_generation(audit, {
        "generation_id": "gen_fresh_active",
        "number": 2,
        "mode": "AUTONOMOUS",
        "status": "RUNNING",
        "created_at": fresh_time,
    })
    audit._queue.join()

    # AutonomousLoopWorker-style recovery (sweep -> reload checkpoint -> resume).
    rec = worker.recover()
    audit._queue.join()
    # recover() resumes the checkpointed generation: it was swept to FAILED
    # first, so the resume must be a no-op (no pending candidates).
    assert rec["status"] == "RESUMED"
    assert rec.get("resumed") == 0
    assert rec.get("generation_id") == "gen_crashed_stale"

    # Stale generation must now be FAILED...
    stale_gen = get_generation(audit, "gen_crashed_stale")
    assert stale_gen["status"] == "FAILED"

    # ...and resume_generation on it must NOT re-execute anything:
    # NOT_FOUND or a no-pending resume (resumed == 0).
    res = factory.resume_generation("gen_crashed_stale")
    if res.get("status") == "NOT_FOUND":
        pass  # acceptable: recovery refuses to touch a FAILED generation
    else:
        assert res.get("status") == "RESUMED"
        assert res.get("resumed", -1) == 0

    # The fresh RUNNING generation survives recovery untouched.
    fresh_gen = get_generation(audit, "gen_fresh_active")
    assert fresh_gen["status"] == "RUNNING"


def test_scenario_7_fresh_generation_not_swept(tmp_path):
    """7. FRESH GENERATION NOT SWEPT: a RUNNING generation created NOW (age <
    30 min) survives the recover() sweep AND can still be resumed."""
    db_path = tmp_path / "scenario_7.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()

    factory, _pipeline = _make_factory(audit)
    worker = AutonomousLoopWorker(factory=factory)

    # Loop state points at a brand-new RUNNING generation.
    now_time = datetime.now(UTC).isoformat()
    upsert_generation(audit, {
        "generation_id": "gen_live_now",
        "number": 1,
        "mode": "AUTONOMOUS",
        "status": "RUNNING",
        "created_at": now_time,
    })
    set_loop_state(audit, {"state": "RUNNING", "generation_id": "gen_live_now", "cycle_count": 0})
    audit._queue.join()

    # Recovery must NOT sweep the fresh generation and must resume it.
    rec = worker.recover()
    audit._queue.join()
    assert rec.get("swept", []) == []
    assert rec.get("generation_id") == "gen_live_now"
    assert rec["status"] == "RESUMED"

    # Still RUNNING after recovery...
    live_gen = get_generation(audit, "gen_live_now")
    assert live_gen["status"] == "RUNNING"

    # ...and still resumable (no pending candidates -> no-op resume, not refusal).
    res = factory.resume_generation("gen_live_now")
    assert res["status"] == "RESUMED"
    assert res["generation_id"] == "gen_live_now"
    assert res["resumed"] == 0


def test_scenario_2_idempotent_sweep(tmp_path):
    """2. IDEMPOTENT SWEEP: running sweep twice on same DB leaves states FAILED and sweeps nothing second time."""
    db_path = tmp_path / "scenario_2.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()

    old_time = (datetime.now(UTC) - timedelta(minutes=60)).isoformat()
    upsert_generation(audit, {
        "generation_id": "gen_stale_1",
        "number": 1,
        "mode": "MANUAL",
        "status": "RUNNING",
        "created_at": old_time,
    })
    audit._queue.join()

    r1 = sweep_stale_generations(audit, max_age_minutes=30)
    audit._queue.join()
    assert "gen_stale_1" in r1["swept"]
    assert get_generation(audit, "gen_stale_1")["status"] == "FAILED"

    r2 = sweep_stale_generations(audit, max_age_minutes=30)
    audit._queue.join()
    assert r2["swept"] == []
    assert get_generation(audit, "gen_stale_1")["status"] == "FAILED"


def test_scenario_3_concurrent_write_regression_p2(tmp_path):
    """3. CONCURRENT WRITE REGRESSION (P2): two registry instances on same sqlite file; overwrite refused."""
    db_path = tmp_path / "scenario_3.db"
    audit_a = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit_b = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit_a._queue.join()
    audit_b._queue.join()

    reg_a = StrategyRegistry(audit_repo=audit_a)
    reg_b = StrategyRegistry(audit_repo=audit_b)

    entry = StrategyRegistryEntry(
        strategy_id="strat_concurrent",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="w1",
        lifecycle=CandidateLifecycle.VALIDATED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert reg_a.upsert(entry) is True
    audit_a._queue.join()

    downgrade_entry = entry.model_copy(update={"lifecycle": CandidateLifecycle.DISCOVERED, "updated_at": datetime.now(UTC)})
    assert reg_b.upsert(downgrade_entry) is False
    audit_b._queue.join()

    assert reg_a.get("strat_concurrent", "1.0.0").lifecycle == CandidateLifecycle.VALIDATED


def test_scenario_4_forward_transition_still_works(tmp_path):
    """4. FORWARD TRANSITION STILL WORKS (P2): VALIDATED -> SHADOW -> ACTIVE via transition_lifecycle succeeds."""
    db_path = tmp_path / "scenario_4.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()

    registry = StrategyRegistry(audit_repo=audit)
    entry = StrategyRegistryEntry(
        strategy_id="strat_fwd",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="w1",
        lifecycle=CandidateLifecycle.VALIDATED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    registry.upsert(entry)
    audit._queue.join()

    res1 = registry.transition_lifecycle("strat_fwd", CandidateLifecycle.SHADOW, reason="testing shadow")
    audit._queue.join()
    assert res1 is not None
    assert res1.lifecycle == CandidateLifecycle.SHADOW

    res2 = registry.transition_lifecycle("strat_fwd", CandidateLifecycle.ACTIVE, reason="testing active")
    audit._queue.join()
    assert res2 is not None
    assert res2.lifecycle == CandidateLifecycle.ACTIVE

    # Persisted read-back: both forward transitions landed on disk despite the
    # new default upsert guard (forward = strength-increasing, never refused).
    stored = registry.get("strat_fwd", "1.0.0")
    assert stored.lifecycle == CandidateLifecycle.ACTIVE


def test_scenario_5_explicit_recovery_path(tmp_path):
    """5. EXPLICIT RECOVERY PATH (P2): administrative downgrade with forbid_lifecycle_regression=False succeeds & logs lineage."""
    db_path = tmp_path / "scenario_5.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()

    registry = StrategyRegistry(audit_repo=audit)
    entry = StrategyRegistryEntry(
        strategy_id="strat_admin",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="w1",
        lifecycle=CandidateLifecycle.VALIDATED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    registry.upsert(entry)
    audit._queue.join()

    downgrade_entry = entry.model_copy(
        update={
            "lifecycle": CandidateLifecycle.DISCOVERED,
            "validation_lineage": [*entry.validation_lineage, "admin_downgrade"],
            "updated_at": datetime.now(UTC),
        }
    )
    success = registry.upsert(downgrade_entry, forbid_lifecycle_regression=False)
    audit._queue.join()
    assert success is True

    stored = registry.get("strat_admin", "1.0.0")
    assert stored.lifecycle == CandidateLifecycle.DISCOVERED
    assert "admin_downgrade" in stored.validation_lineage


def test_scenario_6_no_duplicated_generation_execution(tmp_path):
    """6. NO DUPLICATED GENERATION EXECUTION: after the sweeper marks a crashed
    generation FAILED, resume_generation must not resurrect duplicates — only the
    candidates that were NOT yet evaluated get re-run (count arithmetic)."""
    db_path = tmp_path / "scenario_6.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()

    factory, pipeline = _make_factory(audit)

    gen = factory.create_generation(size=4)
    population = factory.generate_population(gen["generation_id"], size=4)
    validation = factory.validate_population(population)
    passed = validation["passed"]
    assert passed

    dataset = pipeline.dataset_builder.build()
    # Evaluate exactly the first candidate before the simulated crash.
    factory.evaluate_candidate(passed[0], dataset)
    audit._queue.join()

    # Inspect pre-crash persisted candidates. The structural validation gate
    # already promoted every candidate to a terminal/PENDING lifecycle, so the
    # *un-evaluated* set is the whole population that resume_generation will
    # re-run — derived from the generation's candidates, not the GENERATED rows.
    pre = list_candidates(audit, generation_id=gen["generation_id"])
    assert len(pre) >= 1
    # Exactly one candidate was evaluated before the crash.
    evaluated_pre = [c for c in pre if c.get("lifecycle") not in _PENDING_LIFECYCLES]
    assert len(evaluated_pre) == 1

    # resume_generation re-evaluates every candidate that is not already in a
    # "done" lifecycle; before the crash only `passed[0]` was evaluated, so all
    # of `passed` (the rest of the structurally-valid survivors) are re-run.
    expected_resumed = len(passed) - 1
    assert expected_resumed >= 1

    # Simulate the crash: age the generation AND its loop-state heartbeat
    # past the 30-minute threshold AND set it RUNNING.
    old_time = (datetime.now(UTC) - timedelta(minutes=60)).isoformat()
    audit._queue.put_nowait((
        "UPDATE factory_generations SET created_at=?, status='RUNNING' WHERE generation_id=?;",
        (old_time, gen["generation_id"]),
    ))
    set_loop_state(audit, {
        "state": "RUNNING",
        "generation_id": gen["generation_id"],
        "cycle_count": 2,
        "updated_at": old_time,  # stale heartbeat so sweeper reclaims it
    })
    audit._queue.join()
    swept = sweep_stale_generations(audit, max_age_minutes=30)
    audit._queue.join()
    assert gen["generation_id"] in swept["swept"]
    assert get_generation(audit, gen["generation_id"])["status"] == "FAILED"

    # resume_generation must skip the already-evaluated candidate and only
    # re-run the unevaluated ones (no duplicate resurrections).
    res = factory.resume_generation(gen["generation_id"])
    assert res["status"] == "RESUMED"
    assert res["resumed"] == expected_resumed

    # Post-resume: no candidate rows lost or duplicated (upsert keyed by
    # candidate_id), and the already-evaluated candidate was NOT re-executed —
    # its persisted lifecycle is unchanged by the resume pass.
    post = list_candidates(audit, generation_id=gen["generation_id"])
    assert len(post) == len(pre)
    pre_by_id = {c["candidate_id"]: c.get("lifecycle") for c in pre}
    post_by_id = {c["candidate_id"]: c.get("lifecycle") for c in post}
    assert set(post_by_id) == set(pre_by_id)
    evaluated_id = passed[0].candidate_id
    assert post_by_id[evaluated_id] == pre_by_id[evaluated_id]


def test_b1_operational_descent_via_transition_lifecycle(tmp_path):
    """Reviewer REQUIRED_TEST 1 (B1/H1): legal operational descents
    ACTIVE→DEGRADED and ACTIVE→RETIRED must PERSIST via transition_lifecycle
    (the explicit administrative path). Plain upsert() stays guarded."""
    db_path = tmp_path / "b1_descent.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()
    registry = StrategyRegistry(audit_repo=audit)

    entry = StrategyRegistryEntry(
        strategy_id="strat_active",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="w1",
        lifecycle=CandidateLifecycle.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert registry.upsert(entry) is True
    audit._queue.join()

    # Plain upsert with a weaker state MUST be refused (guard intact).
    weaker = entry.model_copy(update={"lifecycle": CandidateLifecycle.DEGRADED})
    assert registry.upsert(weaker) is False

    # But the administrative transition path MUST persist the descent.
    out = registry.transition_lifecycle("strat_active", CandidateLifecycle.RETIRED, reason="operator retire")
    audit._queue.join()
    assert out is not None
    row = registry.get("strat_active", "1.0.0")
    assert row.lifecycle == CandidateLifecycle.RETIRED


def test_b1_shadow_to_degraded_persists(tmp_path):
    """Reviewer REQUIRED_TEST 1b: SHADOW→DEGRADED legal descent persists."""
    db_path = tmp_path / "b1_shadow.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()
    registry = StrategyRegistry(audit_repo=audit)

    entry = StrategyRegistryEntry(
        strategy_id="strat_shadow",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="w1",
        lifecycle=CandidateLifecycle.SHADOW,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert registry.upsert(entry) is True
    audit._queue.join()

    out = registry.transition_lifecycle("strat_shadow", CandidateLifecycle.DEGRADED, reason="shadow degrade")
    audit._queue.join()
    assert out is not None
    assert registry.get("strat_shadow", "1.0.0").lifecycle == CandidateLifecycle.DEGRADED


def test_a4_peer_tier_rejected_cannot_fabricate_validated(tmp_path):
    """Reviewer REQUIRED_TEST 6 (A4): plain upsert() must NOT flip REJECTED<->VALIDATED
    by raw payload fabrication. Both are strength rank 2; the guard now also refuses
    peer-tier truth rewrites (P2 hardening review A4). Real VALIDATED truth comes only
    from the pipeline register path (re-validation evidence); REJECTED->VALIDATED or
    VALIDATED->REJECTED via plain upsert is refused. The administrative transition_lifecycle
    is the only legitimate operator path and only applies to descents/off-boarding."""
    from nexus_scalp.research.models import OOSResult

    db_path = tmp_path / "a4_peer.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()
    registry = StrategyRegistry(audit_repo=audit)

    base = StrategyRegistryEntry(
        strategy_id="strat_peer",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="w1",
        lifecycle=CandidateLifecycle.REJECTED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert registry.upsert(base) is True
    audit._queue.join()

    # Fabricated VALIDATED payload under the existing definition MUST be refused.
    promoted = base.model_copy(update={"lifecycle": CandidateLifecycle.VALIDATED})
    assert registry.upsert(promoted) is False
    audit._queue.join()
    assert registry.get("strat_peer", "1.0.0").lifecycle == CandidateLifecycle.REJECTED

    # And VALIDATED->REJECTED plain upsert is also refused (no silent downgrade).
    val = base.model_copy(update={"lifecycle": CandidateLifecycle.VALIDATED, "oos": OOSResult(status="PASS", strategy_id="strat_peer", strategy_version="1.0.0", dataset_id="d")})
    assert registry.upsert(val, forbid_lifecycle_regression=False) is True  # admin seed
    audit._queue.join()
    downgrade = val.model_copy(update={"lifecycle": CandidateLifecycle.REJECTED})
    assert registry.upsert(downgrade) is False
    audit._queue.join()
    assert registry.get("strat_peer", "1.0.0").lifecycle == CandidateLifecycle.VALIDATED


def test_selfheal_respects_upsert_result(tmp_path):
    """Reviewer HIGH_RISK 2: self-heal must count only REAL repairs. On a
    SHADOW row with failing OOS, the regression guard refuses SHADOW→REJECTED,
    so repaired count must stay 0 (previously fake-incremented)."""
    from nexus_scalp.research.store import self_heal_research
    from nexus_scalp.research.models import OOSResult

    db_path = tmp_path / "selfheal.db"
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    audit._queue.join()
    registry = StrategyRegistry(audit_repo=audit)

    entry = StrategyRegistryEntry(
        strategy_id="strat_heal",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="w1",
        lifecycle=CandidateLifecycle.SHADOW,
        oos=OOSResult(status="FAIL", strategy_id="strat_heal", strategy_version="1.0.0", dataset_id="d"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert registry.upsert(entry) is True
    audit._queue.join()

    repaired = self_heal_research(audit, registry)
    audit._queue.join()
    # SHADOW(3) → REJECTED(2) refused by guard => honest count is 0
    assert repaired == 0
    assert registry.get("strat_heal", "1.0.0").lifecycle == CandidateLifecycle.SHADOW
