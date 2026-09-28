"""Tensor inspector for the Position Adviser (mission §11/§12/§33).

Exposes the ACTUAL input the model is given, with the raw → normalized
transformation visible per feature. This is a read-only diagnostic surface:
it builds a tensor exactly as ``service.evaluate`` does, but never applies a
verdict and never touches the decide path.

Two sources are supported:

``live``    a currently open position, reached through the decide-system seam
            (``integration.build_position_state_for_adviser``) so the feature
            semantics are IDENTICAL to the ones the hot path produces.
``last``    the last sample the service evaluated (so the inspector is useful
            even when no position is open, and what it shows is what a real
            inference consumed — never a synthetic vector presented as live).

The inspector never fabricates a vector. When it has neither a live position
nor a last sample it reports ``source: "none"`` with an empty feature list.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import torch

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.position_adviser.features import (
    ADVISER_FEATURE_DIM,
    AdviserFeatureError,
    build_live_vector,
)
from nexus_scalp.position_adviser.service import PositionAdviserService

logger = get_logger("nexus_scalp.position_adviser.diagnostics")

#: The schema name the UI prints. Bump only when ADVISER_FEATURE_ORDER changes.
FEATURE_SCHEMA_NAME = "adviser_v1"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class FeatureRow:
    """One feature: raw value, normalized value, validity, and freshness."""

    index: int
    name: str
    raw: float
    normalized: float
    finite: bool
    age_sec: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "raw": round(self.raw, 8),
            "normalized": round(self.normalized, 8),
            "finite": bool(self.finite),
            "age_sec": round(self.age_sec, 6),
        }


@dataclass
class TensorInspection:
    """The complete tensor contract for one inference input."""

    model_id: str
    feature_schema: str
    feature_dimension: int
    sequence_length: int
    device: str
    dtype: str
    inference_timestamp: str
    source: str
    tensor_shape: list[int]
    features: list[FeatureRow] = field(default_factory=list)
    validity: dict[str, Any] = field(default_factory=dict)
    position: dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "OK" if not self.error else "UNAVAILABLE",
            "model_id": self.model_id,
            "feature_schema": self.feature_schema,
            "feature_dimension": self.feature_dimension,
            "sequence_length": self.sequence_length,
            "device": self.device,
            "dtype": self.dtype,
            "inference_timestamp": self.inference_timestamp,
            "source": self.source,
            "tensor_shape": list(self.tensor_shape),
            "features": [f.to_dict() for f in self.features],
            "validity": dict(self.validity),
            "position": self.position,
            "error": self.error,
        }


def _validity(raw: np.ndarray, normalized: np.ndarray, model_input_dim: int) -> dict[str, Any]:
    """Raw-vs-normalized-vs-model dimension and cleanliness checks (§12)."""
    raw_dim = int(raw.shape[-1]) if raw.size else 0
    norm_dim = int(normalized.shape[-1]) if normalized.size else 0
    nan_count = int(np.sum(~np.isfinite(raw))) + int(np.sum(~np.isfinite(normalized)))
    inf_count = int(np.sum(np.isinf(raw))) + int(np.sum(np.isinf(normalized)))
    # A "zero/default" value: exactly 0.0 in the RAW space. The scaler's clip
    # band can legitimately produce a saturated normalized value, so only the
    # raw space is authoritative for this count.
    zero_count = int(np.sum(raw == 0.0))
    return {
        "raw_dim": raw_dim,
        "normalized_dim": norm_dim,
        "model_input_dim": int(model_input_dim),
        "dims_match": bool(raw_dim == norm_dim == model_input_dim),
        "nan_count": nan_count,
        "inf_count": inf_count,
        "zero_default_count": zero_count,
        "raw_finite": bool(np.all(np.isfinite(raw))),
        "normalized_finite": bool(np.all(np.isfinite(normalized))),
        "saturated_count": int(np.sum(np.abs(normalized) >= 5.0)),
    }


def inspect_current_input(
    service: PositionAdviserService,
    *,
    live_position_state: dict[str, Any] | None = None,
) -> TensorInspection:
    """Build the inspection for the live position, else the last sample.

    ``live_position_state`` is the dict the decide loop already built through
    ``integration.build_position_state_for_adviser`` (same semantics). When it
    is absent the inspector falls back to the last sample the service saw.
    """
    with service._lock:
        model = service._state._model
        scaler = service._state._scaler
        model_id = service._state.model_id
        feature_dim = service._state.feature_dim
        last_state = dict(service._last_inspected_state or {})

    if model is None or scaler is None:
        return TensorInspection(
            model_id=model_id,
            feature_schema=FEATURE_SCHEMA_NAME,
            feature_dimension=feature_dim,
            sequence_length=1,
            device="cpu",
            dtype="float32",
            inference_timestamp=_utcnow_iso(),
            source="none",
            tensor_shape=[0, feature_dim],
            error="no adviser model loaded",
        )

    source = "none"
    state: dict[str, Any] = {}
    position_summary: dict[str, Any] | None = None
    if live_position_state is not None:
        source = "live_position"
        state = dict(live_position_state)
        position_summary = {
            k: state[k] for k in ("ticket", "snapshot_id", "snapshot_observed_at") if k in state
        }
    elif last_state:
        source = "last_sample"
        state = last_state

    if not state:
        return TensorInspection(
            model_id=model_id,
            feature_schema=FEATURE_SCHEMA_NAME,
            feature_dimension=feature_dim,
            sequence_length=1,
            device="cpu",
            dtype="float32",
            inference_timestamp=_utcnow_iso(),
            source="none",
            tensor_shape=[0, feature_dim],
            error="no live position and no prior sample",
        )

    try:
        raw_vec, names = build_live_vector(state)
    except AdviserFeatureError as exc:
        return TensorInspection(
            model_id=model_id,
            feature_schema=FEATURE_SCHEMA_NAME,
            feature_dimension=feature_dim,
            sequence_length=1,
            device="cpu",
            dtype="float32",
            inference_timestamp=_utcnow_iso(),
            source=source,
            tensor_shape=[0, feature_dim],
            position=position_summary,
            error=str(exc),
        )

    if len(names) != ADVISER_FEATURE_DIM or int(raw_vec.shape[0]) != ADVISER_FEATURE_DIM:
        return TensorInspection(
            model_id=model_id,
            feature_schema=FEATURE_SCHEMA_NAME,
            feature_dimension=feature_dim,
            sequence_length=1,
            device="cpu",
            dtype="float32",
            inference_timestamp=_utcnow_iso(),
            source=source,
            tensor_shape=[0, feature_dim],
            position=position_summary,
            error=f"feature contract mismatch: built {len(names)} names, "
            f"contract is {ADVISER_FEATURE_DIM}",
        )

    normalized = scaler.transform(raw_vec.reshape(1, -1))
    tensor = torch.tensor(normalized, dtype=torch.float32)
    now_mono = time.monotonic()
    observed_at = float(state.get("snapshot_observed_at") or now_mono)

    features: list[FeatureRow] = []
    for i, name in enumerate(names):
        raw_v = float(raw_vec[i])
        norm_v = float(normalized[0, i])
        features.append(
            FeatureRow(
                index=i,
                name=name,
                raw=raw_v,
                normalized=norm_v,
                finite=bool(np.isfinite(raw_v) and np.isfinite(norm_v)),
                age_sec=max(0.0, now_mono - observed_at),
            )
        )

    return TensorInspection(
        model_id=model_id,
        feature_schema=FEATURE_SCHEMA_NAME,
        feature_dimension=feature_dim,
        sequence_length=1,
        device=str(next(model.parameters()).device),
        dtype=str(tensor.dtype).replace("torch.", ""),
        inference_timestamp=_utcnow_iso(),
        source=source,
        tensor_shape=list(tensor.shape),
        features=features,
        validity=_validity(raw_vec, normalized, ADVISER_FEATURE_DIM),
        position=position_summary,
    )


__all__ = [
    "FEATURE_SCHEMA_NAME",
    "FeatureRow",
    "TensorInspection",
    "inspect_current_input",
]
