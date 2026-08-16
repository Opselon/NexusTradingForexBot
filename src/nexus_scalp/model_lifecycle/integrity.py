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

    input_shape = state_dict.get("input_projection.weight")
    if not input_shape:
        return info
    actual_dim = int(input_shape[1])
    actual_out = int(input_shape[0])

    ok = actual_dim == dim and actual_out == num_classes and bool(info.artifact_hash)
    if not ok:
        logger.error(
            "[MODEL] event=INTEGRITY_FAILURE (compatibility)",
            model_id=model_id,
            expected_dim=dim,
            actual_dim=actual_dim,
            expected_classes=num_classes,
            actual_classes=actual_out,
        )
    info = info.model_copy(update={"integrity_ok": ok})
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
