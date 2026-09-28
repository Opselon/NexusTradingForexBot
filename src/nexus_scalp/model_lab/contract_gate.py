"""50D / 70D contract separation (Phase 40 — cross-contamination audit).

The two model contracts must never contaminate each other:

    50D  scalp_v1     exactly 50 features
    70D  scalp_v3     exactly 70 features

Contamination is silent and lethal: a 70D model fed a 50D scaler normalizes only
the first 50 slots and leaves 20 raw; a 50D model fed a 70D vector either crashes
or, worse, gets a truncated tensor that still produces a confident prediction.

The observed studio path did exactly this — ``execute_predict`` zero-padded a
50D live vector to 70D with ``features + [0.0] * 20`` instead of refusing, and
``_StudioLoadedScaler.transform`` sliced ``mean[:x.shape[-1]]`` so a 50D scaler
silently "worked" on a 70D input.

This module is the single rejection gate. Every check raises
``ContractDimensionError`` with the two dimensions named, so the failure is
auditable instead of silent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.model_lab.model_builder import DIMENSION_TO_SCHEMA_ID, SUPPORTED_DIMENSIONS


class ContractDimensionError(ValueError):
    """A 50D/70D contract boundary was violated.

    Raised (never returned as a bool) so a caller cannot accidentally ignore a
    contamination event: the studio routes convert it to a 4xx JSON body.
    """


def _schema_id_for(dim: int) -> str:
    return DIMENSION_TO_SCHEMA_ID.get(dim, "")


def assert_vector_dimension(values: Any, expected_dim: int, context: str = "") -> None:
    """Reject a feature vector whose width does not match the contract.

    Truncation and padding are BOTH rejected — a 50-feature vector is not a
    70-feature vector with 20 missing, it is the wrong contract.
    """
    if expected_dim not in SUPPORTED_DIMENSIONS:
        raise ContractDimensionError(
            f"contract {context or 'unknown'} declares unsupported dimension "
            f"{expected_dim!r}; supported contracts are {SUPPORTED_DIMENSIONS}"
        )
    try:
        width = len(values)
    except TypeError as exc:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: feature payload is not a sequence "
            f"(got {type(values).__name__})"
        ) from exc

    if width != expected_dim:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: expected exactly {expected_dim} features "
            f"({DIMENSION_TO_SCHEMA_ID[expected_dim]}), got {width}. "
            f"50D and 70D are separate contracts — no truncation, no padding."
        )


def assert_tensor_dimension(tensor: Any, expected_dim: int, context: str = "") -> None:
    """Reject a torch/numpy tensor whose LAST axis is not the contract width."""
    if expected_dim not in SUPPORTED_DIMENSIONS:
        raise ContractDimensionError(
            f"contract {context or 'unknown'} declares unsupported dimension "
            f"{expected_dim!r}"
        )
    shape = getattr(tensor, "shape", None)
    if shape is None:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: payload has no shape attribute"
        )
    if len(shape) == 0:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: payload is a scalar, expected a "
            f"{expected_dim}-width tensor"
        )
    if int(shape[-1]) != expected_dim:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: tensor last-axis width is "
            f"{int(shape[-1])}, contract requires exactly {expected_dim} "
            f"({_schema_id_for(expected_dim)}). No truncation, no padding."
        )


def assert_scaler_compatibility(scaler: Any, model_dim: int, context: str = "") -> None:
    """Reject a scaler whose fitted width does not match the model contract.

    This is the exact defect the old ``_StudioLoadedScaler.transform`` permitted:
    it sliced ``mean[:x.shape[-1]]``, so a 50D scaler silently accepted a 70D
    input and left 20 features unnormalized.
    """
    if model_dim not in SUPPORTED_DIMENSIONS:
        raise ContractDimensionError(
            f"contract {context or 'unknown'} declares unsupported model dimension "
            f"{model_dim!r}"
        )

    width = _scaler_width(scaler)
    if width is None:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: scaler carries no measurable width "
            f"(no mean/std/dimension attribute)"
        )
    if width != model_dim:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: scaler is fitted for {width} features "
            f"but the model contract is {model_dim} "
            f"({_schema_id_for(model_dim)}). A {width}D scaler cannot normalize a "
            f"{model_dim}D tensor."
        )


def assert_scaler_file_compatibility(
    scaler_path: Path, model_dim: int, context: str = ""
) -> None:
    """Reject a .scaler.npz whose declared/fitted width mismatches the contract."""
    if model_dim not in SUPPORTED_DIMENSIONS:
        raise ContractDimensionError(
            f"contract {context or 'unknown'} declares unsupported dimension "
            f"{model_dim!r}"
        )
    if not scaler_path.is_file():
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: scaler sidecar missing at "
            f"{scaler_path.name}"
        )
    data = np.load(scaler_path)
    declared = data.get("dimension")
    mean = data.get("mean")
    width: int | None = None
    if declared is not None:
        try:
            width = int(declared)
        except (TypeError, ValueError):
            width = None
    if width is None and mean is not None:
        width = int(np.asarray(mean).shape[-1]) if np.asarray(mean).shape else None
    if width is None:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: scaler {scaler_path.name} declares no "
            f"dimension and carries no mean vector"
        )
    if width != model_dim:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: scaler {scaler_path.name} is "
            f"{width}D, model contract is {model_dim} "
            f"({_schema_id_for(model_dim)})"
        )


def assert_schema_dimension(schema_id: str, expected_dim: int, context: str = "") -> None:
    """Reject a schema id that does not bind to the declared dimension."""
    if expected_dim not in SUPPORTED_DIMENSIONS:
        raise ContractDimensionError(
            f"contract {context or 'unknown'} declares unsupported dimension "
            f"{expected_dim!r}"
        )
    if schema_id != _schema_id_for(expected_dim):
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: schema_id {schema_id!r} does not bind "
            f"to dimension {expected_dim} "
            f"(that dimension is {_schema_id_for(expected_dim)!r})"
        )
    schema = FEATURE_SCHEMAS.resolve(schema_id)
    if schema.dimension != expected_dim:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: schema {schema_id!r} declares "
            f"dimension {schema.dimension}, contract requires {expected_dim}"
        )


def assert_model_weights_dimension(weights_path: Path, expected_dim: int, context: str = "") -> None:
    """Reject a checkpoint whose input projection width is the wrong contract."""
    if expected_dim not in SUPPORTED_DIMENSIONS:
        raise ContractDimensionError(
            f"contract {context or 'unknown'} declares unsupported dimension "
            f"{expected_dim!r}"
        )
    if not weights_path.is_file():
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: weights missing at {weights_path.name}"
        )
    import torch

    weights = torch.load(weights_path, map_location="cpu", weights_only=True)
    proj = weights.get("input_projection.weight")
    if not isinstance(proj, torch.Tensor):
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: {weights_path.name} has no "
            f"input_projection.weight to read the contract width from"
        )
    width = int(proj.shape[1])
    if width != expected_dim:
        raise ContractDimensionError(
            f"contract {context or 'unknown'}: {weights_path.name} input projection "
            f"expects {width} features, contract requires exactly {expected_dim} "
            f"({_schema_id_for(expected_dim)})"
        )


def _scaler_width(scaler: Any) -> int | None:
    for attr in ("dimension", "dim", "num_features", "n_features_in_"):
        candidate = getattr(scaler, attr, None)
        if isinstance(candidate, int) and candidate > 0:
            return candidate
        if isinstance(candidate, np.integer):
            return int(candidate)
    for attr in ("mean", "std", "mu_", "scale_"):
        arr = getattr(scaler, attr, None)
        if arr is not None:
            shape = np.asarray(arr).shape
            if shape:
                return int(shape[-1])
    fn = getattr(scaler, "dimension", None)
    if callable(fn):
        try:
            out = fn()
            if isinstance(out, int) and out > 0:
                return out
        except Exception:
            return None
    return None
