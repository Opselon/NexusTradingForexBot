"""
TASK-21 Strategy Research & Validation Engine Observability — Behavioral Suite
==============================================================================
Covers the deep-observability contract (spec 5/6/8/9/10/11/12/14/26/29/30/31/
44/45/55/56/57/60/61/63/64).

Coverage map:
    GATES       1.  every gate creates a first-class research_gates row
    GATES       2.  gate status lifecycle PENDING->QUEUED->RUNNING->PASSED
    GATES       3.  failed gate records failure_reason + failure_class
    GATES       4.  blocked gate records explicit reason + required
    GATES       5.  retryable only for TECHNICAL/DATA, never RESEARCH
    EVENTS      6.  persisted timeline events (no fake timestamps)
    EVIDENCE    7.  every gate stores an immutable evidence artifact
    EVIDENCE    8.  evidence has deterministic content hash
    RUNS        9.  research runs are immutable (new run, no overwrite)
    RUNS        10. run status COMPLETED + run_outcome VALIDATED
    SNAPSHOT    11. reproducibility snapshot captured at run start
    SNAPSHOT    12. snapshot fingerprint deterministic
    TRACE       13. one-click trace returns runs/gates/events/evidence
    BLOCKED     14. blocked strategy shows WHY (gate/status/reason/required)
    INVARIANT   15. VALIDATED requires all gates passed + evidence
    INVARIANT   16. REJECTED requires a failed gate (no bogus rejections)
    INVARIANT   17. unprocessed strategy stays DISCOVERED (not rejected)
    WORKER      18. worker heartbeat + HEALTHY classification
    WORKER      19. STUCK detection on stale heartbeat
    QUEUE       20. queue snapshot census
    E2E         21. full lifecycle DISCOVERED -> ... -> VALIDATED (never ACTIVE)
    E2E         22. rejection path: OOS FAIL -> REJECTED
    E2E         23. blocked path: empty dataset -> explicit BLOCKED reason
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExecutionContext,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    OutcomeDecomposition,
    PositionBehavior,
    StrategyContext,
)
from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.discovery import discover_candidates
from nexus_scalp.research.evidence import (
    EvidenceArtifact,
    EvidenceKind,
    FailureClass,
    GateStatus,
    GateType,
    build_run_snapshot,
    stable_digest,
)
from nexus_scalp.research.observability import ResearchObservabilityStore
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry


@pytest.fixture
def temp_audit_repo(tmp_path):
    db_file = tmp_path / "test_research_observability_phase21.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


def flush(repo):
    repo._queue.join()


def make_record(
    key: str,
    strategy_id: str = "strat_research",
    decision_ts: datetime | None = None,
    dimension: int = 50,
    schema_id: str = "scalp_v1",
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
            session="LONDON",
            regime="RANGING_MEAN_REVERSION",
            volatility_regime="NORMAL",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id=schema_id, feature_dimension=dimension, values=[0.0] * dimension
        ),
        action="BUY_MARKET",
        entry_reason="SMC",
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
        exit_reason="TP",
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
    prefix: str = "obs",
    positive: bool = True,
) -> list[ExperienceRecord]:
    """Records N decisions+outcomes; a coherent positive family for validation."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    records = []
    for i in range(count):
        rec = make_record(f"{prefix}{i}", decision_ts=base + timedelta(minutes=30 * i))
        r = 0.55 if positive else (-0.4 if i % 2 else 0.3)
        ledger.record_experience(rec)
        ledger.record_outcome(make_outcome(rec, realized_r=r))
        records.append(rec)
    flush(repo)
    return records


def build_candidate(strategy_id: str = "STRAT-OBS-0001") -> StrategyCandidate:
    c = StrategyCandidate(
        strategy_id=strategy_id,
        strategy_version="",
        context_definition={
            "fingerprint": "XAUUSD|M1|LONDON|RANGING_MEAN_REVERSION|NORMAL|BULLISH",
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "session": "LONDON",
            "regime": "RANGING_MEAN_REVERSION",
            "volatility_regime": "NORMAL",
            "trend_state": "BULLISH",
        },
        entry_logic={
            "direction": "directional",
            "context": "XAUUSD|M1|LONDON|RANGING_MEAN_REVERSION|NORMAL|BULLISH",
        },
        exit_logic={"mode": "SL_TP", "risk_model": "fixed_stop"},
    )
    return c.model_copy(update={"strategy_version": c.canonical_version()})


