"""Candidate Trainer (PHASE 13, spec 22 / 36).

Builds CandidateModelArtifacts from DatasetArtifact + ExperimentConfig.
NEVER writes to the Champion path. A failed training run is FAILED, never
CHALLENGER.

Reuses the Phase 10 candidate/staging safety model. This module trains with
a compact, deterministic loop suitable for behavioral tests + CLI smoke
tests; production-scale training continues through the Phase 10
ChallengerTrainer / WalkForwardTrainer (same candidate-staging boundary).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.model_factory import ModelFactory
from nexus_scalp.model_generation.models import (
    ExperimentConfig,
    ModelManifest,
    default_label_schema,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.training")


def _split_columns(frame: pl.DataFrame, news_enabled: bool) -> tuple[list[str], list[str]]:
    """Feature cols = feat_*; news cols = news_* (when enabled)."""
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    news_cols = [
        c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"
    ]
    if not news_enabled:
        news_cols = []
    return feat_cols, news_cols


class CandidateTrainer:
    """Trains one candidate model from a dataset artifact frame."""

    def __init__(self, store: ArtifactStore | None = None) -> None:
        self.store = store or ArtifactStore()
        self.model_factory = ModelFactory()
        self.label_schema = default_label_schema()

    def train_candidate(
        self,
        experiment: ExperimentConfig,
        dataset_frame: pl.DataFrame,
        *,
        feature_cols: list[str] | None = None,
        model_id: str | None = None,
        epochs: int | None = None,
    ) -> dict[str, Any]:
        """Trains and persists a candidate artifact. Never touches Champion.

        Returns {status, model_id, artifact, error?}.
        """
        if dataset_frame.is_empty():
            return {"status": "FAILED", "error": "empty dataset"}
        if "label" not in dataset_frame.columns:
            return {"status": "FAILED", "error": "missing label column"}

        feat_cols, news_cols = _split_columns(dataset_frame, experiment.news_enabled)
        if feature_cols:
            feat_cols = [c for c in feature_cols if c in dataset_frame.columns]
        if not feat_cols:
            return {"status": "FAILED", "error": "no feature columns in dataset"}

        # labels -> numpy (validate 3-class contract)
        labels = dataset_frame["label"].to_numpy().astype(np.int64)
        try:
            self.label_schema.validate_labels(labels.tolist())
        except ValueError as e:
            return {"status": "FAILED", "error": str(e)}

        # features (feat_*) + optional news features (news_*)
        X_arr = dataset_frame.select(feat_cols).to_numpy().astype(np.float32)
        if news_cols:
            X_news = dataset_frame.select(news_cols).to_numpy().astype(np.float32)
            X_arr = np.hstack([X_arr, X_news])

        # split: train on non-test rows, validate on test rows
        split = dataset_frame.get_column("_split") if "_split" in dataset_frame.columns else None
        if split is None:
            # fall back: last 20% as validation
            n = X_arr.shape[0]
            train_idx = np.arange(int(n * 0.8))
            val_idx = np.arange(int(n * 0.8), n)
        else:
            train_idx = np.where(split.to_numpy() != "test")[0]
            val_idx = np.where(split.to_numpy() == "test")[0]

        if len(train_idx) == 0 or len(val_idx) == 0:
            return {"status": "FAILED", "error": "empty train/val split"}

        # ------------------------------------------------------------------
        # Feature scaling (distribution parity, forensic audit T24).
        # Fit mean/std on the TRAIN split ONLY (zero leakage into val/OOS) and
        # persist it with the artifact so the runtime applies the SAME
        # transform during inference. Matches the legacy WalkForwardTrainer's
        # isolated-fitted-scaler invariant.
        # ------------------------------------------------------------------
        feat_mean = X_arr[train_idx].mean(axis=0).astype(np.float32)
        feat_std = X_arr[train_idx].std(axis=0).astype(np.float32)
        feat_std = np.where(feat_std < 1e-8, 1.0, feat_std)  # constant cols -> identity
        X_scaled = (X_arr - feat_mean) / feat_std

        model = self.model_factory.build(
            architecture=experiment.architecture,
            num_classes=int(experiment.class_count or 3),
            parameters={
                "input_dim": X_arr.shape[1],
                **(experiment.architecture_parameters or {}),
            },
        )

        seed = int(experiment.seed or 42)
        torch.manual_seed(seed)
        np.random.seed(seed)

        epochs_n = epochs or int((experiment.training or {}).get("epochs", 10))
        lr = float((experiment.training or {}).get("learning_rate", 0.001))
        batch_size = int((experiment.training or {}).get("batch_size", 256))

        train_ds = TensorDataset(
            torch.from_numpy(X_scaled[train_idx]),
            torch.from_numpy(labels[train_idx]),
        )
        loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)

        optimizer = torch.optim.Adam(model.parameters(), lr=lr)
        criterion = nn.CrossEntropyLoss()

        model.train()
        for _ in range(epochs_n):
            for xb, yb in loader:
                optimizer.zero_grad()
                out = model(xb)
                loss = criterion(out, yb)
                loss.backward()
                optimizer.step()

        # eval on validation (SCALED with the train-fitted transform)
        model.eval()
        with torch.inference_mode():
            val_logits = model(torch.from_numpy(X_scaled[val_idx]))
            val_preds = val_logits.argmax(dim=1).numpy()
        val_acc = float(np.mean(val_preds == labels[val_idx])) if len(val_idx) else 0.0

        mid = (
            model_id
            or f"candidate_{experiment.experiment_id}_{datetime.now(UTC).strftime('%H%M%S')}"
        )
        manifest = ModelManifest(
            model_id=mid,
            model_version="1.0.0",
            role="CANDIDATE",
            status="TRAINED",
            architecture_id=experiment.architecture,
            architecture_parameters=experiment.architecture_parameters,
            feature_schema_id=dataset_frame["feature_schema_id"][0]
            if "feature_schema_id" in dataset_frame.columns
            else "scalp_v1",
            feature_dimension=len(feat_cols),
            label_schema_id=self.label_schema.label_schema_id,
            label_schema_version=self.label_schema.version,
            class_count=self.label_schema.class_count,
            classes=self.label_schema.class_names,
            dataset_id=experiment.dataset_id,
            random_seed=seed,
            training_config=experiment.training,
            optimizer="adam",
            strategy_id=experiment.strategy_id,
            strategy_version=experiment.strategy_version,
            news_enabled=experiment.news_enabled,
            news_schema_version=experiment.news_schema_id,
            walk_forward_status="",
            oos_status="",
            final_validation_result={
                "val_accuracy": round(val_acc, 4),
                "epochs": epochs_n,
                "train_rows": len(train_idx),
                "val_rows": len(val_idx),
            },
            build_metadata={
                "trainer": "CandidateTrainer",
                "news_features": news_cols,
                "input_dimension": len(feat_cols) + len(news_cols),
            },
        )

        artifact = self.store.save_model_artifact(
            mid,
            model.state_dict(),
            manifest.model_dump(mode="json"),
            scaler=(feat_mean, feat_std),
        )
        logger.info("[TRAIN] event=CANDIDATE_READY model_id=%s val_acc=%.4f", mid, val_acc)
        return {
            "status": "COMPLETED",
            "model_id": mid,
            "val_accuracy": round(val_acc, 4),
            "artifact": artifact,
        }
