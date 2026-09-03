"""
BUG-233 regression: OOS fail must short-circuit the robustness gate.

Before the fix the pipeline ran every gate regardless of OOS outcome
(forensic evidence: 3766 OOS-failed runs all executed robustness).
After the fix: when oos.status != PASS the robustness gate is recorded as
BLOCKED (order_index=4, reason "OOS failed — chain stopped"), no robustness
evaluation is executed, no robustness evidence artifact is stored, and the
verdict remains REJECTED via the existing OOS hard-gate (scoring unchanged).
WF gate is untouched — it legitimately ran before OOS.

xdist-safe: no caplog/capsys, no wall-clock dependence.
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
from nexus_scalp.research.evidence import EvidenceKind, FailureClass, GateStatus, GateType
from nexus_scalp.research.models import OOSResult, RobustnessResult
from nexus_scalp.research.observability import ResearchObservabilityStore
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry


@pytest.fixture
def tmp_repo(tmp_path):  # type: ignore[no-untyped-def]
    db = tmp_path / "bug233.db"
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    yield repo
    repo.close()


def _flush(repo: AuditRepository) -> None:
    repo._queue.join()  # type: ignore[attr-defined]


def _rec(key: str, ts: datetime) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=f"req_{key}",
        idempotency_key=key,
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=ts,
        strategy_id="strat_bug233",
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id="strat_bug233",
            symbol="XAUUSD",
            session="LONDON",
            regime="RANGING_MEAN_REVERSION",
            volatility_regime="NORMAL",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id="scalp_v1", feature_dimension=50, values=[0.0] * 50
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


def _out(rec: ExperienceRecord, realized_r: float) -> ExperienceOutcome:
    return ExperienceOutcome(
        idempotency_key=rec.idempotency_key,
        execution_id=f"ticket_{rec.idempotency_key}",
        outcome_timestamp=rec.decision_timestamp + timedelta(minutes=5),
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


def _seed(repo: AuditRepository, n: int = 46) -> None:
    ledger = ExperienceLedger(audit_repo=repo)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(n):
        rec = _rec(f"bug233_{i}", base + timedelta(minutes=30 * i))
        # Positive expectancy so backtest/WF can pass and we isolate the OOS gate.
        ledger.record_experience(rec)
        ledger.record_outcome(_out(rec, realized_r=0.55))
    _flush(repo)


class _FailingOOSGate:
    """Stub OOS gate that always returns FAIL (deterministic, no dataset dependence)."""

    def evaluate(self, dataset, strategy_id: str, strategy_version: str, **_: object) -> OOSResult:  # type: ignore[no-untyped-def]
        return OOSResult(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset.dataset_id,
            in_sample_expectancy_r=0.2,
            oos_expectancy_r=-0.4,
            oos_samples=20,
            oos_win_rate=0.3,
            status="FAIL",
            reason="stub OOS fail for BUG-233 regression",
        )


class _TrackingRobustness:
    """Robustness double that records whether evaluate was called."""

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, dataset, strategy_id: str, strategy_version: str) -> RobustnessResult:  # type: ignore[no-untyped-def]
        self.calls += 1
        return RobustnessResult(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            baseline_expectancy_r=0.2,
            stress_expectancies={"spread": 0.1},
            max_degradation=0.1,
            status="PASS",
            reason="stub robustness pass",
        )


class _PassingOOSGate:
    def evaluate(self, dataset, strategy_id: str, strategy_version: str, **_: object) -> OOSResult:  # type: ignore[no-untyped-def]
        return OOSResult(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset.dataset_id,
            in_sample_expectancy_r=0.3,
            oos_expectancy_r=0.25,
            oos_samples=20,
            oos_win_rate=0.55,
            status="PASS",
            reason="stub OOS pass",
        )


def _build_pipeline(
    repo: AuditRepository,
    *,
    oos_gate: object | None = None,
    robustness: object | None = None,
) -> tuple[ResearchDatasetBuilder, StrategyRegistry, ResearchObservabilityStore, ResearchPipeline]:
    ledger = ExperienceLedger(audit_repo=repo)
    builder = ResearchDatasetBuilder(ledger=ledger)
    reg = StrategyRegistry(repo)
    obs = ResearchObservabilityStore(repo)
    pipe = ResearchPipeline(
        dataset_builder=builder,
        registry=reg,
        observability=obs,
        oos_gate=oos_gate,  # type: ignore[arg-type]
        robustness=robustness,  # type: ignore[arg-type]
    )
    return builder, reg, pipe, obs


class TestBug233GateShortCircuit:
    def test_oos_fail_blocks_robustness(self, tmp_repo: AuditRepository) -> None:
        _seed(tmp_repo, n=46)
        tracker = _TrackingRobustness()
        builder, reg, pipe, obs = _build_pipeline(
            tmp_repo, oos_gate=_FailingOOSGate(), robustness=tracker
        )
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        assert cands, "seed must yield at least one candidate"
        cand = cands[0]

        result = pipe.validate_candidate(cand, ds, run_id="RUN-BUG233-BLOCKED")
        _flush(tmp_repo)

        # Robustness must NOT have been executed (the whole point of the fix).
        assert tracker.calls == 0, "robustness gate must be skipped when OOS failed"

        gates = obs.list_gates(research_run_id="RUN-BUG233-BLOCKED")
        by_type = {g.gate_type: g for g in gates}

        # OOS gate itself is FAILED (not blocked) — it ran and produced the verdict.
        assert GateType.OOS in by_type
        assert by_type[GateType.OOS].status == GateStatus.FAILED

        # Robustness gate exists but is BLOCKED with the specified reason.
        assert GateType.ROBUSTNESS in by_type
        rob_gate = by_type[GateType.ROBUSTNESS]
        assert rob_gate.status == GateStatus.BLOCKED
        assert rob_gate.order_index == 4
        assert "OOS failed" in rob_gate.failure_reason
        assert "chain stopped" in rob_gate.failure_reason

        # No robustness evidence artifact (the gate was blocked, not evaluated).
        evidence = obs.list_evidence(research_run_id="RUN-BUG233-BLOCKED")
        kinds = {e["kind"] for e in evidence}
        assert EvidenceKind.ROBUSTNESS_RESULT.value not in kinds
        # OOS/WF/backtest evidence still present (WF ran before OOS).
        assert EvidenceKind.OOS_RESULT.value in kinds
        assert EvidenceKind.BACKTEST_RESULT.value in kinds
        assert EvidenceKind.WALK_FORWARD_RESULT.value in kinds

        # GATE_BLOCKED event emitted for robustness.
        events = obs.list_events(research_run_id="RUN-BUG233-BLOCKED")
        etypes = [e["event_type"] for e in events]
        assert "GATE_BLOCKED" in etypes

        # Verdict is REJECTED via OOS hard-gate (scoring unchanged), robustness=None handled.
        score = result.get("score") or {}
        assert score.get("verdict") == "REJECTED"
        # Scoring explains missing robustness without inventing data.
        reasons = score.get("reasons") or []
        assert any("No robustness" in r for r in reasons)

        # Registry lifecycle is REJECTED (never VALIDATED with OOS fail).
        entry = reg.get(cand.strategy_id)
        assert entry is not None
        assert entry.lifecycle.value == "REJECTED"

        # WF gate still executed (the legitimate pre-OOS gate), not blocked.
        assert GateType.WALK_FORWARD in by_type
        assert by_type[GateType.WALK_FORWARD].status in (GateStatus.PASSED, GateStatus.FAILED)

    def test_oos_pass_still_runs_robustness(self, tmp_repo: AuditRepository) -> None:
        _seed(tmp_repo, n=46)
        tracker = _TrackingRobustness()
        builder, _reg, pipe, obs = _build_pipeline(
            tmp_repo, oos_gate=_PassingOOSGate(), robustness=tracker
        )
        ds = builder.build()
        cands = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
        assert cands
        pipe.validate_candidate(cands[0], ds, run_id="RUN-BUG233-PASS")
        _flush(tmp_repo)

        assert tracker.calls == 1, "robustness must run when OOS passed"
        gates = obs.list_gates(research_run_id="RUN-BUG233-PASS")
        by_type = {g.gate_type: g for g in gates}
        assert by_type[GateType.ROBUSTNESS].status in (GateStatus.PASSED, GateStatus.FAILED)
        # Blocked must NOT appear in the happy path.
        assert by_type[GateType.ROBUSTNESS].status != GateStatus.BLOCKED
        # Evidence present for robustness in the happy path.
        evidence = obs.list_evidence(research_run_id="RUN-BUG233-PASS")
        assert EvidenceKind.ROBUSTNESS_RESULT.value in {e["kind"] for e in evidence}