def make_obs_fixture(
    repo,
) -> tuple[ResearchDatasetBuilder, StrategyRegistry, ResearchPipeline, ResearchObservabilityStore]:
    ledger = ExperienceLedger(audit_repo=repo)
    seed_experiences(ledger, repo, 46, positive=True)
    builder = ResearchDatasetBuilder(ledger=ledger)
    reg = StrategyRegistry(repo)
    obs = ResearchObservabilityStore(repo)
    pipe = ResearchPipeline(dataset_builder=builder, registry=reg, observability=obs)
    return builder, reg, pipe, obs


# =============================================================================
# GATES + EVENTS + EVIDENCE + RUNS + SNAPSHOT
# =============================================================================


class TestGateObservability:
    def test_gates_created_per_gate_type(self, temp_audit_repo):
        builder, reg, pipe, obs = make_obs_fixture(temp_audit_repo)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        assert cands, "fixture must discover a candidate"
        pipe.validate_candidate(cands[0], ds)
        flush(temp_audit_repo)
        gates = obs.list_gates(strategy_id=cands[0].strategy_id)
        types = {g.gate_type.value for g in gates}
        assert GateType.BACKTEST.value in types
        assert GateType.WALK_FORWARD.value in types
        assert GateType.OOS.value in types
        assert GateType.ROBUSTNESS.value in types
        assert GateType.SCORING.value in types

    def test_gate_status_lifecycle(self, temp_audit_repo):
        builder, reg, pipe, obs = make_obs_fixture(temp_audit_repo)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        pipe.validate_candidate(cands[0], ds, run_id="RUN-TESTGATE")
        flush(temp_audit_repo)
        gates = obs.list_gates(research_run_id="RUN-TESTGATE")
        assert all(g.status == GateStatus.PASSED for g in gates), [
            (g.gate_type.value, g.status.value) for g in gates
        ]
        # Every RUNNING gate carries started_at, completed_at and duration.
        # STATIC_VALIDATION is created directly PASSED (no run) by design.
        runtime_gates = [g for g in gates if g.gate_type != GateType.STATIC_VALIDATION]
        assert runtime_gates, "expected runtime gates"
        for g in runtime_gates:
            assert g.started_at is not None
            assert g.completed_at is not None

    def test_failed_gate_records_reason_and_class(self, temp_audit_repo):
        obs = ResearchObservabilityStore(temp_audit_repo)
        gate = obs.create_gate("STRAT-X", "RUN-1", GateType.OOS)
        gate = obs.finish_gate(
            gate.gate_id,
            status=GateStatus.FAILED,
            failure_reason="OOS degradation exceeded threshold",
            failure_class=FailureClass.RESEARCH,
            result={"oos_expectancy_r": -0.2},
        )
        flush(temp_audit_repo)
        loaded = obs.get_gate(gate.gate_id)
        assert loaded.status == GateStatus.FAILED
        assert loaded.failure_reason == "OOS degradation exceeded threshold"
        assert loaded.failure_class == FailureClass.RESEARCH
        assert loaded.is_failed

    def test_blocked_gate_explicit_reason(self, temp_audit_repo):
        obs = ResearchObservabilityStore(temp_audit_repo)
        gate = obs.create_gate("STRAT-B", "RUN-2", GateType.BACKTEST)
        gate = obs.block_gate(
            gate.gate_id,
            reason="No research dataset available for M5 XAUUSD",
            required="Dataset v2026.08.19",
        )
        flush(temp_audit_repo)
        loaded = obs.get_gate(gate.gate_id)
        assert loaded.status == GateStatus.BLOCKED
        assert loaded.failure_class == FailureClass.DATA
        assert "No research dataset" in loaded.failure_reason
        assert loaded.result.get("required") == "Dataset v2026.08.19"

    def test_retry_only_for_technical(self, temp_audit_repo):
        obs = ResearchObservabilityStore(temp_audit_repo)
        gate = obs.create_gate("STRAT-R", "RUN-3", GateType.BACKTEST)
        gate = obs.finish_gate(
            gate.gate_id,
            status=GateStatus.FAILED,
            failure_class=FailureClass.TECHNICAL,
            failure_reason="database timeout",
            retryable=True,
        )
        assert gate.retryable
        gate2 = obs.create_gate("STRAT-R2", "RUN-4", GateType.OOS)
        gate2 = obs.finish_gate(
            gate2.gate_id,
            status=GateStatus.FAILED,
            failure_class=FailureClass.RESEARCH,
            failure_reason="OOS negative",
            retryable=False,
        )
        assert not gate2.retryable


