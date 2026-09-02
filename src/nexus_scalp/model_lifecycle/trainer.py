"""
Challenger Trainer
==================
PHASE 10 controlled OFFLINE training that produces a CANDIDATE model -
never a production model (spec 16 / 25 / 33).

The trainer reuses the existing production-grade `WalkForwardTrainer`
(schema-driven, purged walk-forward, class-balanced, anti-collapse) instead of
duplicating a second training implementation (spec 11). Its ONLY addition is
the safety boundary: every artifact is written to a candidate/staging path,
and the Champion artifact is NEVER overwritten.

An interrupted/failed training run remains FAILED / INCOMPLETE - never
VALIDATED. Only a fully verified artifact may become a Challenger.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import polars as pl

from nexus_scalp.model_lifecycle.champion import ChampionManager
from nexus_scalp.model_lifecycle.integrity import inspect_artifact
from nexus_scalp.model_lifecycle.models import (
    TrainingDataset,
    TrainingRun,
    TrainingRunStatus,
)
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

logger = get_logger("nexus_scalp.model_lifecycle.trainer")


class ChallengerTrainer:
    """
    Offline candidate trainer with a hard Champion-protection boundary.

    `train_challenger()` returns an immutable TrainingRun. The candidate
    artifact is written under `candidate/<run_id>/`; the Champion path is
    never touched. No call path here can place an order.
    """

    def __init__(
        self,
        champion_manager: ChampionManager,
        train_dataset: TrainingDataset,
        feature_cols: list[str] | None = None,
        hyperparameters: dict[str, Any] | None = None,
        num_epochs: int = 10,
        random_seed: int = 42,
        build_identity: str = "",
    ) -> None:
        self.champion_manager = champion_manager
        self.dataset = train_dataset
        self.feature_cols = feature_cols
        self.hyperparameters = dict(hyperparameters or {})
        self.num_epochs = int(num_epochs)
        self.seed = int(random_seed)
        self.build_identity = build_identity

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def train(self, run_id: str | None = None) -> TrainingRun:
        """
        Runs one controlled training pass and returns the immutable TrainingRun.

        Steps:
          1. resolve run_id + candidate artifact paths (staging, never champion)
          2. convert the TrainingDataset to the trainer's Polars frame
          3. invoke the existing WalkForwardTrainer with artifact_path pointed
             at the candidate staging path
          4. inspect the produced artifact for integrity
          5. record metrics; a failure => TrainingRunStatus.FAILED
        """
        run_id = run_id or f"tr_{uuid.uuid4().hex[:12]}"
        run = TrainingRun(
            run_id=run_id,
            dataset_id=self.dataset.dataset_id,
            feature_schema_id=self.dataset.feature_schema_id,
            feature_dimension=self.dataset.feature_dimension,
            hyperparameters=self.hyperparameters,
            random_seed=self.seed,
            architecture="scalp_net",
            embargo_bars=int(self.hyperparameters.get("embargo_bars", 15)),
            purge_bars=int(self.hyperparameters.get("purge_bars", 15)),
            started_at=datetime.now(UTC),
            status=TrainingRunStatus.RUNNING,
            build_identity=self.build_identity,
        )

        champion = self.champion_manager.champion_or_none()
        if champion is not None:
            run = run.model_copy(
                update={
                    "parent_champion_id": champion.model_id,
                    "parent_champion_version": champion.model_version,
                }
            )

        cand_path = self.champion_manager.candidate_artifact_path(run_id)
        cand_scaler = self.champion_manager.candidate_scaler_path(run_id)

        logger.info(
            "[TRAINING] event=START",
            run_id=run_id,
            dataset_id=self.dataset.dataset_id,
            schema=self.dataset.feature_schema_id,
            samples=self.dataset.sample_count,
        )

        try:
            df = self._to_polars_frame()
            if df.is_empty():
                raise ValueError("Training dataset produced an empty polars frame")

            cols = self.feature_cols or [f"feat_{i}" for i in range(self.dataset.feature_dimension)]
            self._validate_columns(df, cols)

            trainer = WalkForwardTrainer(
                num_folds=int(self.hyperparameters.get("num_folds", 34)),
                batch_size=int(self.hyperparameters.get("batch_size", 256)),
                learning_rate=float(self.hyperparameters.get("learning_rate", 5e-4)),
                epochs_per_fold=int(self.hyperparameters.get("epochs_per_fold", 10)),
                early_stopping_patience=int(self.hyperparameters.get("early_stopping_patience", 3)),
                purge_gap_bars=int(self.hyperparameters.get("purge_gap_bars", 15)),
                random_seed=self.seed,
                artifact_save_path=cand_path,
                feature_schema_id=self.dataset.feature_schema_id,
                embargo_bars=int(self.hyperparameters.get("embargo_bars", 15)),
            )
            # Staging scaler path: point the trainer's scaler at the candidate
            # folder so it never overwrites the champion's model.scaler.npz.
            trainer._get_scaler_path = lambda: cand_scaler  # type: ignore[method-assign]

            start = time.perf_counter()
            trainer.train_and_validate(df, feature_cols=cols)
            elapsed = time.perf_counter() - start

            info = inspect_artifact(
                cand_path,
                cand_scaler,
                model_id=f"candidate_{run_id}",
                model_version=run_id,
                feature_schema_id=self.dataset.feature_schema_id,
                feature_dimension=self.dataset.feature_dimension,
            )

            metrics: dict[str, Any] = {
                "train_seconds": round(elapsed, 3),
                "final_loss": None,
                "validation_accuracy": None,
            }
            # Pull whatever metrics the trainer exposed after training.
            for attr in ("last_val_loss", "last_validation_accuracy", "final_loss"):
                if hasattr(trainer, attr):
                    metrics[attr] = getattr(trainer, attr)

            run = run.model_copy(
                update={
                    "artifacts": [info],
                    "metrics": metrics,
                    "finished_at": datetime.now(UTC),
                    "status": TrainingRunStatus.COMPLETED,
                }
            )
            logger.info(
                "[TRAINING] event=COMPLETE",
                run_id=run_id,
                status="COMPLETED",
                artifact_hash=info.artifact_hash,
                elapsed_ms=round(elapsed * 1000.0),
            )
            return run

        except Exception as e:
            run = run.model_copy(
                update={
                    "finished_at": datetime.now(UTC),
                    "status": TrainingRunStatus.FAILED,
                    "failure_reason": str(e),
                }
            )
            logger.error(
                "[TRAINING] event=FAILED",
                run_id=run_id,
                error=str(e),
                exc_info=True,
            )
            return run

    # ------------------------------------------------------------------
    # Dataset -> Polars frame conversion (deterministic)
    # ------------------------------------------------------------------

    def _to_polars_frame(self) -> pl.DataFrame:
        """Builds the trainer's expected labeled frame from the dataset rows."""
        rows = self.dataset.ordered_rows()
        data: dict[str, list[Any]] = {
            "label": [r.label for r in rows],
            "open": [0.0] * len(rows),
            "high": [0.0] * len(rows),
            "low": [0.0] * len(rows),
            "close": [0.0] * len(rows),
            "tick_volume": [0] * len(rows),
            "timestamp": [r.decision_timestamp for r in rows],
        }
        for i in range(self.dataset.feature_dimension):
            data[f"feat_{i}"] = [r.feature_vector[i] for r in rows]
        return pl.DataFrame(data)

    @staticmethod
    def _validate_columns(df: pl.DataFrame, cols: list[str]) -> None:
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise ValueError(f"Training frame missing feature columns: {missing}")
        if len(cols) != len([c for c in df.columns if c.startswith("feat_")]):
            raise ValueError(
                f"Feature column count mismatch: expected {len(cols)}, "
                f"frame has {len([c for c in df.columns if c.startswith('feat_')])}"
            )


def summarize_run(run: TrainingRun) -> dict[str, Any]:
    """JSON-friendly run summary for APIs / dashboards."""
    return {
        "run_id": run.run_id,
        "dataset_id": run.dataset_id,
        "schema": f"{run.feature_schema_id}/{run.feature_dimension}D",
        "seed": run.random_seed,
        "status": run.status.value,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "parent_champion": (
            f"{run.parent_champion_id}@{run.parent_champion_version}"
            if run.parent_champion_id
            else ""
        ),
        "metrics": run.metrics,
        "gates": [g.model_dump() for g in run.gates],
        "failure_reason": run.failure_reason,
        "artifacts": [a.model_dump() for a in run.artifacts],
        "eligible_as_challenger": run.eligible_as_challenger,
    }
