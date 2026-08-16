"""Local Model Runtime (PHASE 13, spec 30 / 40).

INVARIANT: model inference MUST NOT require database access.

    filesystem artifact
        -> manifest validation
        -> local model runtime
        -> prediction

The DB is history/telemetry/registry — never a runtime dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.model_factory import ModelFactory
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.runtime")


class ManifestValidationError(ValueError):
    """Raised when a model artifact fails manifest/schema/integrity checks."""


class LocalModelRuntime:
    """Loads + validates + predicts from a filesystem-only model artifact.

    No DB import. No registry dependency. A missing/corrupted artifact is a
    hard failure (never silently falls back to a random model).
    """

    def __init__(
        self,
        store: ArtifactStore | None = None,
        model_factory: ModelFactory | None = None,
        device: str | None = None,
    ) -> None:
        self.store = store or ArtifactStore()
        self.model_factory = model_factory or ModelFactory()
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model: torch.nn.Module | None = None
        self._manifest: dict[str, Any] | None = None
        self._scaler: tuple[np.ndarray, np.ndarray] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self, model_id: str) -> LocalModelRuntime:
        """Loads + validates a model artifact. Raises on any integrity
        failure — a corrupted artifact is NEVER silently loaded."""
        manifest = self.store.read_model_manifest(model_id)
        if not manifest:
            raise ManifestValidationError(f"model {model_id}: manifest missing")

        weights_path = self.store.model_weights_path(model_id)
        if not weights_path.exists():
            raise ManifestValidationError(f"model {model_id}: weights missing")

        # integrity: artifact hash must match the manifest
        from nexus_scalp.model_generation.artifact_store import sha256_file

        current = sha256_file(weights_path)
        stored = manifest.get("artifact_hash", "")
        if stored and current != stored:
            raise ManifestValidationError(
                f"model {model_id}: artifact hash mismatch (corrupted artifact)"
            )

        # schema: feature dimension must match the declared schema.
        # The NEURAL input width may exceed the base feature dim when news
        # features were enabled during training (build_metadata carries the
        # exact width used).
        dim = int(manifest.get("feature_dimension", 0))
        metadata = manifest.get("build_metadata", {}) or {}
        input_dim = int(metadata.get("input_dimension", dim) or dim)
        if input_dim < dim:
            # an input narrower than the declared schema is impossible:
            # news can only ADD dims. Corrupted manifest -> refuse.
            raise ManifestValidationError(
                f"model {model_id}: input_dimension {input_dim} < "
                f"feature_dimension {dim} (corrupted schema metadata)"
            )
        if not manifest.get("news_enabled", False) and input_dim != dim:
            # no news features were declared, yet the neural input width
            # differs from the declared schema -> inconsistent manifest.
            raise ManifestValidationError(
                f"model {model_id}: news disabled but input_dimension "
                f"{input_dim} != feature_dimension {dim}"
            )
        arch = str(manifest.get("architecture_id", "LEGACY_SCALPNET_V1"))
        num_classes = int(manifest.get("class_count", 3))

        model = self.model_factory.build(
            architecture=arch,
            num_classes=num_classes,
            parameters={
                "input_dim": input_dim,
                **(manifest.get("architecture_parameters", {}) or {}),
            },
        )
        try:
            state = torch.load(weights_path, map_location=self._device, weights_only=False)
            model.load_state_dict(state)
        except Exception as e:
            raise ManifestValidationError(f"model {model_id}: state_dict load failed: {e}") from e

        model.to(self._device)
        model.eval()
        self._model = model
        self._manifest = manifest
        self._scaler = self.store.read_scaler(model_id)
        logger.info("[MODEL] event=LOADED model_id=%s device=%s", model_id, self._device)
        return self

    def unload(self) -> None:
        self._model = None
        self._manifest = None
        self._scaler = None

    # ------------------------------------------------------------------
    # Metadata / health
    # ------------------------------------------------------------------

    def metadata(self) -> dict[str, Any]:
        if self._manifest is None:
            raise ManifestValidationError("runtime: no model loaded")
        return dict(self._manifest)

    def health(self) -> dict[str, Any]:
        return {
            "loaded": self._model is not None,
            "device": self._device,
            "model_id": (self._manifest or {}).get("model_id", ""),
            "feature_dimension": (self._manifest or {}).get("feature_dimension", 0),
            "class_count": (self._manifest or {}).get("class_count", 0),
        }

    # ------------------------------------------------------------------
    # Prediction (NO DB access)
    # ------------------------------------------------------------------

    def predict(self, feature_vector: list[float] | np.ndarray) -> dict[str, Any]:
        """Single-vector prediction. Returns class probabilities + argmax."""
        if self._model is None or self._manifest is None:
            raise ManifestValidationError("runtime: no model loaded")

        vec = np.asarray(feature_vector, dtype=np.float32).reshape(1, -1)
        metadata = self._manifest.get("build_metadata", {}) or {}
        base_dim = int(self._manifest.get("feature_dimension", 0))
        expected = int(metadata.get("input_dimension", base_dim) or base_dim)
        if vec.shape[1] != expected:
            raise ManifestValidationError(
                f"predict: expected {expected} inputs (schema {base_dim} + news), "
                f"got {vec.shape[1]}"
            )

        if self._scaler is not None:
            mean, std = self._scaler
            vec = (vec - mean.reshape(1, -1)) / (std.reshape(1, -1) + 1e-8)

        with torch.inference_mode():
            x = torch.from_numpy(vec).to(self._device)
            logits = self._model(x)
            probs = torch.softmax(logits, dim=-1).cpu().numpy()[0]

        return {
            "probabilities": [float(p) for p in probs],
            "argmax": int(np.argmax(probs)),
            "label": self._decode(int(np.argmax(probs))),
        }

    def _decode(self, value: int) -> str:
        classes = (self._manifest or {}).get("classes", [])
        if 0 <= value < len(classes):
            return str(classes[value])
        return str(value)


def validate_and_load(
    model_id: str, root: Path | str = "artifacts/model_generation"
) -> LocalModelRuntime:
    """Convenience: load + validate in one call (used by CLI)."""
    rt = LocalModelRuntime(store=ArtifactStore(root))
    return rt.load(model_id)