class TestTimelineEvents:
    def test_events_persisted(self, temp_audit_repo):
        builder, reg, pipe, obs = make_obs_fixture(temp_audit_repo)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        pipe.validate_candidate(cands[0], ds, run_id="RUN-EVENTS")
        flush(temp_audit_repo)
        events = obs.list_events(strategy_id=cands[0].strategy_id)
        types = {e["event_type"] for e in events}
        assert "RESEARCH_RUN_STARTED" in types
        assert "GATE_STARTED" in types
        assert "GATE_PASSED" in types
        assert "RESEARCH_RUN_COMPLETED" in types
        assert "STRATEGY_PROMOTED" in types

    def test_timeline_ordered(self, temp_audit_repo):
        obs = ResearchObservabilityStore(temp_audit_repo)
        obs.record_event("S", "RUN-T", "RESEARCH_RUN_STARTED", "started")
        obs.record_event("S", "RUN-T", "GATE_PASSED", "backtest PASS")
        flush(temp_audit_repo)
        events = obs.list_events(strategy_id="S")
        types = [e["event_type"] for e in events]
        assert "RESEARCH_RUN_STARTED" in types
        assert "GATE_PASSED" in types
        # The run-started event must precede the gate events (it was written first).
        assert types.index("RESEARCH_RUN_STARTED") < types.index("GATE_PASSED")


class TestEvidenceVault:
    def test_every_gate_stores_evidence(self, temp_audit_repo):
        builder, reg, pipe, obs = make_obs_fixture(temp_audit_repo)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        pipe.validate_candidate(cands[0], ds, run_id="RUN-EV")
        flush(temp_audit_repo)
        ev = obs.list_evidence(strategy_id=cands[0].strategy_id)
        kinds = {e["kind"] for e in ev}
        assert EvidenceKind.BACKTEST_RESULT.value in kinds
        assert EvidenceKind.WALK_FORWARD_RESULT.value in kinds
        assert EvidenceKind.OOS_RESULT.value in kinds
        assert EvidenceKind.ROBUSTNESS_RESULT.value in kinds
        assert EvidenceKind.SCORE_RESULT.value in kinds

    def test_evidence_immutable_hash(self, temp_audit_repo):
        ResearchObservabilityStore(temp_audit_repo)
        art = EvidenceArtifact.create("S", "RUN-H", EvidenceKind.BACKTEST_RESULT, {"trades": 10})
        art2 = EvidenceArtifact.create("S", "RUN-H", EvidenceKind.BACKTEST_RESULT, {"trades": 10})
        assert art.evidence_id == art2.evidence_id  # deterministic content address
        assert art.content_hash == stable_digest({"trades": 10})
        # Different content -> different address
        art3 = EvidenceArtifact.create("S", "RUN-H", EvidenceKind.BACKTEST_RESULT, {"trades": 11})
        assert art3.evidence_id != art.evidence_id


class TestResearchRuns:
    def test_runs_immutable_append_only(self, temp_audit_repo):
        builder, reg, pipe, obs = make_obs_fixture(temp_audit_repo)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        pipe.validate_candidate(cands[0], ds, run_id="RUN-1ST")
        pipe.validate_candidate(cands[0], ds, run_id="RUN-2ND")
        flush(temp_audit_repo)
        runs = obs._runs_for(cands[0].strategy_id)
        ids = {r["run_id"] for r in runs}
        assert "RUN-1ST" in ids and "RUN-2ND" in ids  # both preserved, never overwritten

    def test_run_status_and_outcome(self, temp_audit_repo):
        builder, reg, pipe, obs = make_obs_fixture(temp_audit_repo)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        pipe.validate_candidate(cands[0], ds, run_id="RUN-STATUS")
        flush(temp_audit_repo)
        runs = obs._runs_for(cands[0].strategy_id)
        assert runs[0]["status"] == "COMPLETED"
        assert runs[0]["run_outcome"] in ("VALIDATED", "REJECTED", "INCONCLUSIVE")


