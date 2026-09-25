"""Experiment Factory (PHASE 13, spec 18 / 22 / 36 / 37).

Training becomes experiment-driven. Bounded, explainable experiment spaces —
no random hyperparameter generation. Failed experiments are FAILED, never
CHALLENGER; Champion is never overwritten.

The trainer reuses the Phase 10 `ChallengerTrainer` (candidate/staging
safety) rather than duplicating a training implementation (spec 22 / 23).
"""

from __future__ import annotations

from typing import Any

import polars as pl

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.models import ExperimentConfig
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.experiment_factory")


#: Bounded, explainable experiment space (spec 18). Not random.
EXPERIMENT_SPACE: dict[str, dict[str, Any]] = {
    "baseline_scalpnet_v1": {
        "architecture": "LEGACY_SCALPNET_V1",
        "architecture_parameters": {"hidden_dim": 128, "num_heads": 4, "dropout_rate": 0.25},
        "training": {"epochs": 10, "batch_size": 256, "learning_rate": 0.001, "seed": 42},
        "news_enabled": False,
    },
    "baseline_scalpnet_v1_news": {
        "architecture": "LEGACY_SCALPNET_V1",
        "architecture_parameters": {"hidden_dim": 128, "num_heads": 4, "dropout_rate": 0.25},
        "training": {"epochs": 10, "batch_size": 256, "learning_rate": 0.001, "seed": 42},
        "news_enabled": True,
    },
    "mlp_v2": {
        "architecture": "MLP_V2",
        "architecture_parameters": {"hidden_dim": 128, "layers": 3, "dropout": 0.15},
        "training": {"epochs": 12, "batch_size": 256, "learning_rate": 0.001, "seed": 42},
        "news_enabled": False,
    },
    "mlp_v2_news": {
        "architecture": "MLP_V2",
        "architecture_parameters": {"hidden_dim": 128, "layers": 3, "dropout": 0.15},
        "training": {"epochs": 12, "batch_size": 256, "learning_rate": 0.001, "seed": 42},
        "news_enabled": True,
    },
    "tcn_attention_v1": {
        "architecture": "TCN_ATTENTION_V1",
        "architecture_parameters": {
            "hidden_dim": 128,
            "blocks": 3,
            "attention_heads": 4,
            "dropout": 0.15,
        },
        "training": {
            "epochs": 12,
            "batch_size": 128,
            "learning_rate": 0.001,
            "weight_decay": 0.0001,
            "seed": 42,
        },
        "news_enabled": False,
    },
    "tcn_attention_v1_news": {
        "architecture": "TCN_ATTENTION_V1",
        "architecture_parameters": {
            "hidden_dim": 128,
            "blocks": 3,
            "attention_heads": 4,
            "dropout": 0.15,
        },
        "training": {
            "epochs": 12,
            "batch_size": 128,
            "learning_rate": 0.001,
            "weight_decay": 0.0001,
            "seed": 42,
        },
        "news_enabled": True,
    },
}


class ExperimentFactory:
    """Creates + persists experiments bound to a dataset artifact."""

    def __init__(self, store: ArtifactStore | None = None) -> None:
        self.store = store or ArtifactStore()

    def create(
        self,
        dataset_id: str,
        template: str = "baseline_scalpnet_v1",
        *,
        experiment_id: str | None = None,
        strategy_id: str = "scalp_default",
        strategy_version: str = "1.0.0",
        seed: int | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> ExperimentConfig:
        if template not in EXPERIMENT_SPACE:
            raise ValueError(
                f"Unknown experiment template {template!r}; available: {sorted(EXPERIMENT_SPACE)}"
            )
        base = dict(EXPERIMENT_SPACE[template])
        if overrides:
            base.update(overrides)

        import uuid

        eid = experiment_id or f"exp_{template}_{uuid.uuid4().hex[:8]}"
        training = dict(base.get("training", {}))
        if seed is not None:
            training["seed"] = seed

        cfg = ExperimentConfig(
            experiment_id=eid,
            dataset_id=dataset_id,
            architecture=str(base["architecture"]),
            architecture_parameters=dict(base.get("architecture_parameters", {})),
            training=training,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            news_enabled=bool(base.get("news_enabled", False)),
            news_schema_id="news_context_v1",
            seed=int(training.get("seed", 42)),
        )
        self.store.save_experiment(eid, cfg.model_dump(mode="json"))
        logger.info("[EXPERIMENT] event=CREATED experiment_id=%s template=%s", eid, template)
        return cfg

    def load(self, experiment_id: str) -> ExperimentConfig:
        raw = self.store.read_experiment(experiment_id)
        if not raw:
            raise FileNotFoundError(f"Experiment {experiment_id} not found")
        return ExperimentConfig(**raw)


def train_experiment(
    experiment: ExperimentConfig,
    dataset_frame: pl.DataFrame,
    store: ArtifactStore,
    *,
    feature_cols: list[str] | None = None,
) -> dict[str, Any]:
    """Runs one experiment through the existing candidate-safe trainer.

    Returns {status: COMPLETED/FAILED, model_id, artifact: {...}}.

    Uses a minimal training harness: the full WalkForwardTrainer integration
    can be switched here; the safety boundary (candidate staging, never
    overwrite Champion) is inherited from Phase 10's trainer contract.
    """
    from nexus_scalp.model_generation.training import CandidateTrainer

    trainer = CandidateTrainer(store=store)
    result = trainer.train_candidate(
        experiment=experiment,
        dataset_frame=dataset_frame,
        feature_cols=feature_cols,
    )
    return result
