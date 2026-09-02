"""
Challenger Runtime (Shadow-Only)
================================
PHASE 11 the SHADOW-ONLY execution layer (spec 2 / 18 / 19).

The ChallengerRuntime:
  * loads a validated Challenger artifact + scaler with full integrity checks
    (hash, schema, dimension, class count) - an invalid artifact is
    SHADOW_LOAD_FAILED, never silently reshaped;
  * runs inference on the SAME feature vector the Champion saw (same-input
    guarantee, spec 3 / 4);
  * produces a hypothetical proposal ONLY - it holds no adapter, no order
    manager and no risk engine, and has ZERO order authority;
  * NEVER modifies Champion state.

Schema-safety: a Challenger trained under scalp_v2/60D presented with a
scalp_v1/50D live vector is rejected (INVALID_COMPARISON), never padded or
truncated (spec 26).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.model_lifecycle.integrity import (
    SchemaCompatibilityError,
    inspect_artifact,
    scaler_compatibility,
)
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.models import ShadowModelRef

logger = get_logger("nexus_scalp.shadow.challenger")


def resolve_schema(schema_id: str | None = None):
    """Resolves a feature schema id (defaults to the active schema)."""
    return FEATURE_SCHEMAS.resolve(schema_id)


class ChallengerLoadError(RuntimeError):
    """Raised when a Challenger artifact cannot be safely loaded."""


class ChallengerRuntime:
    """
    Shadow-only model runtime.

    Attributes:
        ref: identity + integrity of the loaded Challenger.
        model / scaler: the loaded artifact (never the Champion's).
        live_schema_id / live_dimension: the CURRENT production schema the
            shadow must be compatible with.
    """

    def __init__(
        self,
        artifact_path: Path | str,
        scaler_path: Path | str,
        model_id: str,
        model_version: str,
        live_schema_id: str,
        live_dimension: int,
        num_classes: int = 4,
    ) -> None:
        self.artifact_path = Path(artifact_path)
        self.scaler_path = Path(scaler_path)
        self.model_id = model_id
        self.model_version = model_version
        self.live_schema_id = live_schema_id
        self.live_dimension = int(live_dimension)
        self.num_classes = int(num_classes)
        self.model: ScalpNet | None = None
        self.scaler_mean: Any = None
        self.scaler_std: Any = None
        self.ref: ShadowModelRef | None = None
        self._load()

    # ------------------------------------------------------------------
    # Loading & integrity
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Loads + verifies the Challenger; raises ChallengerLoadError on any."""
        info = inspect_artifact(
            self.artifact_path,
            self.scaler_path,
            model_id=self.model_id,
            model_version=self.model_version,
        )
        if not info.integrity_ok or not info.artifact_hash:
            logger.error(
                "[CHALLENGER] event=LOAD_FAILED",
                reason="artifact integrity failed",
                path=str(self.artifact_path),
                hash=info.artifact_hash or "MISSING",
            )
            raise ChallengerLoadError(
                f"Challenger artifact integrity failed: {self.artifact_path} "
                f"hash={info.artifact_hash or 'MISSING'}"
            )
        if info.feature_dimension != self.live_dimension:
            logger.error(
                "[CHALLENGER] event=SCHEMA_MISMATCH",
                challenger_dim=info.feature_dimension,
                live_dim=self.live_dimension,
            )
            raise ChallengerLoadError(
                f"Challenger schema {info.feature_schema_id}/{info.feature_dimension}D "
                f"incompatible with live {self.live_schema_id}/{self.live_dimension}D"
            )
        if info.num_classes != self.num_classes:
            raise ChallengerLoadError(
                f"Challenger class count {info.num_classes} != expected {self.num_classes}"
            )
        if not scaler_compatibility(self.scaler_path, self.live_dimension):
            logger.error("[CHALLENGER] event=SCALER_MISMATCH")
            raise ChallengerLoadError(f"Challenger scaler incompatible with {self.live_dimension}D")

        try:
            import numpy as np
            import torch

            state = torch.load(self.artifact_path, map_location="cpu", weights_only=False)
            model = ScalpNet(num_features=self.live_dimension, num_classes=self.num_classes)
            model.load_state_dict(state)
            model.eval()
            data = np.load(self.scaler_path)
            self.scaler_mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
            self.scaler_std = np.asarray(data["std"], dtype=np.float32).reshape(-1)
            self.model = model
        except Exception as e:
            logger.error("[CHALLENGER] event=LOAD_FAILED", reason=str(e))
            raise ChallengerLoadError(f"Challenger load failed: {e}") from e

        self.ref = ShadowModelRef(
            model_id=self.model_id,
            model_version=self.model_version,
            feature_schema_id=info.feature_schema_id,
            feature_dimension=info.feature_dimension,
            artifact_hash=info.artifact_hash,
            is_champion=False,
        )
        logger.info(
            "[CHALLENGER] event=LOADED",
            model_id=self.model_id,
            version=self.model_version,
            hash=info.artifact_hash,
            schema=f"{info.feature_schema_id}/{info.feature_dimension}D",
        )

    # ------------------------------------------------------------------
    # Shadow-only inference
    # ------------------------------------------------------------------

    def infer(self, x50: list[float]) -> dict[str, Any]:
        """
        Runs shadow inference on the SAME feature vector the Champion used.

        Returns probabilities + predicted action + confidence. This is pure
        computation: the result is a hypothetical proposal, never an order.
        """
        if self.model is None or self.scaler_mean is None:
            raise ChallengerLoadError("Challenger not loaded")
        if len(x50) != self.live_dimension:
            raise SchemaCompatibilityError(
                f"Shadow input dimension {len(x50)} != live schema {self.live_dimension}D"
            )
        import numpy as np
        import torch
        from torch import nn

        from nexus_scalp.shadow.compat import scale_like_champion

        x_np = np.asarray(x50, dtype=np.float32).reshape(1, -1)
        # CHG-0046 D6: champion-identical transform — (x-mean)/std with the
        # trainer's 1e-3 std floor, clipped to [-5,+5]. The previous
        # (x-mean)/(std+1e-8) UNCLIPPED variant evaluated the challenger
        # under a transform it was never trained with.
        x_scaled = scale_like_champion(x_np, self.scaler_mean, self.scaler_std)
        x_np = np.asarray(x_scaled, dtype=np.float32).reshape(1, -1)
        x = torch.tensor(x_np, dtype=torch.float32)
        x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
        with torch.inference_mode():
            logits = self.model(x, return_logits=True)
            probs = nn.functional.softmax(logits, dim=-1)[0]
        probs_list = [float(v) for v in probs.tolist()]
        action = _action_from_probs(probs_list)
        return {
            "probabilities": probs_list,
            "action": action,
            "confidence": float(max(probs_list)),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "artifact_path": str(self.artifact_path),
            "artifact_hash": self.ref.artifact_hash if self.ref else "",
            "feature_schema_id": self.ref.feature_schema_id if self.ref else "",
            "feature_dimension": self.ref.feature_dimension if self.ref else 0,
            "num_classes": self.num_classes,
            "loaded": self.model is not None,
        }


def _action_from_probs(probs: list[float]) -> str:
    """Maps the 4-class ScalpNet output to an action (0=NO_TRADE,1=BUY,2=SELL,3=WAIT)."""
    idx = max(range(len(probs)), key=lambda i: probs[i])
    mapping = {0: "NO_TRADE", 1: "BUY_MARKET", 2: "SELL_MARKET", 3: "WAIT"}
    return mapping.get(idx, "NO_TRADE")


def load_challenger(
    artifact_path: Path | str,
    scaler_path: Path | str,
    model_id: str,
    model_version: str,
    live_schema_id: str,
    live_dimension: int,
) -> ChallengerRuntime:
    """Convenience factory (raises ChallengerLoadError on any integrity problem)."""
    return ChallengerRuntime(
        artifact_path=artifact_path,
        scaler_path=scaler_path,
        model_id=model_id,
        model_version=model_version,
        live_schema_id=live_schema_id,
        live_dimension=live_dimension,
    )
