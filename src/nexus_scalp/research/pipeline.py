"""
Research Pipeline Orchestrator
==============================
PHASE 09B end-to-end pipeline:

    dataset -> discovery -> backtest -> walk-forward -> OOS -> robustness
        -> score -> registry

A candidate NEVER becomes live automatically. Promotion is operator-gated on
the production side. Research is OFFLINE / BACKGROUND and never blocks the
LiveEngine tick path (spec 31 / 32 / 42).

TASK-4 (family-select validation):
  * Every validation gate (backtest / walk-forward / OOS / robustness) now runs
    on the candidate's OWN context family (the `sample_ids` recorded at
    discovery) instead of the whole heterogeneous dataset. Previously a
    "LONDON RANGING" candidate was evaluated on trades from 22 different
    context families, so its OOS/expectancy/robustness were not family-specific
    evidence (TASK-4 gap).
  * When a candidate carries no family sample ids (e.g. a registry revalidate),
    gates fall back to the full dataset exactly as before.
  * The OOS gate is authoritative: a candidate whose OOS is negative is scored
    REJECTED regardless of in-sample performance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.backtest import BacktestEngine
from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.discovery import discover_candidates
from nexus_scalp.research.models import (
    CandidateLifecycle,
    ResearchDataset,
    ResearchRun,
)
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.robustness import RobustnessEngine
from nexus_scalp.research.scoring import compute_strategy_score
from nexus_scalp.research.walkforward import WalkForwardEngine

logger = get_logger("nexus_scalp.research.pipeline")


def _select_family(dataset: ResearchDataset, candidate: StrategyCandidate) -> ResearchDataset:
    """Returns a dataset restricted to the candidate's own discovery family.

    Uses `discovery_evidence.sample_ids` (recorded at discovery, TASK-4). When
    absent, returns the full dataset (legacy behavior for registry revalidates).
    """
    evidence = candidate.discovery_evidence or {}
    sample_ids = evidence.get("sample_ids") or []
    if not sample_ids:
        return dataset
    wanted = set(sample_ids)
    family = [s for s in dataset.samples if s.idempotency_key in wanted]
    if not family:
        return dataset
    return ResearchDataset(
        dataset_id=dataset.dataset_id,
        source=dataset.source,
        created_at=dataset.created_at,
        samples=family,
        source_range=dataset.source_range,
        schema_ids=dataset.schema_ids,
    )


class ResearchPipeline:
    """
    Orchestrates the full evidence pipeline for strategy candidates.

    Each stage is independently callable so operators can run stages one at a
    time and observe the result at every gate.
    """

    def __init__(
        self,
        dataset_builder: ResearchDatasetBuilder,
        registry: StrategyRegistry,
        backtest: BacktestEngine | None = None,
        walkforward: WalkForwardEngine | None = None,
        oos_gate: OOSGate | None = None,
        robustness: RobustnessEngine | None = None,
        observability: Any = None,
    ) -> None:
        self.dataset_builder = dataset_builder
        self.registry = registry
        self.backtest = backtest or BacktestEngine()
        self.walkforward = walkforward or WalkForwardEngine()
        self.oos_gate = oos_gate or OOSGate()
        self.robustness = robustness or RobustnessEngine()
        # TASK-21: optional observability facade; when absent the pipeline
        # behaves exactly as before (legacy mode).
        self.observability = observability
        self.last_run: ResearchRun | None = None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, dataset: ResearchDataset) -> list[StrategyCandidate]:
        """Runs bounded candidate discovery (never touches the registry yet)."""
        candidates = discover_candidates(
            dataset.samples,
            dataset_id=dataset.dataset_id,
        )
        logger.info(
            "[STRATEGY_RESEARCH] event=CANDIDATE_DISCOVERED",
            count=len(candidates),
            dataset=dataset.dataset_id,
        )
        return candidates

    # ------------------------------------------------------------------
    # Full validation
    # ------------------------------------------------------------------

    def validate_candidate(
        self,
        candidate: StrategyCandidate,
        dataset: ResearchDataset,
        n_folds: int = 3,
        purge_seconds: float = 0.0,
        embargo_seconds: float = 0.0,
        run_id: str | None = None,
        strategy_configuration: dict | None = None,
        random_seed: int | None = None,
    ) -> dict[str, Any]:
        """
        Runs the complete evidence pipeline for one candidate and persists the
        registry entry.

        Pipeline: BACKTESTING -> VALIDATING (walk-forward) -> OOS_TESTING ->
        ROBUSTNESS_TESTING -> VALIDATED/REJECTED (via score verdict).

        TASK-4: every gate runs on the candidate's OWN context family when the
        candidate records one.

        TASK-21: when an observability facade is attached, every gate creates a
        first-class research_gates row (RUNNING -> PASSED/FAILED/BLOCKED), a run
        snapshot is captured for reproducibility, every event lands in the
        persisted timeline, and every gate stores an immutable evidence artifact.
        Runs remain immutable (append-only); a new run_id starts a new run.

        NEVER promotes to ACTIVE automatically.
        """
        sid = candidate.strategy_id
        version = candidate.strategy_version
        obs = self.observability
        from nexus_scalp.research.evidence import (
            EvidenceArtifact,
            EvidenceKind,
            FailureClass,
            GateStatus,
            GateType,
            build_run_snapshot,
            stable_digest,
        )

        family_ds = _select_family(dataset, candidate)
        if len(family_ds.samples) < len(dataset.samples):
            logger.info(
                "[STRATEGY_VALIDATION] event=FAMILY_SELECT_VALIDATION",
                strategy_id=sid,
                family_samples=len(family_ds.samples),
                dataset_samples=len(dataset.samples),
            )

        # One unique run per validation attempt (never overwrite prior runs).
        if run_id is None or not run_id:
            run_id = "RUN-" + stable_digest(
                {"strategy": sid, "version": version, "at": datetime.now(UTC).isoformat()}
            )[:6].upper()

        if obs is not None:
            # Capture the reproducibility snapshot FIRST (spec 9/45).
            fingerprint = ""
            try:
                snapshot = build_run_snapshot(
                    sid,
                    version,
                    candidate.model_dump(mode="json"),
                    family_ds,
                    configuration=strategy_configuration or {},
                    random_seed=random_seed,
                )
                fingerprint = obs.store_run_snapshot(run_id, snapshot)
            except Exception as e:
                logger.error("[STRATEGY_VALIDATION] snapshot failed", error=str(e))
            obs.record_event(
                sid,
                run_id,
                "RESEARCH_RUN_STARTED",
                f"validation run started for {sid}@{version}",
                payload={"dataset_id": family_ds.dataset_id, "samples": len(family_ds.samples)},
            )
        else:
            fingerprint = ""

        # ------------------------------------------------------------------
        # 1. STATIC VALIDATION (spec 14: malformed candidates fail early)
        # ------------------------------------------------------------------
        static_problems = _static_validation_problems(candidate)
        if static_problems:
            if obs is not None:
                gate = obs.create_gate(
                    sid, run_id, GateType.STATIC_VALIDATION,
                    status=GateStatus.FAILED, order_index=0,
                    dataset_version=family_ds.dataset_id,
                )
                gate = obs.finish_gate(
                    gate.gate_id, status=GateStatus.FAILED,
                    failure_reason=static_problems[0],
                    failure_class=FailureClass.RESEARCH,
                    result={"problems": static_problems},
                )
                obs.record_event(
                    sid, run_id, "STRATEGY_REJECTED", "static validation failed",
                    payload={"problems": static_problems}, gate_id=gate.gate_id,
                )
            result = self._register(
                candidate, family_ds, lifecycle=CandidateLifecycle.REJECTED
            )
            self._record_run(
                run_id=run_id, candidate=candidate, dataset=family_ds,
                summary={
                    "lifecycle": "REJECTED",
                    "primary_failure": "STATIC_VALIDATION",
                    "rejection_reason": static_problems[0],
                    "family_samples": len(family_ds.samples),
                },
                status="COMPLETED", run_outcome="REJECTED",
            )
            return result
        if obs is not None:
            gate = obs.create_gate(
                sid, run_id, GateType.STATIC_VALIDATION,
                status=GateStatus.PASSED, order_index=0,
                dataset_version=family_ds.dataset_id,
            )
            gate = obs.finish_gate(
                gate.gate_id, status=GateStatus.PASSED,
                result={"problems": []}, retryable=False,
            )
            obs.record_event(
                sid, run_id, "GATE_PASSED", "static validation PASS",
                payload={"gate": "STATIC_VALIDATION"}, gate_id=gate.gate_id,
            )
        else:
            gate = None
# ------------------------------------------------------------------
        # 2. BACKTEST
        # ------------------------------------------------------------------
        if obs is not None:
            gate = obs.create_gate(
                sid, run_id, GateType.BACKTEST,
                status=GateStatus.QUEUED, order_index=1,
                dataset_version=family_ds.dataset_id,
            )
            obs.record_event(sid, run_id, "GATE_QUEUED", "backtest queued", gate_id=gate.gate_id)
            gate = obs.start_gate(gate.gate_id)
            obs.record_event(sid, run_id, "GATE_STARTED", "backtest started", gate_id=gate.gate_id)
        bt = self.backtest.run(
            family_ds,
            strategy_id=sid,
            strategy_version=version,
            use_split=True,
        )
        bt_data = bt.model_dump(mode="json")
        if bt.total_trades == 0:
            logger.warning("[STRATEGY_VALIDATION] event=ABORTED empty dataset", strategy_id=sid)
            if obs is not None:
                obs.finish_gate(
                    gate.gate_id, status=GateStatus.FAILED,
                    failure_reason="no trades in candidate family (dataset empty)",
                    failure_class=FailureClass.DATA,
                    result=bt_data, retryable=True,
                    evidence=EvidenceArtifact.create(
                        sid, run_id, EvidenceKind.BACKTEST_RESULT, bt_data,
                        gate_id=gate.gate_id, dataset_version=family_ds.dataset_id,
                    ),
                )
                obs.record_event(
                    sid, run_id, "GATE_FAILED",
                    "backtest failed: empty dataset",
                    payload={"gate": "BACKTEST", "failure_class": "DATA"},
                    gate_id=gate.gate_id,
                )
                obs.record_event(
                    sid, run_id, "STRATEGY_BLOCKED",
                    "no research dataset available for this candidate family",
                    payload={"gate": "BACKTEST", "required": family_ds.dataset_id},
                    gate_id=gate.gate_id,
                )
            result = self._register(
                candidate, family_ds, lifecycle=CandidateLifecycle.REJECTED, backtest=bt
            )
            self._record_run(
                run_id=run_id, candidate=candidate, dataset=family_ds,
                summary={
                    "lifecycle": "REJECTED",
                    "primary_failure": "BACKTEST",
                    "reason": "no trades in candidate family",
                    "family_samples": len(family_ds.samples),
                },
                status="COMPLETED", run_outcome="REJECTED",
            )
            return result
        if obs is not None:
            gate = obs.finish_gate(
                gate.gate_id, status=GateStatus.PASSED,
                result=bt_data, retryable=False,
                evidence=EvidenceArtifact.create(
                    sid, run_id, EvidenceKind.BACKTEST_RESULT, bt_data,
                    gate_id=gate.gate_id, dataset_version=family_ds.dataset_id,
                ),
            )
            obs.record_event(
                sid, run_id, "GATE_PASSED", "backtest PASS",
                payload={"gate": "BACKTEST", "trades": bt.total_trades},
                gate_id=gate.gate_id,
            )

        # ------------------------------------------------------------------
        # 3. WALK-FORWARD
        # ------------------------------------------------------------------
        if obs is not None:
            gate = obs.create_gate(
                sid, run_id, GateType.WALK_FORWARD,
                status=GateStatus.QUEUED, order_index=2,
                dataset_version=family_ds.dataset_id,
            )
            obs.record_event(
                sid, run_id, "GATE_QUEUED", "walk-forward queued", gate_id=gate.gate_id
            )
            gate = obs.start_gate(gate.gate_id)
            obs.record_event(
                sid, run_id, "GATE_STARTED", "walk-forward started", gate_id=gate.gate_id
            )
        wf = self.walkforward.validate(
            family_ds,
            strategy_id=sid,
            strategy_version=version,
            n_splits=n_folds,
            purge_seconds=purge_seconds,
            embargo_seconds=embargo_seconds,
        )
        wf_data = wf.model_dump(mode="json")
        if obs is not None:
            wf_status = GateStatus.PASSED if wf.passed else GateStatus.FAILED
            wf_reason = ""
            if not wf.passed:
                wf_reason = f"walk-forward did not pass (degradation {wf.degradation:.3f})"
            gate = obs.finish_gate(
                gate.gate_id, status=wf_status,
                failure_reason=wf_reason,
                failure_class=(
                    FailureClass.RESEARCH if not wf.passed else FailureClass.UNKNOWN
                ),
                result=wf_data, retryable=False,
                evidence=EvidenceArtifact.create(
                    sid, run_id, EvidenceKind.WALK_FORWARD_RESULT, wf_data,
                    gate_id=gate.gate_id, dataset_version=family_ds.dataset_id,
                ),
            )
            obs.record_event(
                sid, run_id,
                "GATE_PASSED" if wf.passed else "GATE_FAILED",
                "walk-forward %s" % ("PASS" if wf.passed else "FAIL"),
                payload={"gate": "WALK_FORWARD", "degradation": round(wf.degradation, 4)},
                gate_id=gate.gate_id,
            )
# ------------------------------------------------------------------
        # 4. OOS (hard gate, contamination-protected)
        # ------------------------------------------------------------------
        if obs is not None:
            gate = obs.create_gate(
                sid, run_id, GateType.OOS,
                status=GateStatus.QUEUED, order_index=3,
                dataset_version=family_ds.dataset_id,
            )
            obs.record_event(sid, run_id, "GATE_QUEUED", "OOS queued", gate_id=gate.gate_id)
            gate = obs.start_gate(gate.gate_id)
            obs.record_event(sid, run_id, "GATE_STARTED", "OOS started", gate_id=gate.gate_id)
        oos = self.oos_gate.evaluate(
            family_ds,
            strategy_id=sid,
            strategy_version=version,
            purge_seconds=purge_seconds,
            embargo_seconds=embargo_seconds,
        )
        oos_data = oos.model_dump(mode="json")
        if obs is not None:
            oos_status = GateStatus.PASSED if oos.status == "PASS" else GateStatus.FAILED
            gate = obs.finish_gate(
                gate.gate_id, status=oos_status,
                failure_reason=oos.reason,
                failure_class=(
                    FailureClass.RESEARCH if oos_status == GateStatus.FAILED
                    else FailureClass.UNKNOWN
                ),
                result=oos_data, retryable=False,
                evidence=EvidenceArtifact.create(
                    sid, run_id, EvidenceKind.OOS_RESULT, oos_data,
                    gate_id=gate.gate_id, dataset_version=family_ds.dataset_id,
                ),
            )
            obs.record_event(
                sid, run_id,
                "GATE_PASSED" if oos_status == GateStatus.PASSED else "GATE_FAILED",
                f"OOS {oos.status}",
                payload={"gate": "OOS", "oos_expectancy_r": oos.oos_expectancy_r},
                gate_id=gate.gate_id,
            )

        # ------------------------------------------------------------------
        # 5. ROBUSTNESS
        # ------------------------------------------------------------------
        if obs is not None:
            gate = obs.create_gate(
                sid, run_id, GateType.ROBUSTNESS,
                status=GateStatus.QUEUED, order_index=4,
                dataset_version=family_ds.dataset_id,
            )
            obs.record_event(
                sid, run_id, "GATE_QUEUED", "robustness queued", gate_id=gate.gate_id
            )
            gate = obs.start_gate(gate.gate_id)
            obs.record_event(
                sid, run_id, "GATE_STARTED", "robustness started", gate_id=gate.gate_id
            )
        rob = self.robustness.evaluate(family_ds, strategy_id=sid, strategy_version=version)
        rob_data = rob.model_dump(mode="json")
        if obs is not None:
            rob_status = GateStatus.PASSED if rob.status == "PASS" else GateStatus.FAILED
            gate = obs.finish_gate(
                gate.gate_id, status=rob_status,
                failure_reason=rob.reason,
                failure_class=(
                    FailureClass.RESEARCH if rob_status == GateStatus.FAILED
                    else FailureClass.UNKNOWN
                ),
                result=rob_data, retryable=False,
                evidence=EvidenceArtifact.create(
                    sid, run_id, EvidenceKind.ROBUSTNESS_RESULT, rob_data,
                    gate_id=gate.gate_id, dataset_version=family_ds.dataset_id,
                ),
            )
            obs.record_event(
                sid, run_id,
                "GATE_PASSED" if rob_status == GateStatus.PASSED else "GATE_FAILED",
                f"robustness {rob.status}",
                payload={"gate": "ROBUSTNESS"},
                gate_id=gate.gate_id,
            )

        # ------------------------------------------------------------------
        # 6. SCORE + VERDICT
        # ------------------------------------------------------------------
        score = compute_strategy_score(
            family_ds, backtest=bt, walkforward=wf, oos=oos, robustness=rob
        )
        score_data = score.model_dump(mode="json")
        if score.verdict == "VALIDATED":
            final_lifecycle = CandidateLifecycle.VALIDATED
        elif score.verdict == "REJECTED":
            final_lifecycle = CandidateLifecycle.REJECTED
        else:
            final_lifecycle = CandidateLifecycle.DISCOVERED
        if obs is not None:
            gate = obs.create_gate(
                sid, run_id, GateType.SCORING,
                status=GateStatus.QUEUED, order_index=5,
                dataset_version=family_ds.dataset_id,
            )
            obs.record_event(sid, run_id, "GATE_QUEUED", "scoring queued", gate_id=gate.gate_id)
            gate = obs.start_gate(gate.gate_id)
            obs.record_event(sid, run_id, "GATE_STARTED", "scoring started", gate_id=gate.gate_id)
            gate = obs.finish_gate(
                gate.gate_id, status=GateStatus.PASSED,
                result=score_data, retryable=False,
                evidence=EvidenceArtifact.create(
                    sid, run_id, EvidenceKind.SCORE_RESULT, score_data,
                    gate_id=gate.gate_id, dataset_version=family_ds.dataset_id,
                ),
            )
            obs.record_event(
                sid, run_id, "GATE_PASSED",
                f"scoring complete (final {score.final_score:.3f})",
                payload={"gate": "SCORING", "final_score": score.final_score,
                         "verdict": score.verdict},
                gate_id=gate.gate_id,
            )
            obs.record_event(
                sid, run_id,
                "STRATEGY_PROMOTED" if final_lifecycle == CandidateLifecycle.VALIDATED
                else "STRATEGY_REJECTED",
                f"candidate {final_lifecycle.value}",
                payload={"lifecycle": final_lifecycle.value, "score": score.final_score},
                gate_id=gate.gate_id,
            )

        result = self._register(
            candidate,
            family_ds,
            lifecycle=final_lifecycle,
            backtest=bt,
            walkforward=wf,
            oos=oos,
            robustness=rob,
            score=score,
        )
        if final_lifecycle == CandidateLifecycle.VALIDATED:
            run_outcome = "VALIDATED"
        elif final_lifecycle == CandidateLifecycle.REJECTED:
            run_outcome = "REJECTED"
        else:
            run_outcome = "INCONCLUSIVE"
        if obs is not None:
            gate_ids = [g.gate_id for g in obs.list_gates(strategy_id=sid, research_run_id=run_id)]
        else:
            gate_ids = []
        self._record_run(
            run_id=run_id,
            candidate=candidate,
            dataset=family_ds,
            summary={
                "lifecycle": final_lifecycle.value,
                "expectancy_r": bt.expectancy_r,
                "oos_expectancy_r": oos.oos_expectancy_r,
                "oos_status": oos.status,
                "robustness_status": rob.status,
                "score": score.final_score if score else 0.0,
                "verdict": score.verdict if score else "INCONCLUSIVE",
                "family_samples": len(family_ds.samples),
                "primary_failure": (
                    "OOS"
                    if oos.status != "PASS"
                    else (
                        "ROBUSTNESS"
                        if rob.status != "PASS"
                        else (
                            "WALK_FORWARD"
                            if (wf is not None and not wf.passed)
                            else ""
                        )
                    )
                ),
                "rejection_reason": oos.reason or rob.reason or "",
            },
            status="COMPLETED",
            run_outcome=run_outcome,
            snapshot_id=fingerprint,
            gates=gate_ids,
        )
        if obs is not None:
            obs.record_event(
                sid, run_id, "RESEARCH_RUN_COMPLETED",
                f"run {run_id} -> {final_lifecycle.value}",
                payload={"lifecycle": final_lifecycle.value, "score": score.final_score},
            )
        logger.info(
            "[STRATEGY_VALIDATION] event=COMPLETE",
            strategy_id=sid,
            score=score.final_score,
            status=final_lifecycle.value,
        )
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _register(
        self,
        candidate: StrategyCandidate,
        dataset: ResearchDataset,
        lifecycle: CandidateLifecycle,
        backtest: Any = None,
        walkforward: Any = None,
        oos: Any = None,
        robustness: Any = None,
        score: Any = None,
    ) -> dict[str, Any]:
        from nexus_scalp.research.models import StrategyRegistryEntry

        entry = StrategyRegistryEntry(
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            feature_schema_id=candidate.feature_schema_id,
            feature_dimension=candidate.feature_dimension,
            discovery_source=candidate.discovery_method,
            discovery_window=candidate.discovery_window,
            context_definition=candidate.context_definition,
            parent_strategy_ids=candidate.parent_strategy_ids,
            lifecycle=lifecycle,
            backtest=backtest,
            walkforward=walkforward,
            oos=oos,
            robustness=robustness,
            score=score,
            confidence=score.sample_confidence if score else 0.0,
            sample_count=backtest.total_trades if backtest else 0,
            validation_lineage=[f"{datetime.now(UTC).isoformat()}:{lifecycle.value}"],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.registry.upsert(entry)
        return entry.model_dump(mode="json")

    def _record_run(
        self,
        run_id: str | None,
        candidate: StrategyCandidate,
        dataset: ResearchDataset,
        summary: dict[str, Any],
        status: str = "COMPLETED",
        run_outcome: str = "INCONCLUSIVE",
        snapshot_id: str = "",
        gates: list[str] | None = None,
    ) -> None:
        import json
        import uuid

        run = ResearchRun(
            run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
            dataset_id=dataset.dataset_id,
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            config={
                "n_folds": 3,
                "purge_seconds": 0.0,
                "embargo_seconds": 0.0,
            },
            result_summary=summary,
            status=status,
            run_outcome=run_outcome,
            snapshot_id=snapshot_id,
            gates=gates or [],
            completed_at=datetime.now(UTC),
        )
        self.last_run = run
        if self.registry and self.registry.audit_repo._is_sqlite:
            try:
                self.registry.audit_repo._queue.put_nowait(
                    (
                        _INSERT_RUN_SQL,
                        (
                            run.run_id,
                            run.dataset_id,
                            run.strategy_id,
                            run.strategy_version,
                            run.executed_at.isoformat(),
                            json.dumps(run.config),
                            run.build_identity,
                            json.dumps(run.result_summary, default=str),
                            run.status,
                            run.run_outcome,
                            run.snapshot_id,
                            json.dumps(run.gates),
                            run.completed_at.isoformat() if run.completed_at else "",
                        ),
                    )
                )
            except Exception as e:
                logger.error("[STRATEGY_RESEARCH] run record failed", error=str(e))


def _static_validation_problems(candidate: StrategyCandidate) -> list[str]:
    """Quality gate before expensive validation (spec 14).

    Returns a list of problems when the candidate is malformed and should
    fail BEFORE the backtest; empty list means the candidate passes.
    """
    problems: list[str] = []
    ctx = candidate.context_definition or {}
    if not ctx.get("symbol"):
        problems.append("context_definition missing symbol")
    if not ctx.get("fingerprint"):
        problems.append("context_definition missing fingerprint")
    if not candidate.entry_logic:
        problems.append("empty entry_logic")
    if not candidate.exit_logic:
        problems.append("empty exit_logic")
    if candidate.feature_dimension < 1:
        problems.append(f"invalid feature_dimension {candidate.feature_dimension}")
    return problems

_INSERT_RUN_SQL = """
    INSERT INTO research_runs
    (run_id, dataset_id, strategy_id, strategy_version, executed_at, config,
     build_identity, result_summary, status, run_outcome, snapshot_id, gates,
     completed_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(run_id) DO NOTHING;
"""