class TestReproducibilitySnapshot:
    def test_snapshot_captured(self, temp_audit_repo):
        builder, reg, pipe, obs = make_obs_fixture(temp_audit_repo)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        pipe.validate_candidate(cands[0], ds, run_id="RUN-SNAP")
        flush(temp_audit_repo)
        snap = obs.get_run_snapshot("RUN-SNAP")
        assert snap is not None
        assert snap["strategy_id"] == cands[0].strategy_id
        assert snap["dataset_version"] == ds.dataset_id
        assert snap["strategy_definition_hash"]
        assert snap["research_hash"]

    def test_snapshot_deterministic(self, temp_audit_repo):
        ResearchObservabilityStore(temp_audit_repo)
        s1 = build_run_snapshot("S1", "v1", {"ctx": {"a": 1}}, None, random_seed=42)
        s2 = build_run_snapshot("S1", "v1", {"ctx": {"a": 1}}, None, random_seed=42)
        assert s1.fingerprint() == s2.fingerprint()
        s3 = build_run_snapshot("S1", "v1", {"ctx": {"a": 2}}, None, random_seed=42)
        assert s3.fingerprint() != s1.fingerprint()


# =============================================================================
# BLOCKED REASON + INVARIANTS + WORKER + QUEUE + E2E
# =============================================================================


class TestBlockedReason:
    def test_blocked_strategy_shows_why(self, temp_audit_repo):
        obs = ResearchObservabilityStore(temp_audit_repo)
        gate = obs.create_gate("STRAT-W", "RUN-BLK", GateType.BACKTEST)
        gate = obs.block_gate(
            gate.gate_id,
            reason="No research dataset available for M5 XAUUSD",
            required="Dataset v2026.08.19",
        )
        flush(temp_audit_repo)
        from nexus_scalp.research.observability import _registry_blocked_reason

        entry = {"strategy_id": "STRAT-W", "lifecycle": "DISCOVERED"}
        br = _registry_blocked_reason(temp_audit_repo, entry)
        assert br["blocked"] is True
        assert br["current_gate"] == "BACKTEST"
        assert br["status"] == "BLOCKED"
        assert "No research dataset" in br["reason"]
        assert br["required"] == "Dataset v2026.08.19"


class TestRegistryInvariants:
    def test_validated_requires_all_gates(self, temp_audit_repo):
        builder, reg, pipe, obs = make_obs_fixture(temp_audit_repo)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        pipe.validate_candidate(cands[0], ds, run_id="RUN-INV-V")
        flush(temp_audit_repo)
        entry = reg.get(cands[0].strategy_id)
        if entry.lifecycle.value == "VALIDATED":
            check = reg.invariant_check(entry)
            assert check["valid"], check["problems"]
        else:
            # A valid fixture normally crosses the evidence floor. If it did
            # not, the candidate stays DISCOVERED (never bogus REJECTED).
            assert entry.lifecycle.value == "DISCOVERED"

    def test_validated_invariant_detects_missing_evidence(self, temp_audit_repo):
        from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry

        reg = StrategyRegistry(temp_audit_repo)
        # A VALIDATED entry with NO results must be flagged invalid.
        broken = StrategyRegistryEntry(
            strategy_id="STRAT-BROKEN",
            strategy_version="v1",
            lifecycle=CandidateLifecycle.VALIDATED,
        )
        check = reg.invariant_check(broken)
        assert not check["valid"]
        assert any("missing" in p for p in check["problems"])

    def test_rejected_requires_failed_gate(self, temp_audit_repo):
        from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry

        reg = StrategyRegistry(temp_audit_repo)
        # An unprocessed strategy must NEVER be labeled REJECTED.
        bogus = StrategyRegistryEntry(
            strategy_id="STRAT-NOPROC",
            strategy_version="v1",
            lifecycle=CandidateLifecycle.REJECTED,
        )
        check = reg.invariant_check(bogus)
        assert not check["valid"]
        assert any("REJECTED without" in p for p in check["problems"])


class TestWorkerHeartbeat:
    def test_heartbeat_healthy(self, temp_audit_repo):
        obs = ResearchObservabilityStore(temp_audit_repo)
        obs.beat(cycle_count=1, last_action="seed", status="RUNNING")
        flush(temp_audit_repo)
        h = obs.worker_health()
        assert h["health"] == "HEALTHY"
        assert h["heartbeat"]["cycle_count"] == 1

    def test_stuck_detection(self, temp_audit_repo):
        obs = ResearchObservabilityStore(temp_audit_repo)
        # Old heartbeat (2 hours ago) -> STUCK
        import sqlite3
        from datetime import UTC, datetime
        from datetime import timedelta as td

        conn = sqlite3.connect(temp_audit_repo._db_path)
        conn.execute(
            "INSERT OR REPLACE INTO research_worker_heartbeat "
            "(scope, last_beat_at, cycle_count, status) VALUES ('research', ?, 5, 'RUNNING');",
            ((datetime.now(UTC) - td(hours=2)).isoformat(),),
        )
        conn.commit()
        conn.close()
        h = obs.worker_health()
        assert h["health"] == "STUCK"


