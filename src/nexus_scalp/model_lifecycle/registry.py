"""
Model Registry Extension (Champion / Challenger / Candidate)
============================================================
PHASE 10 additive extension of the canonical `experience_model_registry`
(spec 5 / 27 / 38.25). The existing `ModelRegistry` (Phase 08) is REUSED -
no duplicate registry is created.

The extension adds lifecycle state to registry rows:
  CHAMPION (production), CHALLENGER (validated candidate, shadow-eligible),
  CANDIDATE (trained, unvalidated), REJECTED (failed a gate),
  ARCHIVED (superseded), INVALID (integrity failure).

Never overwrites history: every promotion/rejection records reason, evidence,
gate results, parent model and child model. Promotion lineage is immutable.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.provenance import ModelRegistry, fingerprint_artifact
from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.model_lifecycle.models import ModelStatus
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.registry")

#: Columns appended additively to experience_model_registry (safe migration).
_EXTENSION_COLUMNS: list[tuple[str, str]] = [
    ("lifecycle_status", "TEXT DEFAULT 'CANDIDATE'"),
    ("training_run_id", "TEXT DEFAULT ''"),
    ("parent_model_id", "TEXT DEFAULT ''"),
    ("parent_model_version", "TEXT DEFAULT ''"),
    ("child_model_id", "TEXT DEFAULT ''"),
    ("promotion_reason", "TEXT DEFAULT ''"),
    ("gate_summary", "TEXT DEFAULT '{}'"),
    ("validation_run_ids", "TEXT DEFAULT '[]'"),
]


def resolve_schema(schema_id: str | None = None):
    """Resolves a feature schema id (defaults to the active schema)."""

    return FEATURE_SCHEMAS.resolve(schema_id)


class ModelLifecycleRegistry:
    """
    Lifecycle-aware facade over the canonical model registry table.

    Writes go through the AuditRepository background queue so the live path is
    never blocked. Reads are bounded short-lived connections.
    """

    def __init__(self, audit_repo: AuditRepository, model_registry: ModelRegistry) -> None:
        self.audit_repo = audit_repo
        self.model_registry = model_registry

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Additive migration: appends lifecycle columns if missing (idempotent)."""
        if not self.audit_repo._is_sqlite:
            return
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                existing = {
                    r[1]
                    for r in conn.execute(
                        "PRAGMA table_info(experience_model_registry);"
                    ).fetchall()
                }
                for col, ctype in _EXTENSION_COLUMNS:
                    if col not in existing:
                        conn.execute(
                            f"ALTER TABLE experience_model_registry ADD COLUMN {col} {ctype};"
                        )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL_REGISTRY] schema migration failed", error=str(e))

    # ------------------------------------------------------------------
    # Status transitions (immutable lineage)
    # ------------------------------------------------------------------

    def set_status(
        self,
        model_id: str,
        model_version: str,
        status: ModelStatus,
        reason: str = "",
        gate_summary: dict[str, Any] | None = None,
        training_run_id: str = "",
        parent_model_id: str = "",
        parent_model_version: str = "",
    ) -> bool:
        """
        Records a lifecycle transition for a registered model.

        Never deletes or rewrites prior rows: the row keeps its identity and the
        transition appends evidence. Returns False when the model was never
        registered (operator should register it first).
        """
        if not self.audit_repo._is_sqlite:
            return False
        self.ensure_schema()
        existing = self.get_status(model_id, model_version)
        if existing is None:
            return False
        query = """
            UPDATE experience_model_registry
            SET lifecycle_status=?, promotion_reason=?,
                gate_summary=?, training_run_id=?,
                parent_model_id=?, parent_model_version=?
            WHERE model_id=? AND model_version=?;
        """
        args = (
            status.value,
            reason,
            json.dumps(gate_summary or {}),
            training_run_id,
            parent_model_id,
            parent_model_version,
            model_id,
            model_version,
        )
        try:
            self.audit_repo._queue.put_nowait((query, args))
            logger.info(
                "[MODEL] event=STATUS",
                model_id=model_id,
                version=model_version,
                status=status.value,
                reason=reason or "",
            )
            return True
        except Exception as e:
            logger.error("[MODEL_REGISTRY] status update failed", error=str(e))
            return False

    def register_candidate(
        self,
        artifact_path: str,
        run_id: str,
        model_id: str,
        model_version: str,
        feature_schema_id: str | None = None,
        feature_dimension: int | None = None,
        parent_model_id: str = "",
        parent_model_version: str = "",
        build_identity: str = "",
    ) -> bool:
        """
        Registers a trained CANDIDATE with its artifact metadata. Idempotent.
        """
        schema = resolve_schema(feature_schema_id)
        dim = feature_dimension or schema.dimension
        fingerprint = fingerprint_artifact(artifact_path)
        if not fingerprint:
            logger.error(
                "[MODEL] event=CANDIDATE_CREATED FAILED (artifact missing)",
                artifact=str(artifact_path),
            )
            return False
        self.model_registry.register_model(
            artifact_path=artifact_path,
            model_version=model_version,
            feature_schema_id=schema.schema_id,
            feature_dimension=dim,
            config_version=build_identity or "0.0.0",
            model_role=model_id,
        )
        # Now stamp the lifecycle status + lineage onto the row.
        return self.set_status(
            model_id=model_id,
            model_version=model_version,
            status=ModelStatus.CANDIDATE,
            reason="trained candidate awaiting validation",
            training_run_id=run_id,
            parent_model_id=parent_model_id,
            parent_model_version=parent_model_version,
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_status(self, model_id: str, model_version: str) -> dict[str, Any] | None:
        """Current registry row for (model_id, model_version)."""
        if not self.audit_repo._is_sqlite:
            return None
        self.ensure_schema()
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM experience_model_registry "
                    "WHERE model_id=? AND model_version=? ORDER BY registered_at DESC LIMIT 1;",
                    (model_id, model_version),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL_REGISTRY] get failed", error=str(e))
            return None

    def list_models(
        self, status: ModelStatus | str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Bounded listing, newest first, optionally filtered by status."""
        if not self.audit_repo._is_sqlite:
            return []
        self.ensure_schema()
        bounded = max(1, min(int(limit), 500))
        sql = "SELECT * FROM experience_model_registry"
        args: tuple[Any, ...] = ()
        if status is not None:
            sql += " WHERE lifecycle_status = ?"
            args = (status.value if isinstance(status, ModelStatus) else str(status),)
        sql += " ORDER BY registered_at DESC LIMIT ?;"
        out: list[dict[str, Any]] = []
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, (*args, bounded)).fetchall()
            finally:
                conn.close()
            for r in rows:
                out.append(dict(r))
        except Exception as e:
            logger.error("[MODEL_REGISTRY] list failed", error=str(e))
        return out

    def champion(self) -> dict[str, Any] | None:
        """The current production-authorized Champion row, if any."""
        rows = self.list_models(status=ModelStatus.CHAMPION, limit=10)
        return rows[0] if rows else None

    def summary(self) -> dict[str, Any]:
        """Counts by status for the dashboard."""
        out: dict[str, Any] = {"available": False, "by_status": {}}
        if not self.audit_repo._is_sqlite:
            return out
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                for r in conn.execute(
                    "SELECT lifecycle_status, COUNT(*) AS c FROM experience_model_registry "
                    "GROUP BY lifecycle_status;"
                ).fetchall():
                    out["by_status"][str(r[0])] = int(r[1])
                out["available"] = True
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL_REGISTRY] summary failed", error=str(e))
        return out


# ---------------------------------------------------------------------------
# Champion-row pairing probe (P0-6 serving trust, registry leg)
# ---------------------------------------------------------------------------
def verify_champion_row_pairing(
    row: dict[str, Any] | None,
    *,
    artifact_path: Path | str | None = None,
) -> dict[str, Any]:
    """Pure pairing probe: the governed CHAMPION row vs the on-disk bytes.

    Detects the Appendix-R drift class AT REST — on-disk serving state that
    no longer matches its governance record — without promoting, repairing,
    or mutating anything. The resolution (recovery vs retirement) belongs to
    the operator; this probe only makes the state observable and fail-closed
    for callers that gate serving on it.

    Returns {ok, reason, ...identity evidence}:
      ok=False, NO_CHAMPION_ROW          no governed row (fail closed — an
                                         artifact must never self-authorize)
      ok=False, ROW_MISSING_ARTIFACT     row does not reference a file
      ok=False, ARTIFACT_MISSING         governed path no longer on disk
      ok=False, FINGERPRINT_MISMATCH     on-disk sha256 prefix != row's
                                         artifact_fingerprint
      ok=True,  PAIRED                   bytes match the governed record
    """
    if not row:
        return {"ok": False, "reason": "NO_CHAMPION_ROW"}
    path_raw = artifact_path if artifact_path is not None else row.get("artifact_path", "")
    if not str(path_raw or ""):
        return {"ok": False, "reason": "ROW_MISSING_ARTIFACT", "row": _row_identity(row)}
    p = Path(str(path_raw))
    if not p.exists() or p.stat().st_size == 0:
        return {
            "ok": False,
            "reason": "ARTIFACT_MISSING",
            "row": _row_identity(row),
            "artifact_path": str(p),
        }
    on_disk = fingerprint_artifact(p)
    governed = str(row.get("artifact_fingerprint", "") or "")
    if not governed:
        return {
            "ok": False,
            "reason": "ROW_MISSING_FINGERPRINT",
            "row": _row_identity(row),
            "on_disk_sha256": on_disk,
        }
    if on_disk != governed.lower():
        return {
            "ok": False,
            "reason": "FINGERPRINT_MISMATCH",
            "row": _row_identity(row),
            "artifact_path": str(p),
            "governed_fingerprint": governed,
            "on_disk_sha256": on_disk,
        }
    return {
        "ok": True,
        "reason": "PAIRED",
        "row": _row_identity(row),
        "artifact_path": str(p),
        "governed_fingerprint": governed,
        "on_disk_sha256": on_disk,
    }


def _row_identity(row: dict[str, Any]) -> dict[str, Any]:
    """Log-safe identity slice of a registry row (no paths in fingerprints)."""
    return {
        "model_id": str(row.get("model_id", "") or ""),
        "model_version": str(row.get("model_version", "") or ""),
        "lifecycle_status": str(row.get("lifecycle_status", "") or ""),
        "feature_schema_id": str(row.get("feature_schema_id", "") or ""),
        "feature_dimension": row.get("feature_dimension"),
    }
