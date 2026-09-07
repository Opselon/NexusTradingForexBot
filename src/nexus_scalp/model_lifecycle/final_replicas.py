"""Final-replica training (research/training-parity P2).

The final production artifact is a NEW model trained on the full dataset —
it was never directly evaluated on a held-out window, and a single lucky
seed must not silently become the champion when training variance is high.

Contract:
  * trains N replicas of the final full-data model from DISTINCT seeds
    (seed = base_seed + replica index; base seed comes from the canonical
    training provenance),
  * per replica records: seed, OOS net expectancy, max drawdown, best epoch,
    artifact hash,
  * reports mean / median / std / min / max across replicas so training
    variance is MEASURED, not assumed,
  * the selected production artifact is the MEDIAN (most-typical) replica by
    net expectancy — a lucky best seed is never auto-selected, and the full
    replica evidence travels with the candidate for the promotion gate,
  * every replica artifact is identity-bound (model_sha256 from the emission
    gate manifest) so "the model that was evaluated" is exactly identifiable.
"""

from __future__ import annotations

import hashlib
import statistics
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

logger = get_logger("nexus_scalp.model_lifecycle.final_replicas")

#: Default replica count. Small, evidence-appropriate: enough to estimate
#: training variance (>=2 for std), bounded compute. Configurable per call;
#: the previously suggested "3" is a candidate default, NOT a mandate.
DEFAULT_REPLICAS: int = 3

#: Feature columns are the canonical feat_i sequence for the schema dim.
def _feat_cols(dim: int) -> list[str]:
    return [f"feat_{i}" for i in range(dim)]


@dataclass(frozen=True)
class ReplicaResult:
    """One replica's identity + OOS evidence (all values auditable)."""

    replica: int
    seed: int
    net_expectancy_r: float | None
    max_fold_drawdown_r: float | None
    best_epoch: int | None
    artifact_sha256: str
    training_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplicaSummary:
    """Aggregate evidence across replicas (mean/median/std/min/max)."""

    replicas: int
    net_expectancy_mean_r: float | None
    net_expectancy_median_r: float | None
    net_expectancy_std_r: float | None
    net_expectancy_min_r: float | None
    net_expectancy_max_r: float | None
    drawdown_max_r: float | None
    selected_replica: int
    selected_seed: int
    selected_artifact_sha256: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "replicas": self.replicas,
            "net_expectancy_mean_r": self.net_expectancy_mean_r,
            "net_expectancy_median_r": self.net_expectancy_median_r,
            "net_expectancy_std_r": self.net_expectancy_std_r,
            "net_expectancy_min_r": self.net_expectancy_min_r,
            "net_expectancy_max_r": self.net_expectancy_max_r,
            "drawdown_max_r": self.drawdown_max_r,
            "selected_replica": self.selected_replica,
            "selected_seed": self.selected_seed,
            "selected_artifact_sha256": self.selected_artifact_sha256,
            "rows": list(self.rows),
        }