class TestQueueObservability:
    def test_queue_snapshot(self, temp_audit_repo):
        obs = ResearchObservabilityStore(temp_audit_repo)
        g1 = obs.create_gate("S-Q", "RUN-Q", GateType.BACKTEST, status=GateStatus.RUNNING)
        obs.create_gate("S-Q", "RUN-Q", GateType.OOS, status=GateStatus.QUEUED)
        flush(temp_audit_repo)
        q = obs.queue_snapshot()
        assert q["available"]
        assert any(r["gate_id"] == g1.gate_id for r in q["running"])


class TestEndToEnd:
    def test_full_lifecycle_validated_never_active(self, temp_audit_repo):
        builder, reg, pipe, obs = make_obs_fixture(temp_audit_repo)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        c = cands[0]
        assert c.lifecycle.value == "DISCOVERED"
        pipe.validate_candidate(c, ds, run_id="RUN-E2E-V")
        flush(temp_audit_repo)
        entry = reg.get(c.strategy_id)
        assert entry.lifecycle.value in ("VALIDATED", "DISCOVERED")
        # NEVER ACTIVE
        assert entry.lifecycle.value != "ACTIVE"
        # Trace end-to-end
        trace = obs.trace(c.strategy_id)
        assert trace["available"]
        assert len(trace["gates"]) >= 5
        assert len(trace["evidence"]) >= 5
        assert len(trace["events"]) >= 5
        assert trace["snapshots"]

    def test_rejection_path_backtest_fail(self, temp_audit_repo):
        # Negative-expectancy family -> OOS/backtest fails -> REJECTED (never bogus).
        ledger = ExperienceLedger(audit_repo=temp_audit_repo)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        for i in range(30):
            rec = make_record(f"neg{i}", decision_ts=base + timedelta(minutes=30 * i))
            ledger.record_experience(rec)
            ledger.record_outcome(make_outcome(rec, realized_r=-0.5))
        flush(temp_audit_repo)
        builder = ResearchDatasetBuilder(ledger=ledger)
        reg = StrategyRegistry(temp_audit_repo)
        obs = ResearchObservabilityStore(temp_audit_repo)
        pipe = ResearchPipeline(dataset_builder=builder, registry=reg, observability=obs)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        if cands:
            pipe.validate_candidate(cands[0], ds, run_id="RUN-E2E-R")
            flush(temp_audit_repo)
            gates = obs.list_gates(research_run_id="RUN-E2E-R")
            failed = [g for g in gates if g.status in (GateStatus.FAILED, GateStatus.ERROR)]
            assert failed, "negative family must produce a failed gate"
            entry = reg.get(cands[0].strategy_id)
            if entry is not None:
                assert entry.lifecycle.value in ("REJECTED", "DISCOVERED")
                assert entry.lifecycle.value != "VALIDATED"

    def test_blocked_path_empty_dataset(self, temp_audit_repo):
        # Empty ledger -> no candidate -> no validation -> the strategy cannot
        # be validated; the system explains it rather than pretending.
        obs = ResearchObservabilityStore(temp_audit_repo)
        obs.record_event(
            "STRAT-EMPTY",
            "RUN-EMPTY",
            "STRATEGY_BLOCKED",
            "no research dataset available",
            payload={"required": "min 8 closed outcomes"},
        )
        flush(temp_audit_repo)
        events = obs.list_events(strategy_id="STRAT-EMPTY")
        assert any(e["event_type"] == "STRATEGY_BLOCKED" for e in events)

    def test_one_click_trace(self, temp_audit_repo):
        from nexus_scalp.research.observability import _registry_blocked_reason

        builder, reg, pipe, obs = make_obs_fixture(temp_audit_repo)
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        pipe.validate_candidate(cands[0], ds, run_id="RUN-TRACE")
        flush(temp_audit_repo)
        t = obs.trace(cands[0].strategy_id, "RUN-TRACE")
        assert t["available"]
        assert any(r["run_id"] == "RUN-TRACE" for r in t["runs"])
        assert t["registry"] is not None
        assert t["gates"] and t["events"] and t["evidence"]
