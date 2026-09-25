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

    def supersede_champion_on_governed_replace(
        self,
        *,
        model_id: str,
        model_version: str,
        artifact_path: str,
        new_fingerprint: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """BUG-271: repoint the governed CHAMPION fingerprint after a
        GOVERNED in-place replacement of the serving artifact.

        The async-retrain persist, collapse recovery, promotion/rollback
        activation and hot-swap paths all rewrite ``model.pt`` IN PLACE at the
        champion path and re-register provenance under the new fingerprint.
        The lifecycle CHAMPION stamp, however, stays on the OLD row (the new
        row lands with the column default CANDIDATE), which orphans the
        governed fingerprint. The next cold boot then compares serving bytes
        against that stale fingerprint and the P0-2 trust anchor
        (application/live/model_bundle_store) refuses to load — trading dies
        permanently after a legitimate retrain, with no self-heal path.

        Semantics (evidence-preserving, append-only):
          * the stale champion row goes ARCHIVED with its ORIGINAL fingerprint
            intact (history of what bytes were governed is never rewritten);
          * the row carrying the new fingerprint becomes CHAMPION;
          * both transitions are ONE queued statement, so the write-queue
            worker can never expose a state with zero or two champions;
          * only rows already in CHAMPION/CANDIDATE participate — a REJECTED /
            INVALID / ARCHIVED row is never resurrected by a persist.

        Refusals are explicit and observable (never silent):
          EMPTY_FINGERPRINT     caller has no governed identity for the bytes
          NO_CHAMPION_ROW       nothing to supersede (registry read failed)
          NO_SUPERSESSION_NEEDED champion already carries the new fingerprint,
                                or governs a DIFFERENT artifact path (that is
                                champion_sync's registry-truth decision, not a
                                persist's business), or carries no path
        This is NOT an operator override: only the governed persist paths call
        it, and an unauthorized (out-of-process) rewrite of the artifact still
        fails closed at boot — the fingerprint on disk will not match any row.
        """
        out: dict[str, Any] = {"ok": True, "reason": "NO_SUPERSESSION_NEEDED"}
        if not self.audit_repo._is_sqlite:
            out["reason"] = "NOT_SQLITE"
            return out
        new_fp = str(new_fingerprint or "").strip().lower()
        if not new_fp:
            return {"ok": False, "reason": "EMPTY_FINGERPRINT"}
        self.ensure_schema()

        def _norm(p: Any) -> str:
            return str(p or "").replace("\\", "/")

        serving = _norm(artifact_path)
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                champion = conn.execute(
                    "SELECT * FROM experience_model_registry "
                    "WHERE lifecycle_status=? ORDER BY registered_at DESC LIMIT 1;",
                    (ModelStatus.CHAMPION.value,),
                ).fetchone()
                new_row = conn.execute(
                    "SELECT lifecycle_status FROM experience_model_registry "
                    "WHERE model_id=? AND model_version=? AND artifact_fingerprint=? "
                    "ORDER BY registered_at DESC LIMIT 1;",
                    (model_id, model_version, new_fp),
                ).fetchone()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL_REGISTRY] supersession read failed", error=str(e))
            return {"ok": False, "reason": "REGISTRY_READ_FAILED", "detail": str(e)[:200]}
        if champion is None:
            out["reason"] = "NO_CHAMPION_ROW"
            return out
        # PROMOTABILITY is refused EARLY when the successor row is already
        # visible and non-promotable: a row in REJECTED/INVALID must never
        # become CHAMPION by merely being persisted. An ABSENT successor row is
        # NOT a refusal — the provenance INSERT is queued on the same FIFO
        # audit worker as this statement (the write may simply not be durable
        # yet), so the SQL EXISTS guard below is the authority and decides the
        # outcome after the insert has applied.
        promotable = {
            ModelStatus.CHAMPION.value,
            ModelStatus.CANDIDATE.value,
            ModelStatus.ARCHIVED.value,
        }
        if new_row is not None and str(new_row["lifecycle_status"]) not in promotable:
            return {
                **out,
                "champion_row": _row_identity(dict(champion)),
                "successor_status": str(new_row["lifecycle_status"]),
            }
        row = dict(champion)
        old_fp = str(row.get("artifact_fingerprint", "") or "").strip().lower()
        row_path = _norm(row.get("artifact_path", ""))
        if str(row.get("model_id", "") or "") != str(model_id or "") or (
            str(row.get("model_version", "") or "") != str(model_version or "")
        ):
            # A different identity is champion (cross-contract promotion /
            # registry-truth shape): the governed activation path and
            # champion_sync own that transition, not a persist.
            return {**out, "champion_row": _row_identity(row)}
        if not row_path or not serving:
            out["reason"] = "NO_SUPERSESSION_NEEDED_NO_PATH"
            return out
        if old_fp == new_fp:
            # Champion already governs the on-disk bytes: idempotent no-op
            # (the boot anchor compares fingerprints, so a stale PATH on the
            # row is champion_sync's business, not a reason to re-write).
            return {**out, "champion_row": _row_identity(row), "fingerprint": new_fp}
        if not old_fp:
            out["reason"] = "CHAMPION_ROW_HAS_NO_FINGERPRINT"
            return out

        # ONE statement, two transitions (see docstring): the new-fingerprint
        # row becomes CHAMPION, the stale champion goes ARCHIVED. Runs after
        # the provenance INSERT because the audit write queue is FIFO. The
        # EXISTS guard makes the statement all-or-nothing: promotion requires
        # a PROMOTABLE row (CHAMPION/CANDIDATE/ARCHIVED — ARCHIVED covers the
        # rollback-to-previously-governed-bytes shape, because provenance
        # re-registration ON CONFLICT refreshes the old row in place instead
        # of creating a new one). A REJECTED/INVALID row is never resurrected
        # by a persist; if nothing is promotable, the stale champion stays put
        # and the next boot fails closed exactly as before the fix.
        _promotable = (
            ModelStatus.CHAMPION.value,
            ModelStatus.CANDIDATE.value,
            ModelStatus.ARCHIVED.value,
        )
        query = """
            UPDATE experience_model_registry
            SET lifecycle_status = CASE
                    WHEN artifact_fingerprint=? THEN ?
                    ELSE ?
                END,
                promotion_reason = ?
            WHERE model_id=? AND model_version=?
              AND (
                    (artifact_fingerprint=? AND lifecycle_status IN (?,?,?))
                    OR (artifact_fingerprint=? AND lifecycle_status=?)
              )
              AND EXISTS (
                    SELECT 1 FROM experience_model_registry
                    WHERE model_id=? AND model_version=?
                      AND artifact_fingerprint=? AND lifecycle_status IN (?,?,?)
              );
        """
        args = (
            new_fp,
            ModelStatus.CHAMPION.value,
            ModelStatus.ARCHIVED.value,
            reason or "governed in-place artifact replacement",
            model_id,
            model_version,
            new_fp,
            _promotable[0],
            _promotable[1],
            _promotable[2],
            old_fp,
            ModelStatus.CHAMPION.value,
            model_id,
            model_version,
            new_fp,
            _promotable[0],
            _promotable[1],
            _promotable[2],
        )
        try:
            self.audit_repo._queue.put_nowait((query, args))
        except Exception as e:
            logger.error("[MODEL_REGISTRY] supersession persist failed", error=str(e))
            return {
                "ok": False,
                "reason": "QUEUE_FAILED",
                "detail": str(e)[:200],
                "stale_fingerprint": old_fp,
                "new_fingerprint": new_fp,
            }
        logger.warning(
            "[MODEL] event=CHAMPION_FINGERPRINT_SUPERSEDED "
            "model_id=%s version=%s stale_sha16=%s new_sha16=%s reason=%s",
            model_id,
            model_version,
            old_fp,
            new_fp,
            reason or "governed_in_place_replace",
        )
        return {
            "ok": True,
            "reason": "SUPERSEDED",
            "model_id": model_id,
            "model_version": model_version,
            "stale_fingerprint": old_fp,
            "new_fingerprint": new_fp,
        }

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
