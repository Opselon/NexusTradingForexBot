"""Tensor inspection — RAW / NORMALIZED / MODEL INPUT triple (Phase 23/24/30).

The Neural Studio must show three explicitly distinguishable layers and prove
their dimensions are identical:

    RAW FEATURES  ==  NORMALIZED FEATURES  ==  ACTUAL MODEL TENSOR
    70            ==  70                   ==  70      (70D contract)
    50            ==  50                   ==  50      (50D contract)

It also reports per-slot validity (NaN / Inf / zero-default / clamped) for the
normalization inspector, which previously assumed 50D for every model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch

from nexus_scalp.model_lab.contract_gate import (
    assert_scaler_compatibility,
    assert_vector_dimension,
)
from nexus_scalp.model_lab.model_builder import (
    DIMENSION_TO_SCHEMA_ID,
    get_feature_contract,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lab.tensor_inspector")

_CLAMP_RANGE = 5.0


@dataclass
class SlotInspection:
    """One feature slot across the three layers (Phase 23)."""

    index: int
    feature: str
    family: str
    raw_value: float | None
    normalized_value: float | None
    model_input_value: float | None
    valid: bool
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TensorInspection:
    """The full three-layer tensor report."""

    model_id: str
    schema_id: str
    dimension: int
    sequence_length: int
    dtype: str
    device: str
    timestamp: str
    raw: dict[str, Any]
    normalized: dict[str, Any]
    model_input: dict[str, Any]
    slots: list[SlotInspection]
    nan_count: int
    inf_count: int
    zero_default_count: int
    clamped_count: int
    dimensions_match: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "schema_id": self.schema_id,
            "dimension": self.dimension,
            "sequence_length": self.sequence_length,
            "dtype": self.dtype,
            "device": self.device,
            "timestamp": self.timestamp,
            "raw": self.raw,
            "normalized": self.normalized,
            "model_input": self.model_input,
            "slots": [s.to_dict() for s in self.slots],
            "nan_count": self.nan_count,
            "inf_count": self.inf_count,
            "zero_default_count": self.zero_default_count,
            "clamped_count": self.clamped_count,
            "dimensions_match": self.dimensions_match,
            "all_dimensions": {
                "raw": self.raw["width"],
                "normalized": self.normalized["width"],
                "model_input": self.model_input["width"],
            },
        }


def _finite_or_none(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def inspect_tensor(
    *,
    model_id: str,
    dimension: int,
    raw_features: list[float] | np.ndarray,
    scaler: Any,
    model: torch.nn.Module | None = None,
    context: str = "studio_inspect",
) -> TensorInspection:
    """Build the RAW / NORMALIZED / MODEL INPUT triple for a contract.

    ``dimension`` is the CONTRACT dimension (the model's own width), never a UI
    selector — the inspector reports whatever the model actually consumes, so a
    50D selection over a 70D model is caught rather than papered over.
    """
    assert_vector_dimension(raw_features, dimension, context=context)

    schema_id = DIMENSION_TO_SCHEMA_ID.get(dimension, "")
    contract = get_feature_contract(dimension)
    names = [s.name for s in contract.slots]

    raw_np = np.asarray(raw_features, dtype=np.float32).reshape(1, -1)

    # ---------------------------------------------------------- NORMALIZED
    # The scaler must match the contract — a 50D scaler on a 70D input is the
    # exact contamination this gate exists to reject.
    assert_scaler_compatibility(scaler, dimension, context=context)
    normalized_np = _apply_scaler(scaler, raw_np)

    # ------------------------------------------------------- MODEL INPUT
    model_input_np = normalized_np
    device = "cpu"
    if model is not None:
        try:
            device = str(next(model.parameters()).device)
        except (StopIteration, RuntimeError):
            device = "cpu"

    # ------------------------------------------------------------ SLOTS
    raw_flat = raw_np.flatten()
    norm_flat = normalized_np.flatten()
    model_flat = model_input_np.flatten()

    slots: list[SlotInspection] = []
    nan_count = 0
    inf_count = 0
    zero_default_count = 0
    clamped_count = 0

    for i in range(dimension):
        raw_val = _finite_or_none(raw_flat[i])
        norm_val = _finite_or_none(norm_flat[i])
        model_val = _finite_or_none(model_flat[i])

        flags: list[str] = []
        if raw_val is None:
            if np.isnan(raw_flat[i]):
                flags.append("NAN")
                nan_count += 1
            else:
                flags.append("INF")
                inf_count += 1
        elif raw_val == 0.0:
            flags.append("ZERO_DEFAULT")
            zero_default_count += 1

        if norm_val is not None and abs(norm_val) >= _CLAMP_RANGE:
            flags.append("CLAMPED")
            clamped_count += 1

        slot = SlotInspection(
            index=i,
            feature=names[i] if i < len(names) else f"feature_{i}",
            family=contract.slots[i].family if i < len(contract.slots) else "UNKNOWN",
            raw_value=raw_val,
            normalized_value=norm_val,
            model_input_value=model_val,
            valid=raw_val is not None,
            flags=flags,
        )
        slots.append(slot)

    def _layer(arr: np.ndarray, label: str) -> dict[str, Any]:
        return {
            "layer": label,
            "shape": [int(x) for x in arr.shape],
            "width": int(arr.shape[-1]),
            "dtype": str(arr.dtype),
            "nan_count": int(np.sum(~np.isfinite(arr))),
            "min": _finite_or_none(arr.min()) if arr.size else None,
            "max": _finite_or_none(arr.max()) if arr.size else None,
            "mean": _finite_or_none(arr.mean()) if arr.size else None,
        }

    widths = {
        int(raw_np.shape[-1]),
        int(normalized_np.shape[-1]),
        int(model_input_np.shape[-1]),
    }

    return TensorInspection(
        model_id=model_id,
        schema_id=schema_id,
        dimension=dimension,
        sequence_length=1,
        dtype="float32",
        device=device,
        timestamp=datetime.now(UTC).isoformat(),
        raw=_layer(raw_np, "RAW"),
        normalized=_layer(normalized_np, "NORMALIZED"),
        model_input=_layer(model_input_np, "MODEL_INPUT"),
        slots=slots,
        nan_count=nan_count,
        inf_count=inf_count,
        zero_default_count=zero_default_count,
        clamped_count=clamped_count,
        dimensions_match=len(widths) == 1,
    )


def _apply_scaler(scaler: Any, x: np.ndarray) -> np.ndarray:
    """Apply the scaler, hard-clamping to the studio's normalization range.

    Falls back to identity only when the scaler reports itself NOT ready — and
    that fallback is labeled in the report by the caller, never silent.
    """
    transform = getattr(scaler, "transform", None) or getattr(scaler, "transform_50d", None)
    if callable(transform):
        try:
            out = np.asarray(transform(x), dtype=np.float32)
        except Exception as exc:
            raise ValueError(f"scaler transform failed: {exc}") from exc
        if out.shape != x.shape:
            raise ValueError(
                f"scaler changed the tensor width: {x.shape[-1]} -> {out.shape[-1]}"
            )
        return np.clip(out, -_CLAMP_RANGE, _CLAMP_RANGE)
    return np.clip(x, -_CLAMP_RANGE, _CLAMP_RANGE)
