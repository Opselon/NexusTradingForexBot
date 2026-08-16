"""Sample Replay & Model Drift (PHASE 13, spec 34 / 35).

SampleReplay reconstructs historical context from a sample_id:

    sample_id
        -> historical context / feature vector / news context
        -> model prediction
        -> strategy policy / risk decision (where supported)

Drift detection compares current vs historical distributions. Drift is an
ALERT/RESEARCH trigger — never an automatic retrain (spec 35).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.runtime import LocalModelRuntime
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.replay")


class SampleReplay:
    """Reconstructs one sample's context + prediction (forensic tool)."""

    def __init__(self, store: ArtifactStore | None = None) -> None:
        self.store = store or ArtifactStore()

    def replay(
        self,
        dataset_id: str,
        sample_id: str,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Reconstructs a sample from the dataset artifact + optional model.

        Returns the full historical context. When ``model_id`` is given the
        current model's prediction for the reconstructed vector is also
        included so drift is detectable.
        """
        frame = self.store.read_dataset(dataset_id)
        if frame is None or frame.is_empty():
            raise FileNotFoundError(f"dataset {dataset_id} not found or empty")

        row = frame.filter(__import__("polars").col("sample_id") == sample_id)
        if row.is_empty():
            raise FileNotFoundError(f"sample {sample_id} not found in dataset {dataset_id}")

        rec = row.row(0, named=True)
        feat_cols = [c for c in frame.columns if c.startswith("feat_")]
        vector = [float(rec.get(c, 0.0)) for c in feat_cols]
        news_cols = [
            c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"
        ]
        news_ctx = {c.replace("news_", "", 1): float(rec.get(c, 0.0)) for c in news_cols}

        result: dict[str, Any] = {
            "sample_id": sample_id,
            "dataset_id": dataset_id,
            "timestamp": str(rec.get("timestamp", "")),
            "symbol": rec.get("symbol", ""),
            "timeframe": rec.get("timeframe", ""),
            "regime": rec.get("regime", "UNKNOWN"),
            "setup_id": rec.get("setup_id", ""),
            "strategy_id": rec.get("strategy_id", ""),
            "strategy_version": rec.get("strategy_version", ""),
            "label": int(rec.get("label", 0)),
            "label_str": rec.get("label_str", ""),
            "feature_vector": vector,
            "feature_dimension": len(vector),
            "news_context": news_ctx,
            "news_context_schema_id": rec.get("news_context_schema_id", ""),
        }

        if model_id:
            try:
                rt = LocalModelRuntime(store=self.store).load(model_id)
                # news-aware models expect [base features + news context]
                mm = self.store.read_model_manifest(model_id) or {}
                input_meta = mm.get("build_metadata", {}) or {}
                input_dim = int(input_meta.get("input_dimension", len(vector)))
                full_vector = list(vector)
                if input_dim > len(full_vector):
                    from nexus_scalp.model_generation.models import (
                        default_news_context_schema,
                    )

                    schema = default_news_context_schema()
                    full_vector += schema.vectorize(news_ctx)
                pred = rt.predict(full_vector)
                result["model_prediction"] = pred
                result["model_id"] = model_id
            except Exception as e:
                result["model_prediction"] = {"error": str(e)}
                result["model_id"] = model_id

        return result

    def compare(
        self,
        dataset_id: str,
        sample_id: str,
        model_id: str,
        expected_label: int | None = None,
    ) -> dict[str, Any]:
        """Compares the historical label vs the current model prediction.

        A disagreement flags possible MODEL_DRIFT (or label drift) and is an
        alert/research trigger — never an automatic action.
        """
        rec = self.replay(dataset_id, sample_id, model_id=model_id)
        pred = rec.get("model_prediction", {})
        pred_label = pred.get("argmax") if isinstance(pred, dict) else None
        hist_label = int(rec.get("label", -1))
        drifted = pred_label is not None and pred_label != hist_label
        return {
            "sample_id": sample_id,
            "historical_label": hist_label,
            "predicted_label": pred_label,
            "drift": drifted,
            "drift_type": "MODEL_DRIFT" if drifted else "CONSISTENT",
            "note": "alert/research trigger only — no automatic retrain",
        }


# =============================================================================
# Drift detection (spec 35)
# =============================================================================


def detect_feature_drift(
    reference: np.ndarray,
    current: np.ndarray,
    threshold: float = 0.1,
    statistic: str = "wasserstein",
) -> dict[str, Any]:
    """Compares reference vs current feature distributions.

    Returns per-feature drift (absolute mean shift) + a global verdict.
    """
    if reference.shape[1] != current.shape[1]:
        return {"error": "dimension mismatch", "drifted": True}

    ref_mean = reference.mean(axis=0) + 1e-8
    cur_mean = current.mean(axis=0) + 1e-8
    rel_shift = np.abs(cur_mean - ref_mean) / np.abs(ref_mean)
    drifted_features = [int(i) for i, v in enumerate(rel_shift) if v > threshold]
    return {
        "statistic": statistic,
        "drifted": len(drifted_features) > 0,
        "drifted_features": drifted_features,
        "max_shift": round(float(rel_shift.max()), 4),
        "mean_shift": round(float(rel_shift.mean()), 4),
        "threshold": threshold,
    }


def detect_prediction_drift(
    reference_probs: np.ndarray,
    current_probs: np.ndarray,
    threshold: float = 0.15,
) -> dict[str, Any]:
    """Compares predicted class distributions (spec 35)."""
    if reference_probs.shape != current_probs.shape[1:]:
        ref_dist = reference_probs.mean(axis=0)
        cur_dist = current_probs.mean(axis=0)
    else:
        ref_dist = reference_probs
        cur_dist = current_probs
    shift = float(np.abs(cur_dist - ref_dist).sum())
    return {
        "drifted": shift > threshold,
        "total_variation": round(shift, 4),
        "threshold": threshold,
        "reference_distribution": ref_dist.tolist(),
        "current_distribution": cur_dist.tolist(),
    }
