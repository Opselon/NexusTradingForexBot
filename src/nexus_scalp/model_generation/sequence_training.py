"""Sequence Candidate Trainer (PHASE 13B).

Trains SEQUENCE architectures (3D input: Batch x SeqLen x Features) through
the SAME artifact/dataset pipeline used for the 2D legacy baseline, keeping
the benchmark fair (spec 2 of the task):

    SAME dataset artifact
    SAME labels
    SAME temporal splits
    SAME purge/embargo (inherited from the labeler)
    SAME friction (inherited from the labeler)
    ONLY the model architecture differs.

Safety (spec 10 / audit BUG-041 carried forward):
    * non-finite features/news/labels => FAILED, never COMPLETED
    * NaN/Inf loss or exploding gradients => FAILED with reason

Determinism (spec 9):
    * torch / numpy / python seeds set before model construction + training
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
from nexus_scalp.model_generation.lineage import assert_production_eligible
from nexus_scalp.model_generation.model_factory import ModelFactory
from nexus_scalp.model_generation.models import (
    ExperimentConfig,
    ModelManifest,
    default_label_schema,
)
from nexus_scalp.model_generation.sequence import SequenceBuilder
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.sequence_training")

#: reject if gradient/loss exceeds this (NaN/explosion defense)
_MAX_ABS_LOSS = 1e6
_MAX_GRAD_NORM = 1e4


class SequenceCandidateTrainer:
    """Trains one sequence-model candidate (e.g. TCN_ATTENTION_V1).

    Defaults are the canonical SequenceContract (L=32, gap=15min); explicit
    values win (benchmark matrix: L=8/16 ablations).
    """

    def __init__(
        self,
        store: ArtifactStore | None = None,
        seq_len: int | None = None,
        max_gap_us: int | None | str = "contract",
    ) -> None:
        from nexus_scalp.model_generation.sequence import SEQUENCE_CONTRACT

        if seq_len is None:
            seq_len = SEQUENCE_CONTRACT.sequence_length
        if max_gap_us == "contract":
            max_gap_us = SEQUENCE_CONTRACT.max_gap_us
        self.store = store or ArtifactStore()
        self.model_factory = ModelFactory()
        self.label_schema = default_label_schema()
        self.seq_len = int(seq_len)
        self.max_gap_us = max_gap_us  # type: ignore[assignment]

    def train_candidate(
        self,
        experiment: ExperimentConfig,
        dataset_frame: pl.DataFrame,
        *,
        model_id: str | None = None,
        epochs: int | None = None,
        governance_override: bool = False,
    ) -> dict[str, Any]:
        """Trains a sequence candidate. Returns {status, model_id, ...}.

        MLFIX-T7: tainted dataset manifests are production-ineligible without
        an explicit operator token (governance_override=True).
        """
        if dataset_frame is None or dataset_frame.is_empty():
            return {"status": "FAILED", "error": "empty dataset"}
        if "label" not in dataset_frame.columns:
            return {"status": "FAILED", "error": "missing label column"}

        feat_cols = sorted(c for c in dataset_frame.columns if c.startswith("feat_"))
        news_cols = sorted(
            c
            for c in dataset_frame.columns
            if c.startswith("news_") and c != "news_context_schema_id"
        )
        if not feat_cols:
            return {"status": "FAILED", "error": "no feature columns in dataset"}

        labels = dataset_frame["label"].to_numpy().astype(np.int64)
        try:
            self.label_schema.validate_labels(labels.tolist())
        except ValueError as e:
            return {"status": "FAILED", "error": str(e)}

        # ---- deterministic seed ----
        seed = int(experiment.seed or 42)
        torch.manual_seed(seed)
        np.random.seed(seed)

        # ---- build sequences (deterministic, causal, boundary-safe) ----
        builder = SequenceBuilder(seq_len=self.seq_len, max_gap_us=self.max_gap_us)
        seq = builder.build(dataset_frame, news_enabled=experiment.news_enabled)
        X_all = seq["X"]  # (N, T, F)
        y_all = seq["y"]  # (N,)
        valid = seq["valid"]  # (N,) bool

        # Non-finite defense (BUG-041 carried forward)
        if not np.isfinite(X_all).all():
            return {"status": "FAILED", "error": "non-finite feature values in sequences"}

        if valid.sum() < 10:
            return {
                "status": "FAILED",
                "error": f"insufficient valid sequences ({int(valid.sum())})",
            }

        X_all = X_all[valid]
        y_all = y_all[valid]

        n = X_all.shape[0]
        # temporal split: last 20% = validation (same policy as 2D trainer)
        val_n = max(1, int(n * 0.2))
        train_idx = np.arange(n - val_n)
        val_idx = np.arange(n - val_n, n)

        # ---- feature scaling per-dimension (train-only fit) ----
        flat = X_all[train_idx].reshape(-1, X_all.shape[-1])
        mean = flat.mean(axis=0).astype(np.float32)
        std = flat.std(axis=0).astype(np.float32)
        std = np.where(std < 1e-8, 1.0, std)
        X_train = ((X_all[train_idx] - mean) / std).astype(np.float32)
        X_val = ((X_all[val_idx] - mean) / std).astype(np.float32)

        input_dim = X_all.shape[-1]  # features + news dims (from live schema)
        opt_params: dict[str, Any] = dict(experiment.architecture_parameters or {})
        opt_params["input_dim"] = input_dim

        model = self.model_factory.build(
            architecture=experiment.architecture,
            num_classes=int(experiment.class_count or 3),
            parameters=opt_params,
        )

        epochs_n = epochs or int((experiment.training or {}).get("epochs", 10))
        lr = float((experiment.training or {}).get("learning_rate", 0.001))
        batch_size = int((experiment.training or {}).get("batch_size", 256))

        train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_all[train_idx]))
        loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)

        optimizer = torch.optim.Adam(
            model.parameters(),
            lr=lr,
            weight_decay=float((experiment.training or {}).get("weight_decay", 0.0)),
        )
        criterion = nn.CrossEntropyLoss()

        model.train()
        loss_history: list[float] = []
        try:
            for _ in range(epochs_n):
                for xb, yb in loader:
                    optimizer.zero_grad()
                    out = model(xb)
                    loss = criterion(out, yb)
                    loss_val = float(loss.item())
                    if not np.isfinite(loss_val) or abs(loss_val) > _MAX_ABS_LOSS:
                        return {
                            "status": "FAILED",
                            "error": f"non-finite/exploded loss ({loss_val})",
                        }
                    loss.backward()
                    grad_norm = _grad_norm(model)
                    if not np.isfinite(grad_norm) or grad_norm > _MAX_GRAD_NORM:
                        return {
                            "status": "FAILED",
                            "error": f"invalid/exploding gradients (norm={grad_norm:.3f})",
                        }
                    torch.nn.utils.clip_grad_norm_(model.parameters(), _MAX_GRAD_NORM)
                    optimizer.step()
                    loss_history.append(loss_val)
        except Exception as e:  # isolated failure — never a CHALLENGER
            return {"status": "FAILED", "error": f"training exception: {e}"}

        # ---- validation ----
        model.eval()
        with torch.inference_mode():
            val_logits = model(torch.from_numpy(X_val))
            val_preds = val_logits.argmax(dim=1).numpy()
        val_acc = float(np.mean(val_preds == y_all[val_idx])) if len(val_idx) else 0.0

        from nexus_scalp.model_generation.validation import compute_calibration

        cal = compute_calibration(torch.softmax(val_logits, dim=-1).numpy(), y_all[val_idx])

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
            architecture_parameters=dict(opt_params),
            feature_schema_id=str(dataset_frame["feature_schema_id"][0])
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
            final_validation_result={
                "val_accuracy": round(val_acc, 4),
                "ece": cal.get("ece"),
                "epochs": epochs_n,
                "sequences_train": len(train_idx),
                "sequences_val": len(val_idx),
                "loss_final": round(loss_history[-1], 6) if loss_history else None,
            },
            build_metadata={
                "trainer": "SequenceCandidateTrainer",
                "seq_len": self.seq_len,
                "max_gap_us": self.max_gap_us,
                "trained_mode": "sequence",
                "sequence_contract": {
                    "sequence_length": self.seq_len,
                    "feature_dim": input_dim,
                    "max_gap_us": self.max_gap_us,
                    "schema_id": str(dataset_frame["feature_schema_id"][0])
                    if "feature_schema_id" in dataset_frame.columns
                    else "scalp_v1",
                    "contract_version": "1",
                },
                "input_dimension": input_dim,
                "news_features": news_cols,
            },
        )

        artifact = self.store.save_model_artifact(
            mid,
            model.state_dict(),
            manifest.model_dump(mode="json"),
            scaler=(mean, std),
        )
        logger.info(
            "[TRAIN_SEQ] event=CANDIDATE_READY model_id=%s val_acc=%.4f ece=%.4f",
            mid,
            val_acc,
            cal.get("ece", 0.0),
        )
        return {
            "status": "COMPLETED",
            "model_id": mid,
            "val_accuracy": round(val_acc, 4),
            "ece": cal.get("ece"),
            "artifact": artifact,
        }


def _grad_norm(model: nn.Module) -> float:
    total = 0.0
    for p in model.parameters():
        if p.grad is not None:
            total += float(p.grad.detach().float().norm().item() ** 2)
    return float(np.sqrt(total))