class FinalReplicaTrainer:
    """Trains N seeded replicas of the final production model and selects
    the MEDIAN replica, publishing the exact selected artifact identity."""

    def __init__(
        self,
        *,
        num_replicas: int = DEFAULT_REPLICAS,
        base_seed: int = 42,
        num_folds: int = 4,
        epochs_per_fold: int = 3,
        purge_gap_bars: int = 15,
        walk_forward_mode: str = "expanding",
        feature_schema_id: str | None = None,
        feature_dimension: int | None = None,
        artifact_dir: Any,
        smoke: bool = False,
        trainer_kwargs: dict[str, Any] | None = None,
    ) -> None:
        if num_replicas < 2:
            raise ValueError(
                f"num_replicas must be >= 2 to measure variance, got {num_replicas}"
            )
        self.num_replicas = int(num_replicas)
        self.base_seed = int(base_seed)
        self.num_folds = int(num_folds)
        self.epochs_per_fold = int(epochs_per_fold)
        self.purge_gap_bars = int(purge_gap_bars)
        self.walk_forward_mode = str(walk_forward_mode)
        self.feature_schema_id = feature_schema_id
        self.feature_dimension = int(feature_dimension) if feature_dimension else None
        from pathlib import Path as _Path

        self.artifact_dir = _Path(artifact_dir)
        self.smoke = bool(smoke)
        self.trainer_kwargs = dict(trainer_kwargs or {})

    # ------------------------------------------------------------------

    def _replica_dir(self, replica: int) -> Any:
        return self.artifact_dir / f"replica_{replica:02d}"

    def _train_one(
        self,
        df: pl.DataFrame,
        feature_cols: list[str],
        replica: int,
        seed: int,
    ) -> tuple[WalkForwardTrainer, ReplicaResult]:
        out_dir = self._replica_dir(replica)
        artifact_path = out_dir / "model.pt"
        trainer = WalkForwardTrainer(
            num_folds=self.num_folds,
            epochs_per_fold=self.epochs_per_fold,
            purge_gap_bars=self.purge_gap_bars,
            walk_forward_mode=self.walk_forward_mode,
            feature_schema_id=self.feature_schema_id,
            artifact_save_path=artifact_path,
            random_seed=seed,
            smoke=self.smoke,
            **self.trainer_kwargs,
        )
        model = trainer.train_and_validate(df, feature_cols)
        del model  # the artifact bundle is the identity; the object is not kept

        conv = getattr(trainer, "last_convergence_metadata", {}) or {}
        artifact_sha = self._artifact_sha256(artifact_path)
        return trainer, ReplicaResult(
            replica=replica,
            seed=seed,
            net_expectancy_r=conv.get("net_expectancy_r"),
            max_fold_drawdown_r=conv.get("max_fold_drawdown_r"),
            best_epoch=self._best_epoch(conv),
            artifact_sha256=artifact_sha,
            training_config={
                "walk_forward_mode": self.walk_forward_mode,
                "num_folds": self.num_folds,
                "epochs_per_fold": self.epochs_per_fold,
                "purge_gap_bars": self.purge_gap_bars,
            },
        )

    @staticmethod
    def _best_epoch(convergence: dict[str, Any]) -> int | None:
        """Median best_epoch across folds (stable integer summary)."""
        folds = convergence.get("folds") or []
        bests = [
            int(f.get("best_epoch"))
            for f in folds
            if isinstance(f, dict) and f.get("best_epoch") is not None
        ]
        return int(statistics.median(bests)) if bests else None

    @staticmethod
    def _artifact_sha256(path: Any) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------------

    def train_replicas(
        self,
        df: pl.DataFrame,
        feature_cols: list[str] | None = None,
        *,
        feature_dimension: int | None = None,
    ) -> ReplicaSummary:
        """Trains all replicas and returns the aggregate evidence.

        Selection = MEDIAN replica by net expectancy (most typical training
        outcome). The selected artifact's directory + sha256 are recorded so
        the promotion pipeline evaluates EXACTLY the published candidate.
        """
        if feature_cols is None:
            dim = int(
                feature_dimension
                or self.feature_dimension
                or self.trainer_kwargs.get("feature_dimension")
                or 0
            )
            feature_cols = _feat_cols(dim) if dim else None
        if feature_cols is None:
            raise ValueError("feature_cols not provided and dimension unresolvable")

        rows: list[dict[str, Any]] = []
        results: list[ReplicaResult] = []
        for i in range(self.num_replicas):
            seed = self.base_seed + i
            logger.info(
                "[FINAL_REPLICA] start replica=%d/%d seed=%d", i + 1, self.num_replicas, seed
            )
            _, result = self._train_one(df, feature_cols, i + 1, seed)
            results.append(result)
            rows.append(
                {
                    "replica": result.replica,
                    "seed": result.seed,
                    "net_expectancy_r": result.net_expectancy_r,
                    "max_fold_drawdown_r": result.max_fold_drawdown_r,
                    "best_epoch": result.best_epoch,
                    "artifact_sha256": result.artifact_sha256,
                }
            )

        nets = [r.net_expectancy_r for r in results if r.net_expectancy_r is not None]
        if len(nets) != len(results):
            logger.warning(
                "[FINAL_REPLICA] %d/%d replicas missing net expectancy evidence",
                len(results) - len(nets),
                len(results),
            )
        mean = float(np.mean(nets)) if nets else None
        median = float(np.median(nets)) if nets else None
        std = float(np.std(nets, ddof=1)) if len(nets) >= 2 else None
        # Median replica by net expectancy (ties -> lowest replica index).
        # Without evidence, selection is refused (fail-closed: never publish
        # an unmeasured final artifact).
        if not nets:
            raise RuntimeError(
                "FINAL_REPLICA_SELECTION_REFUSED: no replica produced net "
                "expectancy evidence; the final artifact may not be selected"
            )
        ranked = sorted(
            (r for r in results if r.net_expectancy_r is not None),
            key=lambda r: (r.net_expectancy_r, r.replica),
        )
        # Median replica by net expectancy — for an odd count, len//2 lands
        # exactly on the middle element (0.1 of [0.1, 0.5, 5.0] -> index 1).
        # Ties break to the LOWEST net expectancy (conservative: never select
        # a luckier replica when two are equally ranked).
        median_rank = (len(ranked) - 1) // 2
        selected = ranked[median_rank]

        summary = ReplicaSummary(
            replicas=self.num_replicas,
            net_expectancy_mean_r=mean,
            net_expectancy_median_r=median,
            net_expectancy_std_r=std,
            net_expectancy_min_r=min(nets),
            net_expectancy_max_r=max(nets),
            drawdown_max_r=(
                max((r.max_fold_drawdown_r for r in results if r.max_fold_drawdown_r is not None), default=None)
            ),
            selected_replica=selected.replica,
            selected_seed=selected.seed,
            selected_artifact_sha256=selected.artifact_sha256,
            rows=rows,
        )
        logger.info(
            "[FINAL_REPLICA] selected replica=%d seed=%d sha=%s net_mean=%.4f net_std=%s",
            summary.selected_replica,
            summary.selected_seed,
            summary.selected_artifact_sha256[:12],
            summary.net_expectancy_mean_r or 0.0,
            summary.net_expectancy_std_r,
        )
        return summary


__all__ = [
    "DEFAULT_REPLICAS",
    "FinalReplicaTrainer",
    "ReplicaResult",
    "ReplicaSummary",
]
