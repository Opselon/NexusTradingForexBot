"""
Champion Model Management
=========================
PHASE 10 the production-authorized model (spec 3 / 26 / 28 / 29).

The Champion is the ONLY model allowed in the production inference path. Loading
it verifies integrity (hash, schema, dimension, class count, scaler) and NEVER
silently loads a corrupted artifact. A missing/invalid Champion is a supported
cold-start state: experience/strategy/history are preserved and the engine
continues on a fresh artifact, but the lineage explicitly records it.

The Champion artifact is NEVER overwritten by candidate training: candidates
write to `candidate/staging` paths and only a fully verified artifact may
become a Challenger (spec 16 / 33).
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
from nexus_scalp.model_lifecycle.models import ModelArtifactInfo
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.champion")


def resolve_schema(schema_id: str | None = None):
    """Resolves a feature schema id (defaults to the active schema)."""

    return FEATURE_SCHEMAS.resolve(schema_id)


class ChampionModel:
    """Immutable description of the current production model."""

    def __init__(
        self,
        artifact_path: Path | str,
        model_id: str,
        model_version: str,
        feature_schema_id: str,
        feature_dimension: int,
        num_classes: int = 4,
        scaler_path: Path | str = "",
    ) -> None:
        self.artifact_path = Path(artifact_path)
        self.model_id = model_id
        self.model_version = model_version
        self.feature_schema_id = feature_schema_id
        self.feature_dimension = int(feature_dimension)
        self.num_classes = int(num_classes)
        if scaler_path:
            self.scaler_path = Path(scaler_path)
        else:
            # Canonical sibling naming: model.pt -> model.scaler.npz (the
            # trainer/forensics convention). The old '.pt.scaler.npz'
            # suffix silently missed the real file and logged a misleading
            # 'scaler missing' warning on every verify while never
            # validating the scaler.
            art = Path(self.artifact_path)
            if art.name.endswith(".pt"):
                self.scaler_path = art.with_name("model.scaler.npz")
            else:
                self.scaler_path = Path(str(art) + ".scaler.npz")
        self.info: ModelArtifactInfo = inspect_artifact(
            self.artifact_path,
            self.scaler_path,
            model_id=model_id,
            model_version=model_version,
            feature_schema_id=feature_schema_id,
            feature_dimension=feature_dimension,
            num_classes=num_classes,
        )

    @property
    def available(self) -> bool:
        return self.info.integrity_ok

    @property
    def artifact_hash(self) -> str:
        return self.info.artifact_hash

    def verify(self, raise_on_mismatch: bool = True) -> bool:
        """Verifies compatibility; raises by default, returns bool otherwise."""
        if not self.available:
            if raise_on_mismatch:
                raise SchemaCompatibilityError(
                    f"Champion artifact {self.artifact_path} is missing or invalid "
                    f"(hash={self.info.artifact_hash or 'MISSING'})"
                )
            return False
        if not scaler_compatibility(self.scaler_path, self.feature_dimension):
            logger.warning(
                "[MODEL] Champion scaler missing/mismatched (cold-start acceptable)",
                path=str(self.scaler_path),
            )
        return True

    def summary(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "artifact_path": str(self.artifact_path),
            "artifact_hash": self.info.artifact_hash,
            "feature_schema_id": self.feature_schema_id,
            "feature_dimension": self.feature_dimension,
            "num_classes": self.num_classes,
            "available": self.available,
            "scaler_path": str(self.scaler_path),
        }


class ChampionManager:
    """
    Wraps the production Champion path so the rest of Phase 10 can reference
    the Champion without owning an execution path.
    """

    def __init__(
        self,
        artifact_path: Path | str,
        model_id: str = "primary_scalp",
        model_version: str = "1.0.0",
        feature_schema_id: str | None = None,
        feature_dimension: int | None = None,
        num_classes: int = 4,
    ) -> None:
        schema = resolve_schema(feature_schema_id)
        self.artifact_path = Path(artifact_path)
        self.model_id = model_id
        self.model_version = model_version
        self.feature_schema_id = schema.schema_id
        self.feature_dimension = feature_dimension or schema.dimension
        self.num_classes = int(num_classes)

    def load_champion(self) -> ChampionModel:
        """Loads + verifies the Champion; raises on corruption."""
        champion = ChampionModel(
            self.artifact_path,
            model_id=self.model_id,
            model_version=self.model_version,
            feature_schema_id=self.feature_schema_id,
            feature_dimension=self.feature_dimension,
            num_classes=self.num_classes,
        )
        champion.verify(raise_on_mismatch=True)
        logger.info(
            "[MODEL] CHAMPION VERIFIED",
            model_id=champion.model_id,
            version=champion.model_version,
            hash=champion.artifact_hash,
        )
        return champion

    def champion_or_none(self) -> ChampionModel | None:
        """Best-effort load: returns None (never raises) when unavailable."""
        try:
            return self.load_champion()
        except Exception as e:
            logger.warning("[MODEL] Champion unavailable", error=str(e))
            return None

    def candidate_artifact_path(self, run_id: str) -> Path:
        """Staging path for a candidate - NEVER the champion path (spec 33)."""
        return self.artifact_path.parent / "candidate" / run_id / "model.pt"

    def candidate_scaler_path(self, run_id: str) -> Path:
        return Path(str(self.candidate_artifact_path(run_id)) + ".scaler.npz")

    def challenger_artifact_path(self, run_id: str) -> Path:
        """Path a fully-verified candidate is promoted to as Challenger."""
        return self.artifact_path.parent / "challengers" / run_id / "model.pt"

    def challenger_scaler_path(self, run_id: str) -> Path:
        return Path(str(self.challenger_artifact_path(run_id)) + ".scaler.npz")
