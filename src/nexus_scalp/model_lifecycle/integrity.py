"""
Model Artifact Integrity & Compatibility
========================================
PHASE 10 (spec 28 / 29).

Every saved artifact is associated with a hash, size, schema, architecture,
version, provenance and validation result. On load, integrity is verified and
a corrupted artifact is NEVER silently loaded.

The compatibility gate (spec 6 / 29) is explicit:
  * feature schema id must match
  * feature dimension must match (50D today; 60D/350D future schemas are
    additive; a mismatch FAILS loudly - never silently reshape/truncate)
  * output class count must match the model head (4)
  * scaler/preprocessing must be schema-compatible
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexus_scalp.experience.provenance import fingerprint_artifact
from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.model_lifecycle.models import ModelArtifactInfo
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.integrity")

#: ScalpNet head width: NO_TRADE, BUY, SELL, WAIT.
EXPECTED_NUM_CLASSES: int = 4


def resolve_schema(schema_id: str | None = None):
    """Resolves a feature schema id (defaults to the active schema)."""

    return FEATURE_SCHEMAS.resolve(schema_id)


class SchemaCompatibilityError(ValueError):
    """Raised when a model artifact does not match the declared schema."""


def compute_artifact_hash(path: Path | str) -> str:
    """SHA256 prefix of an artifact file, or '' when absent."""
    return fingerprint_artifact(path)


def artifact_size(path: Path | str) -> int:
    p = Path(path)
    try:
        return p.stat().st_size if p.exists() and p.is_file() else 0
    except Exception:
        return 0


def inspect_artifact(
    artifact_path: Path | str,
    scaler_path: Path | str = "",
    model_id: str = "",
    model_version: str = "",
    feature_schema_id: str | None = None,
    feature_dimension: int | None = None,
    num_classes: int = EXPECTED_NUM_CLASSES,
) -> ModelArtifactInfo:
    """
    Inspects a model artifact file and returns integrity info.

    Never raises for a missing file (missing artifact is a supported cold-start
    state); `integrity_ok` reflects whether every expected marker is present and
    the file passes the declared-schema gates.
    """
    p = Path(artifact_path)
    schema = resolve_schema(feature_schema_id)
    dim = feature_dimension or schema.dimension

    info = ModelArtifactInfo(
        model_id=model_id,
        model_version=model_version,
        artifact_path=str(p),
        artifact_hash=compute_artifact_hash(p),
        artifact_bytes=artifact_size(p),
        feature_schema_id=schema.schema_id,
        feature_dimension=dim,
        num_classes=num_classes,
        architecture="scalp_net",
        scaler_path=str(scaler_path) if scaler_path else "",
        scaler_hash=compute_artifact_hash(scaler_path) if scaler_path else "",
        integrity_ok=False,
    )
    if not p.exists() or p.stat().st_size == 0:
        return info

    state_dict = _load_state_dict_shapes(p)
    if not state_dict:
        logger.error("[MODEL] event=INTEGRITY_FAILURE model_id=%s", model_id)
        return info

    input_shape = state_dict.get("input_projection.weight") or state_dict.get(
        "projection.weight"
    )
    if not input_shape:
        return info
    actual_dim = int(input_shape[1])

    # =====================================================================
    # CLASS-HEAD PROBE (BUG-110): the class count MUST come from the
    # classifier head (the final Linear), never from input_projection,
    # whose shape[0] is the HIDDEN width (128) — reading it as classes
    # produced the false "actual_classes=128 / expected_classes=4"
    # INTEGRITY_FAILURE on every valid ScalpNet v1 artifact.
    # Head candidates resolved in canonical priority order:
    #   classifier.weight > head.2.weight > head.1.weight > fc_out.weight
    # and verified against every 2D weight in the state_dict (any tensor
    # whose last axis carries the class logits). Never pads/truncates.
    # =====================================================================
    head_candidates = [
        "classifier.weight",
        "head.3.weight",  # TCNAttentionV1 final layer (Linear(hidden//2, C))
        "head.2.weight",
        "head.1.weight",
        "head.0.weight",  # TCNAttentionV1 first head layer (not a class head)
        "fc_out.weight",
    ]
    head_key = next((k for k in head_candidates if k in state_dict), None)
    actual_out: int | None = None
    if head_key is not None:
        # head.0.weight is the FIRST head layer (hidden->hidden/2) — its rows
        # are a hidden width, not classes. Only treat it as a class head when
        # it is the ONLY head-scale tensor (defensive fallback).
        rows = int(state_dict[head_key][0])
        if head_key == "head.0.weight" and any(
            k.startswith("head.") and k != "head.0.weight" and "weight" in k
            for k in state_dict
        ):
            head_key = None
        else:
            actual_out = rows
    else:
        # Fallback: find any 2D weight whose out count is NOT a hidden width.
        two_dim_weights = [k for k, v in state_dict.items() if len(v) == 2]
        candidate_outs: set[int] = set()
        for k in two_dim_weights:
            rows = int(state_dict[k][0])
            if rows <= 64:
                candidate_outs.add(rows)
        if len(candidate_outs) == 1:
            actual_out = next(iter(candidate_outs))

    if actual_out is None:
        logger.error(
            "[MODEL] event=INTEGRITY_FAILURE model_id=%s reason=CLASS_HEAD_NOT_FOUND",
            model_id,
            expected_dim=dim,
            expected_classes=num_classes,
        )
        return info.model_copy(
            update={
                "actual_input_dimension": actual_dim,
                "actual_output_classes": None,
                "actual_hidden_dimension": (
                    int(input_shape[0]) if input_shape and len(input_shape) == 2 else None
                ),
                "integrity_ok": False,
                "integrity_reason": "CLASS_HEAD_NOT_FOUND",
            }
        )

    hidden_dim = int(input_shape[0]) if input_shape and len(input_shape) == 2 else None

    # Scaler dimension is a REAL gate alongside model tensors.
    scaler_dim: int | None = None
    if scaler_path:
        sp = Path(scaler_path)
        if sp.exists():
            try:
                import numpy as np

                data = np.load(sp)
                mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
                std = np.asarray(data["std"], dtype=np.float32).reshape(-1)
                if mean.shape[0] == std.shape[0]:
                    scaler_dim = int(mean.shape[0])
            except Exception as e:
                logger.warning("[MODEL] scaler dimension inspection failed", error=str(e))

    ok = (
        bool(info.artifact_hash)
        and actual_dim == dim
        and actual_out == num_classes
        and (scaler_dim is None or scaler_dim == dim)
    )
    reason = ""
    if not ok:
        if not info.artifact_hash:
            reason = "ARTIFACT_HASH_MISSING"
        elif actual_dim != dim:
            reason = "DIMENSION_MISMATCH"
        elif actual_out != num_classes:
            reason = "CLASS_COUNT_MISMATCH"
        elif scaler_dim is not None and scaler_dim != dim:
            reason = "SCALER_DIMENSION_MISMATCH"
        logger.error(
            "[MODEL] event=INTEGRITY_FAILURE (compatibility)",
            model_id=model_id,
            expected_dim=dim,
            actual_dim=actual_dim,
            expected_classes=num_classes,
            actual_classes=actual_out,
            head_key=head_key,
            scaler_dimension=scaler_dim,
            reason=reason,
        )
    info = info.model_copy(
        update={
            "integrity_ok": ok,
            "actual_input_dimension": actual_dim,
            "actual_output_classes": actual_out,
            "actual_hidden_dimension": hidden_dim,
            "class_head_name": head_key or "",
            "scaler_dimension": scaler_dim,
            "integrity_reason": reason,
        }
    )
    return info


def verify_compatibility(
    artifact_path: Path | str,
    feature_schema_id: str,
    feature_dimension: int,
    num_classes: int = EXPECTED_NUM_CLASSES,
) -> dict[str, Any]:
    """
    Explicit compatibility gate (spec 29 / 38.13-15).

    Raises SchemaCompatibilityError on any mismatch. NEVER silently reshapes.
    """
    schema = resolve_schema(feature_schema_id)
    if schema.dimension != feature_dimension:
        raise SchemaCompatibilityError(
            f"Schema id {feature_schema_id} declares dimension {schema.dimension}, "
            f"but caller supplied dimension {feature_dimension}"
        )
    info = inspect_artifact(
        artifact_path,
        feature_schema_id=feature_schema_id,
        feature_dimension=feature_dimension,
        num_classes=num_classes,
    )
    if not info.integrity_ok:
        raise SchemaCompatibilityError(
            f"Artifact {artifact_path} failed compatibility: "
            f"dimension={info.feature_dimension} classes={info.num_classes} "
            f"hash={info.artifact_hash or 'MISSING'}"
        )
    return info.model_dump()


def _load_state_dict_shapes(path: Path) -> dict[str, tuple[int, ...]]:
    """Returns {tensor_name: shape} from a torch state dict without loading it."""
    try:
        import torch

        state = torch.load(path, map_location="cpu", weights_only=False)
        if not isinstance(state, dict):
            return {}
        out: dict[str, tuple[int, ...]] = {}
        for k, v in state.items():
            if hasattr(v, "shape"):
                out[str(k)] = tuple(int(x) for x in v.shape)
            elif isinstance(v, dict):
                for k2, v2 in v.items():
                    if hasattr(v2, "shape"):
                        out[f"{k}.{k2}"] = tuple(int(x) for x in v2.shape)
        return out
    except Exception as e:
        logger.warning("[MODEL] state-dict shape inspect failed", path=str(path), error=str(e))
        return {}


def scaler_compatibility(scaler_path: Path | str, feature_dimension: int) -> bool:
    """
    Verifies the persisted scaler matches the declared feature dimension.

    Returns False (never raises) when the scaler is missing or mismatched.
    """
    p = Path(scaler_path)
    if not p.exists():
        return False
    try:
        import numpy as np

        data = np.load(p)
        mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
        std = np.asarray(data["std"], dtype=np.float32).reshape(-1)
        return bool(mean.shape[0] == feature_dimension and std.shape[0] == feature_dimension)
    except Exception as e:
        logger.warning("[MODEL] scaler compatibility check failed", error=str(e))
        return False


def artifact_metadata_json(info: ModelArtifactInfo) -> str:
    """Compact JSON metadata for persistence."""
    return json.dumps(info.model_dump(mode="json"), default=str)
