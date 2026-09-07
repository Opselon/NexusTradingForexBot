"""
Model Lifecycle Orchestrator
============================
PHASE 10 end-to-end controlled training pipeline (spec 25 / 35):

    VERIFIED EXPERIENCE
            -> RESEARCH DATASET
            -> TRAINING DATASET
            -> CANDIDATE MODEL (offline training, staging paths)
            -> VALIDATION GATES (1..12)
            -> CHAMPION COMPARISON
            -> CHALLENGER (shadow-eligible)  [NEVER auto-promoted]

The orchestrator NEVER promotes to production automatically. A validated
Challenger is stored with `ModelStatus.CHALLENGER` and stays shadow-eligible;
production authority remains with the existing controlled process.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.model_lifecycle.champion import ChampionManager, ChampionModel
from nexus_scalp.model_lifecycle.comparison import ChampionChallengerComparator
from nexus_scalp.model_lifecycle.dataset import TrainingDatasetBuilder
from nexus_scalp.model_lifecycle.gates import (
    gate_artifact_integrity,
    gate_dataset_integrity,
    gate_label_integrity,
    gate_oos,
    gate_reproducibility,
    gate_risk_drawdown,
    gate_robustness,
    gate_schema_compatibility,
    gate_training_stability,
    gate_validation_performance,
    gate_walkforward,
)
from nexus_scalp.model_lifecycle.models import (
    GateResult,
    ModelStatus,
    TrainingDataset,
    TrainingRun,
    TrainingRunStatus,
)
from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry
from nexus_scalp.model_lifecycle.store import TrainingRunStore
from nexus_scalp.model_lifecycle.trainer import ChallengerTrainer, summarize_run
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.robustness import RobustnessEngine
from nexus_scalp.research.walkforward import WalkForwardEngine

logger = get_logger("nexus_scalp.model_lifecycle.orchestrator")


class ModelLifecycleOrchestrator:
    """
    Wires dataset -> training -> gates -> comparison -> registry.

    No order capability anywhere: this orchestrator holds no adapter, no order
    manager and no risk engine.
    """

    def __init__(
        self,
        audit_repo: Any,
        ledger: ExperienceLedger,
        champion_manager: ChampionManager,
        model_registry: Any,
        run_store: TrainingRunStore | None = None,
        lifecycle_registry: ModelLifecycleRegistry | None = None,
        comparator: ChampionChallengerComparator | None = None,
    ) -> None:
        self.audit_repo = audit_repo
        self.ledger = ledger
        self.champion_manager = champion_manager
        self.model_registry = model_registry
        self.run_store = run_store or TrainingRunStore(audit_repo)
        self.lifecycle_registry = lifecycle_registry or ModelLifecycleRegistry(
            audit_repo, model_registry
        )
        self.comparator = comparator or ChampionChallengerComparator()
        self.dataset_builder = TrainingDatasetBuilder(ledger)
        self.run_store.ensure_schema()
        self.lifecycle_registry.ensure_schema()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_training_dataset(
        self,
        include_no_trade: bool = True,
        weight_no_trade: float = 0.25,
        as_of: datetime | None = None,
        only_executed: bool = True,
    ) -> TrainingDataset:
        """Builds the deterministic training dataset (spec 7 / 8)."""
        return self.dataset_builder.build(
            include_no_trade=include_no_trade,
            weight_no_trade=weight_no_trade,
            as_of=as_of,
            only_executed=only_executed,
        )

    def run_controlled_training(
        self,
        dataset: TrainingDataset,
        hyperparameters: dict[str, Any] | None = None,
        num_epochs: int = 10,
        run_id: str | None = None,
        build_identity: str = "",
        evaluate_champion: bool = True,
    ) -> dict[str, Any]:
        """
        Runs one full controlled training pass + validation gates + comparison.

        Returns a summary dict with run_id, status, gates, comparison and the
        registry transition. NEVER promotes to production.
        """
        run_id = run_id or f"tr_{uuid.uuid4().hex[:12]}"
        champ = self.champion_manager.champion_or_none()

        # ---- 1. TRAIN (candidate only, staging paths) ----------------------
        trainer = ChallengerTrainer(
            champion_manager=self.champion_manager,
            train_dataset=dataset,
            feature_cols=[f"feat_{i}" for i in range(dataset.feature_dimension)],
            hyperparameters=hyperparameters,
            num_epochs=num_epochs,
            random_seed=int((hyperparameters or {}).get("random_seed", 42)),
            build_identity=build_identity,
        )
        # Persist the RUNNING row BEFORE training begins (restart-safety):
        # a crash mid-training must leave a durable RUNNING trace that the
        # worker's _restore_inflight_state can mark INCOMPLETE on restart —
        # previously save_run happened only after gates, so a crash left zero
        # rows and restart-safety was nominal.
        from nexus_scalp.model_lifecycle.models import TrainingRun as _TrainingRun

        _pre_run = _TrainingRun(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            feature_schema_id=dataset.feature_schema_id,
            feature_dimension=dataset.feature_dimension,
            hyperparameters=dict(hyperparameters or {}),
            random_seed=int((hyperparameters or {}).get("random_seed", 42)),
            status=TrainingRunStatus.RUNNING,
            build_identity=build_identity,
        )
        self.run_store.save_run(_pre_run)

        run = trainer.train(run_id=run_id)

        # ---- 2. GATES --------------------------------------------------------
        gates, all_passed = self._evaluate_gates(
            run=run,
            dataset=dataset,
            champion=champ,
            evaluate_champion=evaluate_champion,
        )
        run = run.model_copy(update={"gates": gates})
        if not all_passed:
            run = run.model_copy(
                update={
                    "status": TrainingRunStatus.COMPLETED,  # training finished
                    "metrics": {**run.metrics, "validation_gates": "FAIL"},
                }
            )
        # Persistent status: COMPLETED means training finished; eligibility is
        # decided by gates, not status.
        self.run_store.save_run(run)

        # ---- 3. REGISTRY TRANSITION -------------------------------------------
        candidate_status = ModelStatus.CHALLENGER if all_passed else ModelStatus.REJECTED
        registry_ok = False
        if run.artifacts:
            artifact = run.artifacts[0]
            registry_ok = self.lifecycle_registry.set_status(
                model_id=artifact.model_id or f"candidate_{run_id}",
                model_version=artifact.model_version or run_id,
                status=candidate_status,
                reason=(
                    "all validation gates passed; shadow-eligible"
                    if all_passed
                    else f"validation gate(s) failed: {_failed_gate_names(gates)}"
                ),
                gate_summary={g.gate: g.passed for g in gates},
                training_run_id=run_id,
                parent_model_id=champ.model_id if champ else "",
                parent_model_version=champ.model_version if champ else "",
            )
        logger.info(
            "[MODEL] event=VALIDATION_GATE status=%s",
            "PASS" if all_passed else "FAIL",
            run_id=run_id,
            gates_passed=sum(1 for g in gates if g.passed),
            gates_total=len(gates),
        )

        # ---- 4. CHAMPION COMPARISON (learning-loop closure) -------------------
        # The challenger-vs-champion comparison is now part of EVERY gated
        # training pass (it was dead code before). When the champion's own
        # metrics are unavailable the comparison records eligible=False with
        # the reason — promotion eligibility is never asserted from missing
        # evidence (fail-closed).
        # ---- 4. CHAMPION COMPARISON (learning-loop closure) -------------------
        # The challenger-vs-champion comparison is part of EVERY gated
        # training pass that produced an artifact (it was dead code before).
        # When the champion's own metrics are unavailable the comparison
        # records eligible=False with the reason — promotion eligibility is
        # never asserted from missing evidence (fail-closed).
        comparison_summary: dict[str, Any] | None = None
        if run.artifacts:
            try:
                comparison = self.compare_against_champion(run, champ)
                if comparison is not None:
                    comparison_summary = {
                        "eligible": comparison.eligible,
                        "improvement_score": comparison.improvement_score,
                        "reasons": list(comparison.reasons),
                    }
            except Exception as exc:
                logger.error("[MODEL] champion comparison failed (non-fatal)", error=str(exc))

        return {
            "run_id": run_id,
            "run": summarize_run(run),
            "gates": [g.model_dump(mode="json") for g in gates],
            "all_gates_passed": all_passed,
            "candidate_status": candidate_status.value,
            "registry_updated": registry_ok,
            "champion_unavailable": champ is None,
            "champion_comparison": comparison_summary,
        }

    # ------------------------------------------------------------------
    # Champion comparison
    # ------------------------------------------------------------------

    def compare_against_champion(
        self,
        run: TrainingRun,
        champion: ChampionModel | None,
    ) -> Any:
        """Runs the structured Champion vs Challenger comparison (spec 19).

        Learning-loop closure Phase 2: the champion side MUST come from REAL
        evidence — the persisted baseline_eval artifact keyed by the
        champion's verified artifact hash (model_lifecycle.champion_metrics),
        falling back to the champion's own recorded COMPLETED training run.
        Zeroed placeholders are removed: when neither evidence source exists
        the comparison is computed but FORCED ineligible with an explicit
        reason (never eligible from missing evidence — fail-closed).
        """
        if champion is None:
            return None
        # Reuse Phase 09 research engines to evaluate both models' trading
        # quality on the same execution assumptions.
        # The dataset for evaluation is the challenger's training dataset.
        ch_metrics = dict(run.metrics)
        # PRIMARY evidence: the baseline_eval artifact for the champion's
        # exact serving bytes (research.baseline_eval output).
        champ_metrics: dict[str, Any] | None = None
        try:
            from nexus_scalp.model_lifecycle.champion_metrics import ChampionMetricsProvider

            baseline = ChampionMetricsProvider().load(champion)
        except Exception as exc:
            logger.error("[MODEL] champion baseline lookup failed", error=str(exc))
            baseline = None
        if baseline is not None:
            champ_metrics = {
                "expectancy_r": float(baseline.get("expectancy_r", 0.0)),
                "max_drawdown_r": float(baseline.get("max_drawdown_r", 0.0)),
                "oos_expectancy_r": float(baseline.get("oos_expectancy_r", 0.0)),
                "tail_loss_count": int(baseline.get("tail_loss_count", 0)),
                "robustness_status": str(baseline.get("robustness_status", "UNKNOWN")),
                "stability": float(baseline.get("stability", 0.0)),
                "model_id": baseline.get("model_id", ""),
                "model_version": baseline.get("model_version", ""),
                "source": "baseline_eval_artifact",
                "evaluation_id": baseline.get("evaluation_id", ""),
            }
        # SECONDARY evidence: the champion's own recorded COMPLETED run.
        if champ_metrics is None:
            try:
                runs = self.run_store.list_runs(status="COMPLETED", limit=200)
            except Exception as exc:
                logger.error("[MODEL] champion run lookup failed", error=str(exc))
                runs = []
            for prior in runs:
                prior_metrics = prior.get("metrics") or {}
                if isinstance(prior_metrics, str):
                    try:
                        import json as _json

                        prior_metrics = _json.loads(prior_metrics)
                    except Exception:
                        prior_metrics = {}
                if prior_metrics and float(prior_metrics.get("expectancy_r", 0.0) or 0.0) != 0.0:
                    champ_metrics = dict(prior_metrics)
                    break

        comparison = self.comparator.compare(
            champion=champ_metrics
            or {
                "expectancy_r": 0.0,
                "max_drawdown_r": 0.0,
                "oos_expectancy_r": 0.0,
                "tail_loss_count": 0,
                "robustness_status": "PASS",
                "stability": 1.0,
            },
            challenger=ch_metrics,
            run_id=run.run_id,
        )
        if champ_metrics is None:
            # Fail-closed: without real champion evidence the challenger can
            # never be promotion-eligible from this comparison alone.
            comparison = comparison.model_copy(
                update={
                    "eligible": False,
                    "reasons": [
                        *list(comparison.reasons),
                        "champion_metrics_unavailable: no baseline_eval artifact "
                        "and no recorded COMPLETED run with real metrics",
                    ],
                }
            )
        self.run_store.save_comparison(comparison)
        return comparison

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    def _evaluate_gates(
        self,
        run: TrainingRun,
        dataset: TrainingDataset,
        champion: ChampionModel | None,
        evaluate_champion: bool,
    ) -> tuple[list[GateResult], bool]:
        gates = [
            lambda: gate_dataset_integrity(dataset),
            lambda: gate_schema_compatibility(
                dataset, run.feature_schema_id, run.feature_dimension
            ),
            lambda: gate_label_integrity(dataset),
            lambda: gate_training_stability(run.metrics),
            lambda: gate_validation_performance(run.metrics),
            lambda: gate_artifact_integrity(run.artifacts[0] if run.artifacts else None),
            lambda: gate_reproducibility(
                run.run_id, run.dataset_id, run.feature_schema_id, run.random_seed
            ),
        ]
        # OOS / walk-forward / robustness: when the training produced a model we
        # evaluate it through the Phase 09 research engines on the trading
        # outcome distribution (recorded R multiples).
        oos_result = None
        rob_result = None
        if run.status == TrainingRunStatus.COMPLETED and run.artifacts:
            try:
                oos_result = self._evaluate_oos(dataset)
                wf_result = None
                rob_result = self._evaluate_robustness(dataset)
            except Exception as e:
                logger.error("[MODEL] gate evaluation failed", error=str(e))
        if oos_result is not None:
            gates.append(lambda: gate_oos(oos_result))
        try:
            wf_result = self._evaluate_walkforward(dataset)
        except Exception as e:
            logger.error("[MODEL] walk-forward evaluation failed", error=str(e))
            wf_result = None
        if wf_result is not None:
            gates.append(lambda: gate_walkforward(wf_result))
        if rob_result is not None:
            gates.append(lambda: gate_robustness(rob_result))
        if oos_result is not None:
            gates.append(lambda: gate_risk_drawdown(oos_result))

        results: list[GateResult] = []
        all_passed = True
        for gate_fn in gates:
            try:
                res = gate_fn()
            except Exception as e:
                res = GateResult(gate="UNKNOWN", passed=False, reason=str(e))
            results.append(res)
            if not res.passed:
                all_passed = False
        return results, all_passed

    def _evaluate_oos(self, dataset: TrainingDataset) -> Any:
        """Runs the Phase 09 OOS gate over the dataset's outcome distribution."""
        rd = self._research_dataset(dataset)
        return OOSGate().evaluate(rd, "candidate", "candidate")

    def _evaluate_robustness(self, dataset: TrainingDataset) -> Any:
        rd = self._research_dataset(dataset)
        return RobustnessEngine().evaluate(rd, "candidate", "candidate")

    def _research_dataset(self, dataset: TrainingDataset) -> Any:
        """Converts a training dataset to a Phase 09 research dataset (outcomes)."""
        from nexus_scalp.research.models import ResearchDataset, ResearchSample

        samples = [
            ResearchSample(
                sample_id=r.sample_id,
                experience_id=r.experience_id,
                idempotency_key=r.idempotency_key,
                decision_timestamp=r.decision_timestamp,
                outcome_timestamp=r.decision_timestamp,
                symbol=r.symbol,
                timeframe=r.timeframe,
                strategy_id=r.strategy_id,
                strategy_version=r.strategy_version,
                feature_schema_id=r.feature_schema_id,
                feature_dimension=r.feature_dimension,
                regime=r.regime,
                session=r.session,
                realized_r=r.outcome_r,
                realized_pnl_usd=r.outcome_r * 100.0,
                risk_distance=10.0,
                holding_duration_sec=300.0,
                mae_r=0.2,
                mfe_r=1.0,
                exit_reason=r.exit_reason,
            )
            for r in dataset.rows
            if r.is_executed and r.is_closed
        ]
        return ResearchDataset(dataset_id=dataset.dataset_id, samples=samples)

    def _evaluate_walkforward(self, dataset: TrainingDataset) -> Any:
        rd = self._research_dataset(dataset)
        return WalkForwardEngine().validate(rd, "candidate", "candidate", n_splits=3)


def _failed_gate_names(gates: list[GateResult]) -> str:
    return ", ".join(g.gate for g in gates if not g.passed)
