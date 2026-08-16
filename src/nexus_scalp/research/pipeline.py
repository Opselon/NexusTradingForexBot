"""
Research Pipeline Orchestrator
==============================
PHASE 09B end-to-end pipeline:

    dataset -> discovery -> backtest -> walk-forward -> OOS -> robustness
        -> score -> registry

A candidate NEVER becomes live automatically. Promotion is operator-gated on
the production side. Research is OFFLINE / BACKGROUND and never blocks the
LiveEngine tick path (spec 31 / 32 / 42).
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
    ) -> None:
        self.dataset_builder = dataset_builder
        self.registry = registry
        self.backtest = backtest or BacktestEngine()
        self.walkforward = walkforward or WalkForwardEngine()
        self.oos_gate = oos_gate or OOSGate()
        self.robustness = robustness or RobustnessEngine()
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
    ) -> dict[str, Any]:
        """
        Runs the complete evidence pipeline for one candidate and persists the
        registry entry.

        Pipeline: BACKTESTING -> VALIDATING (walk-forward) -> OOS_TESTING ->
        ROBUSTNESS_TESTING -> VALIDATED/REJECTED (via score verdict).

        NEVER promotes to ACTIVE automatically.
        """
        sid = candidate.strategy_id
        version = candidate.strategy_version

        # 1. BACKTEST (in-sample only; never OOS)
        bt = self.backtest.run(
            dataset,
            strategy_id=sid,
            strategy_version=version,
            use_split=True,
        )
        if bt.total_trades == 0:
            logger.warning("[STRATEGY_VALIDATION] event=ABORTED empty dataset", strategy_id=sid)
            return self._register(
                candidate, dataset, lifecycle=CandidateLifecycle.REJECTED, backtest=bt
            )

        # 2. WALK-FORWARD (VALIDATING)
        wf = self.walkforward.validate(
            dataset,
            strategy_id=sid,
            strategy_version=version,
            n_splits=n_folds,
            purge_seconds=purge_seconds,
            embargo_seconds=embargo_seconds,
        )

        # 3. OOS GATE (OOS_TESTING)
        oos = self.oos_gate.evaluate(
            dataset,
            strategy_id=sid,
            strategy_version=version,
            purge_seconds=purge_seconds,
            embargo_seconds=embargo_seconds,
        )

        # 4. ROBUSTNESS (ROBUSTNESS_TESTING)
        rob = self.robustness.evaluate(dataset, strategy_id=sid, strategy_version=version)

        # 5. SCORE + VERDICT
        score = compute_strategy_score(
            dataset, backtest=bt, walkforward=wf, oos=oos, robustness=rob
        )
        final_lifecycle = (
            CandidateLifecycle.VALIDATED
            if score.verdict == "VALIDATED"
            else CandidateLifecycle.REJECTED
        )

        result = self._register(
            candidate,
            dataset,
            lifecycle=final_lifecycle,
            backtest=bt,
            walkforward=wf,
            oos=oos,
            robustness=rob,
            score=score,
        )
        self._record_run(
            run_id=run_id,
            candidate=candidate,
            dataset=dataset,
            summary={
                "lifecycle": final_lifecycle.value,
                "expectancy_r": bt.expectancy_r,
                "oos_expectancy_r": oos.oos_expectancy_r,
                "oos_status": oos.status,
                "robustness_status": rob.status,
                "score": score.final_score if score else 0.0,
                "verdict": score.verdict if score else "INCONCLUSIVE",
            },
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
                        ),
                    )
                )
            except Exception as e:
                logger.error("[STRATEGY_RESEARCH] run record failed", error=str(e))


_INSERT_RUN_SQL = """
    INSERT INTO research_runs
    (run_id, dataset_id, strategy_id, strategy_version, executed_at, config,
     build_identity, result_summary)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(run_id) DO NOTHING;
"""
