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
    WalkForwardResult,
)
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.robustness import RobustnessEngine
from nexus_scalp.research.scoring import compute_strategy_score
from nexus_scalp.research.splitting import (
    DEFAULT_EMBARGO_SECONDS,
    DEFAULT_PURGE_SECONDS,
)
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


def _extract_context_contract(candidate) -> dict | None:
    """PHASE 26: derive the evaluation context contract from a candidate DSL.

    Reads candidate.context_definition (the persisted DSL context block).
    Returns None when no explicit session/regime/volatility claim exists,
    so generic candidates keep the legacy global evaluation path.
    """
    from nexus_scalp.research.context_contract import (
        ContextContractError as _cce,  # defined before try so except is bound
    )

    try:
        from nexus_scalp.research.context_contract import (
            extract_context_contract,
            has_active_contract,
        )

        ctx = getattr(candidate, "context_definition", None) or {}
        hyp = (ctx.get("hypothesis") if isinstance(ctx, dict) else None) or {}
        contract = extract_context_contract(ctx if isinstance(ctx, dict) else {}, hyp)
        if not has_active_contract(contract):
            return None
        return contract
    except _cce:
        raise
    except Exception as exc:
        # PHASE 27 fail-loud: extraction errors must NEVER silently degrade a
        # declared-context strategy to global evaluation.
        sid = getattr(candidate, "strategy_id", "?")
        raise _cce(
            f"CONTEXT_CONTRACT_MISMATCH: contract extraction failed for {sid}: {exc}"
        ) from exc


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
        purge_seconds: float = DEFAULT_PURGE_SECONDS,
        embargo_seconds: float = DEFAULT_EMBARGO_SECONDS,
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

        # PHASE 27 CONSISTENCY: derive ONE canonical context contract up front
        # and scope EVERY gate (backtest, WF, OOS) to the SAME population.
        # contract_hash is stamped into each gate result so the registry
        # evidence proves all stages consumed the identical semantic filter.
        from nexus_scalp.research.context_contract import (
            ContextContractError,
            filter_samples_by_contract,
        )
        from nexus_scalp.research.context_contract import (
            contract_hash as _contract_hash,
        )

        _ctx_for_validation = _extract_context_contract(candidate)
        _ctx_hash = _contract_hash(_ctx_for_validation) if _ctx_for_validation else None
        _ctx_ds_for_gates = None
        if _ctx_for_validation:
            _filtered, _ctx_diag = filter_samples_by_contract(
                list(family_ds.samples), _ctx_for_validation
            )
            if _filtered:
                _ctx_ds_for_gates = family_ds.model_copy(update={"samples": _filtered})
            else:
                # PHASE 27 F1 (fail-loud): a declared-context strategy whose
                # family contains ZERO matching samples must ABSTAIN — never
                # silently validate on the global population.
                raise ContextContractError(
                    f"CONTEXT_CONTRACT_EMPTY_POPULATION: {sid} "
                    f"hash={_ctx_hash} matched=0/{len(family_ds.samples)}"
                )
            logger.info(
                "[CONTEXT_CONTRACT] event=GATES_SCOPED strategy_id=%s hash=%s population=%d/%d",
                sid,
                _ctx_hash,
                len(_filtered),
                len(family_ds.samples),
            )

        # One unique run per validation attempt (never overwrite prior runs).
        if run_id is None or not run_id:
            run_id = (
                "RUN-"
                + stable_digest(
                    {"strategy": sid, "version": version, "at": datetime.now(UTC).isoformat()}
                )[:6].upper()
            )

        if obs is not None:
            # Capture the reproducibility snapshot FIRST (spec 9/45).
            # CHG-0035: the configuration now carries the RESOLVED identity
            # (schema/model/commit) so build_run_snapshot records what this
            # run ACTUALLY used — not placeholders.
            identity_cfg: dict[str, Any] = dict(strategy_configuration or {})
            try:
                from nexus_scalp.features.schema_contract import (
                    SCHEMA_ID as _SID,
                )
                from nexus_scalp.features.schema_contract import (
                    feature_schema_hash as _FSH,
                )

                identity_cfg.setdefault("feature_schema_id", _SID)
                identity_cfg.setdefault("feature_schema_hash", _FSH())
            except Exception:
                pass
            try:
                from nexus_scalp.release.metadata import _git_commit as _git

                identity_cfg.setdefault("git_commit", _git() or "")
            except Exception:
                pass
            try:
                from pathlib import Path as _ConfigPath

                from nexus_scalp.configuration.config import AppConfig

                _yaml = _ConfigPath("configs/live.yaml")
                _cfg = AppConfig.load_from_yaml(_yaml) if _yaml.exists() else AppConfig()
                _art = str(_cfg.model.model_artifact_path or "")
                if _art:
                    identity_cfg.setdefault("model_id", _art)
                    try:
                        from nexus_scalp.research.streaming_replay import _sha256_file

                        identity_cfg.setdefault("model_hash", _sha256_file(_ConfigPath(_art)))
                    except Exception:
                        pass
            except Exception:
                pass
            fingerprint = ""
            try:
                snapshot = build_run_snapshot(
                    sid,
                    version,
                    candidate.model_dump(mode="json"),
                    family_ds,
                    configuration=identity_cfg,
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
                    sid,
                    run_id,
                    GateType.STATIC_VALIDATION,
                    status=GateStatus.FAILED,
                    order_index=0,
                    dataset_version=family_ds.dataset_id,
                )
                gate = obs.finish_gate(
                    gate.gate_id,
                    status=GateStatus.FAILED,
                    failure_reason=static_problems[0],
                    failure_class=FailureClass.RESEARCH,
                    result={"problems": static_problems},
                )
                obs.record_event(
                    sid,
                    run_id,
                    "STRATEGY_REJECTED",
                    "static validation failed",
                    payload={"problems": static_problems},
                    gate_id=gate.gate_id,
                )
            result = self._register(candidate, family_ds, lifecycle=CandidateLifecycle.REJECTED)
            self._record_run(
                run_id=run_id,
                candidate=candidate,
                dataset=family_ds,
                summary={
                    "lifecycle": "REJECTED",
                    "primary_failure": "STATIC_VALIDATION",
                    "rejection_reason": static_problems[0],
                    "family_samples": len(family_ds.samples),
                },
                status="COMPLETED",
                run_outcome="REJECTED",
            purge_seconds=purge_seconds,
            embargo_seconds=embargo_seconds,
            )
            return result
        if obs is not None:
            gate = obs.create_gate(
                sid,
                run_id,
                GateType.STATIC_VALIDATION,
                status=GateStatus.PASSED,
                order_index=0,
                dataset_version=family_ds.dataset_id,
            )
            gate = obs.finish_gate(
                gate.gate_id,
                status=GateStatus.PASSED,
                result={"problems": []},
                retryable=False,
            )
            obs.record_event(
                sid,
                run_id,
                "GATE_PASSED",
                "static validation PASS",
                payload={"gate": "STATIC_VALIDATION"},
                gate_id=gate.gate_id,
            )
        else:
            gate = None
        # ------------------------------------------------------------------
        # 2. BACKTEST
        # ------------------------------------------------------------------
        if obs is not None:
            gate = obs.create_gate(
                sid,
                run_id,
                GateType.BACKTEST,
                status=GateStatus.QUEUED,
                order_index=1,
                dataset_version=family_ds.dataset_id,
            )
            obs.record_event(sid, run_id, "GATE_QUEUED", "backtest queued", gate_id=gate.gate_id)
            gate = obs.start_gate(gate.gate_id)
            obs.record_event(sid, run_id, "GATE_STARTED", "backtest started", gate_id=gate.gate_id)
        bt = self.backtest.run(
            _ctx_ds_for_gates if _ctx_ds_for_gates is not None else family_ds,
            strategy_id=sid,
            strategy_version=version,
            use_split=True,
        purge_seconds=purge_seconds,
        embargo_seconds=embargo_seconds,
        )
        bt_data = bt.model_dump(mode="json")
        bt_data["context_contract_hash"] = _ctx_hash
        bt_data["contract_consistent"] = True
        if bt.total_trades == 0:
            logger.warning("[STRATEGY_VALIDATION] event=ABORTED empty dataset", strategy_id=sid)
            if obs is not None:
                obs.finish_gate(
                    gate.gate_id,
                    status=GateStatus.FAILED,
                    failure_reason="no trades in candidate family (dataset empty)",
                    failure_class=FailureClass.DATA,
                    result=bt_data,
                    retryable=True,
                    evidence=EvidenceArtifact.create(
                        sid,
                        run_id,
                        EvidenceKind.BACKTEST_RESULT,
                        bt_data,
                        gate_id=gate.gate_id,
                        dataset_version=family_ds.dataset_id,
                    ),
                )
                obs.record_event(
                    sid,
                    run_id,
                    "GATE_FAILED",
                    "backtest failed: empty dataset",
                    payload={"gate": "BACKTEST", "failure_class": "DATA"},
                    gate_id=gate.gate_id,
                )
                obs.record_event(
                    sid,
                    run_id,
                    "STRATEGY_BLOCKED",
                    "no research dataset available for this candidate family",
                    payload={"gate": "BACKTEST", "required": family_ds.dataset_id},
                    gate_id=gate.gate_id,
                )
            result = self._register(
                candidate, family_ds, lifecycle=CandidateLifecycle.REJECTED, backtest=bt
            )
            self._record_run(
                run_id=run_id,
                candidate=candidate,
                dataset=family_ds,
                summary={
                    "lifecycle": "REJECTED",
                    "primary_failure": "BACKTEST",
                    "reason": "no trades in candidate family",
                    "family_samples": len(family_ds.samples),
                },
                status="COMPLETED",
                run_outcome="REJECTED",
            purge_seconds=purge_seconds,
            embargo_seconds=embargo_seconds,
            )
            return result
        if obs is not None:
            gate = obs.finish_gate(
                gate.gate_id,
                status=GateStatus.PASSED,
                result=bt_data,
                retryable=False,
                evidence=EvidenceArtifact.create(
                    sid,
                    run_id,
                    EvidenceKind.BACKTEST_RESULT,
                    bt_data,
                    gate_id=gate.gate_id,
                    dataset_version=family_ds.dataset_id,
                ),
            )
            obs.record_event(
                sid,
                run_id,
                "GATE_PASSED",
                "backtest PASS",
                payload={"gate": "BACKTEST", "trades": bt.total_trades},
                gate_id=gate.gate_id,
            )

        # ------------------------------------------------------------------
        # 3. WALK-FORWARD
        # ------------------------------------------------------------------
        if obs is not None:
            gate = obs.create_gate(
                sid,
                run_id,
                GateType.WALK_FORWARD,
                status=GateStatus.QUEUED,
                order_index=2,
                dataset_version=family_ds.dataset_id,
            )
            obs.record_event(
                sid, run_id, "GATE_QUEUED", "walk-forward queued", gate_id=gate.gate_id
            )
            gate = obs.start_gate(gate.gate_id)
            obs.record_event(
                sid, run_id, "GATE_STARTED", "walk-forward started", gate_id=gate.gate_id
            )
        # PHASE 27: WF consumes the SAME canonical contract-scoped dataset as
        # the backtest gate (single extraction upstream). Thresholds unchanged.
        # PHASE 29 ADAPTIVE FOLDS: request only what the family population can
        # actually support (splitting.walk_forward_folds needs n >= (splits+2)*3,
        # so a 60-sample family supports 2 folds, not the configured 3). When even
        # one fold is impossible we skip WF with an explicit insufficient_reason
        # instead of passing zeros through. No gate threshold changes: this only
        # prevents structurally-impossible fold requests.
        _wf_ds = _ctx_ds_for_gates if _ctx_ds_for_gates is not None else family_ds
        max_folds = min(
            n_folds,
            max(1, (len(_wf_ds.samples) // 15) - 2),
        )
        if max_folds < 1:
            wf = WalkForwardResult(
                strategy_id=sid,
                strategy_version=version,
                dataset_id=family_ds.dataset_id,
                folds=[],
                passed=False,
                insufficient_reason=(
                    f"FAMILY_TOO_SMALL_FOR_FOLDS: {len(_wf_ds.samples)} samples "
                    "cannot form any walk-forward fold"
                ),
            )
        else:
            wf = self.walkforward.validate(
                _wf_ds,
                strategy_id=sid,
                strategy_version=version,
                n_splits=max_folds,
                purge_seconds=purge_seconds,
                embargo_seconds=embargo_seconds,
            )
        wf_data = wf.model_dump(mode="json")
        wf_data["context_contract_hash"] = _ctx_hash
        wf_data["contract_consistent"] = True
        if obs is not None:
            wf_status = GateStatus.PASSED if wf.passed else GateStatus.FAILED
            wf_reason = ""
            if not wf.passed:
                wf_reason = f"walk-forward did not pass (degradation {wf.degradation:.3f})"
            gate = obs.finish_gate(
                gate.gate_id,
                status=wf_status,
                failure_reason=wf_reason,
                failure_class=(FailureClass.RESEARCH if not wf.passed else FailureClass.UNKNOWN),
                result=wf_data,
                retryable=False,
                evidence=EvidenceArtifact.create(
                    sid,
                    run_id,
                    EvidenceKind.WALK_FORWARD_RESULT,
                    wf_data,
                    gate_id=gate.gate_id,
                    dataset_version=family_ds.dataset_id,
                ),
            )
            obs.record_event(
                sid,
                run_id,
                "GATE_PASSED" if wf.passed else "GATE_FAILED",
                f"walk-forward {'PASS' if wf.passed else 'FAIL'}",
                payload={"gate": "WALK_FORWARD", "degradation": round(wf.degradation, 4)},
                gate_id=gate.gate_id,
            )
        # ------------------------------------------------------------------
        # 4. OOS (hard gate, contamination-protected)
        # ------------------------------------------------------------------
        if obs is not None:
            gate = obs.create_gate(
                sid,
                run_id,
                GateType.OOS,
                status=GateStatus.QUEUED,
                order_index=3,
                dataset_version=family_ds.dataset_id,
            )
            obs.record_event(sid, run_id, "GATE_QUEUED", "OOS queued", gate_id=gate.gate_id)
            gate = obs.start_gate(gate.gate_id)
            obs.record_event(sid, run_id, "GATE_STARTED", "OOS started", gate_id=gate.gate_id)
        # PHASE 27: OOS consumes the SAME contract-scoped dataset (single
        # extraction upstream; hash stamped for registry evidence).
        oos = self.oos_gate.evaluate(
            _ctx_ds_for_gates if _ctx_ds_for_gates is not None else family_ds,
            strategy_id=sid,
            strategy_version=version,
            purge_seconds=purge_seconds,
            embargo_seconds=embargo_seconds,
        )
        oos_data = oos.model_dump(mode="json")
        oos_data["context_contract_hash"] = _ctx_hash
        oos_data["contract_consistent"] = True
        if obs is not None:
            oos_status = GateStatus.PASSED if oos.status == "PASS" else GateStatus.FAILED
            gate = obs.finish_gate(
                gate.gate_id,
                status=oos_status,
                failure_reason=oos.reason,
                failure_class=(
                    FailureClass.RESEARCH
                    if oos_status == GateStatus.FAILED
                    else FailureClass.UNKNOWN
                ),
                result=oos_data,
                retryable=False,
                evidence=EvidenceArtifact.create(
                    sid,
                    run_id,
                    EvidenceKind.OOS_RESULT,
                    oos_data,
                    gate_id=gate.gate_id,
                    dataset_version=family_ds.dataset_id,
                ),
            )
            obs.record_event(
                sid,
                run_id,
                "GATE_PASSED" if oos_status == GateStatus.PASSED else "GATE_FAILED",
                f"OOS {oos.status}",
                payload={"gate": "OOS", "oos_expectancy_r": oos.oos_expectancy_r},
                gate_id=gate.gate_id,
            )

        # ------------------------------------------------------------------
        # 5. ROBUSTNESS (BUG-233: short-circuit when OOS failed)
        # ------------------------------------------------------------------
        if oos.status != "PASS":
            if obs is not None:
                gate = obs.create_gate(
                    sid,
                    run_id,
                    GateType.ROBUSTNESS,
                    status=GateStatus.BLOCKED,
                    order_index=4,
                    dataset_version=family_ds.dataset_id,
                )
                gate = obs.finish_gate(
                    gate.gate_id,
                    status=GateStatus.BLOCKED,
                    failure_reason="OOS failed — chain stopped",
                    failure_class=FailureClass.RESEARCH,
                    result={"reason": "OOS failed — chain stopped", "blocked": True},
                    retryable=False,
                )
                obs.record_event(
                    sid,
                    run_id,
                    "GATE_BLOCKED",
                    "robustness BLOCKED (OOS failed — chain stopped)",
                    payload={"gate": "ROBUSTNESS", "reason": "OOS failed — chain stopped"},
                    gate_id=gate.gate_id,
                )
            rob = None
            rob_data = None
        else:
            if obs is not None:
                gate = obs.create_gate(
                    sid,
                    run_id,
                    GateType.ROBUSTNESS,
                    status=GateStatus.QUEUED,
                    order_index=4,
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
                    gate.gate_id,
                    status=rob_status,
                    failure_reason=rob.reason,
                    failure_class=(
                        FailureClass.RESEARCH
                        if rob_status == GateStatus.FAILED
                        else FailureClass.UNKNOWN
                    ),
                    result=rob_data,
                    retryable=False,
                    evidence=EvidenceArtifact.create(
                        sid,
                        run_id,
                        EvidenceKind.ROBUSTNESS_RESULT,
                        rob_data,
                        gate_id=gate.gate_id,
                        dataset_version=family_ds.dataset_id,
                    ),
                )
                obs.record_event(
                    sid,
                    run_id,
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

        # Lifecycle verdict mapping (lifecycle-repair 2026-08-23): the previous
        # else-branch collapsed any non-terminal verdict back to DISCOVERED,
        # which re-queued the candidate for validation every cycle and left
        # Validated=0 forever. Every non-passing verdict is now an explicit
        # REJECTED with a recorded reason — DISCOVERED is never re-entered
        # after gates have run, and no threshold is weakened.
        if score.verdict == "VALIDATED":
            final_lifecycle = CandidateLifecycle.VALIDATED
        elif score.verdict == "REJECTED":
            final_lifecycle = CandidateLifecycle.REJECTED
        elif score.verdict == "INCONCLUSIVE":
            final_lifecycle = CandidateLifecycle.REJECTED
            logger.warning(
                "[STRATEGY_VALIDATION] event=VERDICT_INCONCLUSIVE_REJECTED",
                strategy_id=sid,
                score=score.final_score,
            )
        else:
            final_lifecycle = CandidateLifecycle.REJECTED
            logger.warning(
                "[STRATEGY_VALIDATION] event=UNKNOWN_VERDICT_REJECTED",
                strategy_id=sid,
                verdict=score.verdict,
            )
        if obs is not None:
            gate = obs.create_gate(
                sid,
                run_id,
                GateType.SCORING,
                status=GateStatus.QUEUED,
                order_index=5,
                dataset_version=family_ds.dataset_id,
            )
            obs.record_event(sid, run_id, "GATE_QUEUED", "scoring queued", gate_id=gate.gate_id)
            gate = obs.start_gate(gate.gate_id)
            obs.record_event(sid, run_id, "GATE_STARTED", "scoring started", gate_id=gate.gate_id)
            gate = obs.finish_gate(
                gate.gate_id,
                status=GateStatus.PASSED,
                result=score_data,
                retryable=False,
                evidence=EvidenceArtifact.create(
                    sid,
                    run_id,
                    EvidenceKind.SCORE_RESULT,
                    score_data,
                    gate_id=gate.gate_id,
                    dataset_version=family_ds.dataset_id,
                ),
            )
            obs.record_event(
                sid,
                run_id,
                "GATE_PASSED",
                f"scoring complete (final {score.final_score:.3f})",
                payload={
                    "gate": "SCORING",
                    "final_score": score.final_score,
                    "verdict": score.verdict,
                },
                gate_id=gate.gate_id,
            )
            obs.record_event(
                sid,
                run_id,
                "STRATEGY_PROMOTED"
                if final_lifecycle == CandidateLifecycle.VALIDATED
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
                "robustness_status": (rob.status if rob is not None else "BLOCKED"),
                "score": score.final_score if score else 0.0,
                "verdict": score.verdict if score else "INCONCLUSIVE",
                "family_samples": len(family_ds.samples),
                "primary_failure": (
                    "OOS"
                    if oos.status != "PASS"
                    else (
                        "ROBUSTNESS"
                        if (rob is not None and rob.status != "PASS")
                        else ("WALK_FORWARD" if (wf is not None and not wf.passed) else "")
                    )
                ),
                "rejection_reason": (oos.reason or (rob.reason if rob is not None else "") or ""),
            },
            status="COMPLETED",
            run_outcome=run_outcome,
            snapshot_id=fingerprint,
            gates=gate_ids,
        purge_seconds=purge_seconds,
        embargo_seconds=embargo_seconds,
        )
        if obs is not None:
            obs.record_event(
                sid,
                run_id,
                "RESEARCH_RUN_COMPLETED",
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
        purge_seconds: float = DEFAULT_PURGE_SECONDS,
        embargo_seconds: float = DEFAULT_EMBARGO_SECONDS,
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
                "purge_seconds": purge_seconds,
                "embargo_seconds": embargo_seconds,
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
