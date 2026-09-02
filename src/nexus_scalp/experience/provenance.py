"""
Model Provenance Registry
=========================
Phase 08 model/memory separation.

This is the ONLY model registry in the repository. It intentionally stores
*descriptive metadata* about model artifacts - never weights, never file
handles, never a torch import. Consequences:

* The production model artifact may be deleted, retrained, rebuilt on startup,
  hot-swapped or widened from 50D to 60D/350D without touching a single stored
  experience.
* A rebuilt model can read the provenance of historical experiences even though
  the artifact that produced them no longer exists.
* Historical experiences are NEVER rewritten to match a newly registered model.

Registration is idempotent per (model_id, model_version, artifact_fingerprint).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.models import (
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
    ModelProvenance,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.provenance")


def fingerprint_artifact(path: Path | str, chunk_size: int = 1 << 20) -> str:
    """
    SHA256 prefix of a model artifact, or "" when it does not exist.

    A missing artifact is a normal, supported state: the engine may be starting
    cold and about to construct a fresh model. Experience memory does not depend
    on this value.
    """
    p = Path(path)
    try:
        if not p.exists() or not p.is_file():
            return ""
        digest = hashlib.sha256()
        with p.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()[:16]
    except Exception as e:
        logger.warning("[MODEL] artifact fingerprint failed", path=str(p), error=str(e))
        return ""


class ModelRegistry:
    """
    Append-only registry of model identities used by the decision path.

    The registry answers "which artifact, schema and config produced this
    decision?" without requiring the artifact to still exist.
    """

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo
        self._current: ModelProvenance = ModelProvenance()

    @property
    def current(self) -> ModelProvenance:
        """Provenance stamped onto experiences recorded right now."""
        return self._current

    def register_model(
        self,
        artifact_path: Path | str,
        model_version: str,
        feature_schema_id: str = CANONICAL_FEATURE_SCHEMA_ID,
        feature_dimension: int = CANONICAL_FEATURE_DIMENSION,
        config_version: str = "0.0.0",
        model_role: str = "PRIMARY_SCALP",
        build_identity: str = "",
        replaced: bool = False,
    ) -> ModelProvenance:
        """
        Registers the currently active model and returns its provenance.

        `replaced=True` marks a hot-swap/retrain event. Registering a new model
        NEVER modifies or deletes any experience: prior rows keep pointing at
        the provenance that actually produced them.
        """
        fingerprint = fingerprint_artifact(artifact_path)
        model_id = f"{model_role.lower()}_{feature_schema_id}_{feature_dimension}d"
        provenance = ModelProvenance(
            model_id=model_id,
            model_version=model_version,
            model_role=model_role,
            artifact_fingerprint=fingerprint,
            feature_schema_id=feature_schema_id,
            feature_dimension=feature_dimension,
            config_version=config_version,
            build_identity=build_identity,
            registered_at=datetime.now(UTC),
        )
        self._current = provenance
        self._persist(provenance, artifact_path=str(artifact_path), replaced=replaced)

        logger.info(
            "[MODEL] REPLACED" if replaced else "[MODEL] REGISTERED",
            model_id=provenance.model_id,
            model_version=provenance.model_version,
            artifact_fingerprint=fingerprint or "ABSENT",
            feature_schema=provenance.feature_schema_id,
            feature_dimension=provenance.feature_dimension,
        )
        return provenance

    def _persist(self, provenance: ModelProvenance, artifact_path: str, replaced: bool) -> None:
        """Queues the provenance row through the async audit worker."""
        if not self.audit_repo._is_sqlite:
            return
        query = """
            INSERT INTO experience_model_registry
            (model_id, model_version, model_role, artifact_path, artifact_fingerprint,
             feature_schema_id, feature_dimension, config_version, build_identity,
             was_replacement, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_id, model_version, artifact_fingerprint) DO UPDATE SET
                registered_at=excluded.registered_at,
                was_replacement=excluded.was_replacement;
        """
        args = (
            provenance.model_id,
            provenance.model_version,
            provenance.model_role,
            artifact_path,
            provenance.artifact_fingerprint,
            provenance.feature_schema_id,
            provenance.feature_dimension,
            provenance.config_version,
            provenance.build_identity,
            1 if replaced else 0,
            provenance.registered_at.isoformat(),
        )
        try:
            self.audit_repo._queue.put_nowait((query, args))
        except Exception as e:
            logger.error("[MODEL] registry persistence failed", error=str(e))

    def list_registered_models(self, limit: int = 50) -> list[dict[str, object]]:
        """Bounded listing of registered model identities, newest first."""
        if not self.audit_repo._is_sqlite:
            return []
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT * FROM experience_model_registry
                    ORDER BY registered_at DESC LIMIT ?;
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL] registry listing failed", error=str(e))
            return []
