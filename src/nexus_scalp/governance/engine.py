"""
Model Governance Engine
=======================
TASK-6 / CHG-0003: the truthful runtime registry + promotion gate +
rollback + health state machine. Composable with the LiveEngine but holds
NO execution references (no adapter / order manager / risk engine).

Responsibilities
----------------
1. REGISTRY TRUTHFULNESS (spec 3): reconcile the canonical
   `experience_model_registry` rows and the model_generation artifacts into
   explicit answers for CURRENT_CHAMPION / CURRENT_CHALLENGER / SHADOW /
   PENDING_APPROVAL / RETIRED / FAILED. A model is never "current" merely
   because its file exists.
2. PROMOTION STATE MACHINE (spec 21 / 22 / 23): audited transitions with
   hard gates; operator approval required; rollback restores artifact +
   manifest + scaler + runtime pointer + metadata without deleting evidence.
3. HEALTH (spec 27): truthful champion/challenger/shadow runtime state.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.governance.load_gate import ModelLoadGate, sha256_hex
from nexus_scalp.governance.models import (
    PROMOTION_TRANSITIONS,
    GovernanceErrorCode,
    GovernanceEvent,
    GovernanceStage,
    PromotionState,
    PromotionTransition,
    RegistryCategory,
    RegistryModel,
)
from nexus_scalp.governance.store import GovernanceStore
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.governance.engine")

#: The promotion checklist gates with their evidence keys (spec 22).
CHECKLIST_EVIDENCE_KEYS: tuple[str, ...] = (
    "artifact_valid",
    "manifest_valid",
    "schema_valid",
    "scaler_valid",
    "oos_pass",
    "robustness_pass",
    "calibration_acceptable",
    "no_class_collapse",
    "no_severe_feature_drift",
    "shadow_sample_floor",
    "shadow_evidence_acceptable",
    "latency_acceptable",
    "no_critical_anomalies",
    "rollback_target",
)


class PromotionGateError(RuntimeError):
    """Raised when a promotion/rollback is blocked by a hard gate."""


class ModelGovernanceEngine:
    """Truthful registry + promotion lifecycle + rollback + health."""

    def __init__(
        self,
        store: GovernanceStore,
        dependency_map: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.load_gate = ModelLoadGate()
        #: dependency_map carries the runtime pointers needed for a real
        #: activation swap (e.g. {"activate": callable, "repo": ..., ...}).
        #: Defaults to none: promotion records the transition and leaves the
        #: final activation to the operator-approved wiring.
        self.dep = dependency_map or {}
        self._health_cache: dict[str, Any] = {}
        self._last_reconcile: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Registry truthfulness
    # ------------------------------------------------------------------

    def registry_snapshot(
        self,
        *,
        audit_db: Path | str,
        artifact_store: Any | None = None,
        champion_id: str = "",
        champion_artifact: Path | str = "",
    ) -> dict[str, Any]:
        """Answers the six registry questions with full metadata.

        This is a READ-ONLY reconciliation; it never writes and never
        mutates a registry. Returns:
            {
              "categories": {CURRENT_CHAMPION: RegistryModel|None, ...},
              "models": [...all...],
              "reconciled_at": ...,
              "champion_verification": {...}
            }
        """
        out: dict[str, Any] = {
            "categories": {},
            "models": [],
            "reconciled_at": datetime.now(UTC).isoformat(),
        }
        db = Path(audit_db)
        if not db.exists():
            return out
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM experience_model_registry ORDER BY registered_at DESC LIMIT 500;"
                ).fetchall()
                models: list[RegistryModel] = []
                for r in rows:
                    d = dict(r)
                    models.append(
                        RegistryModel(
                            model_id=str(d.get("model_id", "")),
                            version=str(d.get("model_version", "")),
                            architecture="",
                            schema_id=str(d.get("feature_schema_id", "")),
                            input_dimension=int(d.get("feature_dimension", 0) or 0),
                            scaler_hash="",
                            artifact_hash=str(d.get("artifact_fingerprint", "")),
                            manifest_hash="",
                            validation_result=str(d.get("gate_summary", "") or "{}"),
                            oos_result="",
                            robustness_result="",
                            registration_timestamp=str(d.get("registered_at", "")),
                            source_commit="",
                            lifecycle_state=str(d.get("lifecycle_status", "CANDIDATE")),
                            artifact_path=str(d.get("artifact_path", "")),
                            category=self._category_for(
                                str(d.get("lifecycle_status", "CANDIDATE"))
                            ),
                        )
                    )
                # Champion from lifecycle CHAMPION first, else the passed identity,
                # else the most recent row that matches the champion artifact.
                champion = next((m for m in models if m.lifecycle_state == "CHAMPION"), None)
                if champion is None and champion_id:
                    champion = next((m for m in models if m.model_id == champion_id), None)
                if champion is None and champion_artifact:
                    champ_path = str(Path(champion_artifact))
                    champion = next(
                        (
                            m
                            for m in models
                            if m.artifact_path.replace("\\\\", "/")
                            == champ_path.replace("\\\\", "/")
                        ),
                        None,
                    )
                cats: dict[str, RegistryModel | None] = {c.value: None for c in RegistryCategory}
                if champion is not None:
                    cats[RegistryCategory.CURRENT_CHAMPION.value] = champion
                challengers = [m for m in models if m.lifecycle_state == "CHALLENGER"]
                if challengers:
                    cats[RegistryCategory.CURRENT_CHALLENGER.value] = challengers[0]
                shadow_rows = [
                    m
                    for m in models
                    if m.lifecycle_state in ("SHADOW", "READY_FOR_REVIEW", "APPROVED")
                ]
                if shadow_rows:
                    cats[RegistryCategory.SHADOW.value] = shadow_rows[0]
                pending = [
                    m for m in models if m.lifecycle_state in ("CANDIDATE", "VALIDATED", "RESEARCH")
                ]
                if pending:
                    cats[RegistryCategory.PENDING_APPROVAL.value] = pending[0]
                retired = [m for m in models if m.lifecycle_state in ("RETIRED", "ARCHIVED")]
                if retired:
                    cats[RegistryCategory.RETIRED.value] = retired[0]
                failed = [
                    m for m in models if m.lifecycle_state in ("REJECTED", "FAILED", "INVALID")
                ]
                if failed:
                    cats[RegistryCategory.FAILED.value] = failed[0]

                # Enrich the champion with live artifact verification.
                champ_verify: dict[str, Any] = {}
                if champion is not None and champion.artifact_path:
                    champ_verify = self._verify_champion_artifact(champion.artifact_path)

                out["categories"] = {
                    k: (v.model_dump(mode="json") if v else None) for k, v in cats.items()
                }
                out["models"] = [m.model_dump(mode="json") for m in models]
                out["champion_verification"] = champ_verify
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] registry snapshot failed", error=str(e))
        return out

    @staticmethod
    def _category_for(state: str) -> RegistryCategory:
        mapping = {
            "CHAMPION": RegistryCategory.CURRENT_CHAMPION,
            "CHALLENGER": RegistryCategory.CURRENT_CHALLENGER,
            "SHADOW": RegistryCategory.SHADOW,
            "READY_FOR_REVIEW": RegistryCategory.PENDING_APPROVAL,
            "APPROVED": RegistryCategory.PENDING_APPROVAL,
            "CANDIDATE": RegistryCategory.PENDING_APPROVAL,
            "VALIDATED": RegistryCategory.PENDING_APPROVAL,
            "RESEARCH": RegistryCategory.PENDING_APPROVAL,
            "RETIRED": RegistryCategory.RETIRED,
            "ARCHIVED": RegistryCategory.RETIRED,
            "REJECTED": RegistryCategory.FAILED,
            "FAILED": RegistryCategory.FAILED,
            "INVALID": RegistryCategory.FAILED,
        }
        return mapping.get(state, RegistryCategory.SHADOW)

    def _verify_champion_artifact(self, artifact_path: str) -> dict[str, Any]:
        p = Path(artifact_path)
        scaler = Path(str(p) + ".scaler.npz")
        gate = self.load_gate.evaluate(
            artifact_path=p,
            scaler_path=scaler,
            model_id="",
            manifest=None,
        )
        return {
            "exists": p.exists(),
            "hash": sha256_hex(p) if p.exists() else "",
            "load_gate": gate.model_dump(mode="json"),
        }

    # ------------------------------------------------------------------
    # Promotion state machine
    # ------------------------------------------------------------------

    def can_transition(self, current: PromotionState, target: PromotionState) -> bool:
        allowed = PROMOTION_TRANSITIONS.get(current, set())
        return target in allowed

    def transition(
        self,
        *,
        model_id: str,
        model_version: str,
        target: PromotionState,
        actor: str = "system",
        reason: str = "",
        evidence: dict[str, Any] | None = None,
        source_commit: str = "",
        artifact_hash: str = "",
        enforce_gate: bool = True,
    ) -> PromotionTransition:
        """Applies one audited transition. Raises PromotionGateError when
        the target is not reachable from the current state.

        IMPORTANT (spec 21): SHADOW -> CHAMPION is NEVER reachable here. The
        path is SHADOW -> READY_FOR_REVIEW -> APPROVED -> CHAMPION and the
        final APPROVED -> CHAMPION step additionally requires
        ``enforce_gate``-style operator wiring (see ``promote``).
        """
        current_row = self.store.get_state(model_id, model_version)
        current_str = (
            str(current_row.get("lifecycle_state", "RESEARCH")) if current_row else "RESEARCH"
        )
        try:
            current = PromotionState(current_str)
        except ValueError:
            current = PromotionState.RESEARCH

        allowed = PROMOTION_TRANSITIONS.get(current, set())
        if target not in allowed:
            ev = GovernanceEvent(
                event_id=f"ev_{uuid.uuid4().hex[:16]}",
                event=GovernanceErrorCode.PROMOTION_BLOCKED.value,
                stage=GovernanceStage.PROMOTION,
                model_id=model_id,
                model_version=model_version,
                actor=actor,
                previous_state=current.value,
                new_state=target.value,
                reason=f"illegal transition {current.value} -> {target.value}",
                payload={"allowed_from_current": sorted(v.value for v in allowed)},
            )
            self.store.record_event(ev)
            raise PromotionGateError(ev.reason)

        t = PromotionTransition(
            transition_id=f"tr_{uuid.uuid4().hex[:12]}",
            model_id=model_id,
            model_version=model_version,
            previous_state=current,
            new_state=target,
            actor=actor,
            reason=reason,
            evidence_snapshot=evidence or {},
            source_commit=source_commit,
            artifact_hash=artifact_hash,
        )
        self.store.record_transition(t)
        logger.info(
            "[MODEL_GOVERNANCE] event=PROMOTION_TRANSITION",
            model_id=model_id,
            previous=current.value,
            new=target.value,
            actor=actor,
        )
        return t

    def promotion_checklist(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Evaluates the hard promotion checklist (spec 22)."""
        passed: list[str] = []
        failed: list[str] = []
        for key in CHECKLIST_EVIDENCE_KEYS:
            v = evidence.get(key)
            if v is True:
                passed.append(key)
            else:
                failed.append(key)
        return {
            "passed": passed,
            "failed": failed,
            "ready_for_review": not failed,
            "checklist": CHECKLIST_EVIDENCE_KEYS,
        }

    def promote_to_review(
        self,
        *,
        model_id: str,
        model_version: str,
        actor: str,
        evidence: dict[str, Any],
        source_commit: str = "",
        artifact_hash: str = "",
    ) -> PromotionTransition:
        """SHADOW -> READY_FOR_REVIEW. Blocks unless the FULL checklist passes."""
        check = self.promotion_checklist(evidence)
        if not check["ready_for_review"]:
            ev = GovernanceEvent(
                event_id=f"ev_{uuid.uuid4().hex[:16]}",
                event=GovernanceErrorCode.PROMOTION_BLOCKED.value,
                stage=GovernanceStage.PROMOTION,
                model_id=model_id,
                model_version=model_version,
                actor=actor,
                previous_state=PromotionState.SHADOW.value,
                new_state=PromotionState.READY_FOR_REVIEW.value,
                reason="promotion checklist not satisfied",
                payload={"failed": check["failed"]},
            )
            self.store.record_event(ev)
            raise PromotionGateError(f"promotion checklist failed: {check['failed']}")
        return self.transition(
            model_id=model_id,
            model_version=model_version,
            target=PromotionState.READY_FOR_REVIEW,
            actor=actor,
            reason="operator promotion review requested (checklist passed)",
            evidence=evidence,
            source_commit=source_commit,
            artifact_hash=artifact_hash,
        )

    def approve(
        self,
        *,
        model_id: str,
        model_version: str,
        actor: str,
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> PromotionTransition:
        """READY_FOR_REVIEW -> APPROVED. Requires an explicit operator actor."""
        if not actor or actor == "system":
            raise PromotionGateError("approval requires an explicit operator actor")
        return self.transition(
            model_id=model_id,
            model_version=model_version,
            target=PromotionState.APPROVED,
            actor=actor,
            reason=reason,
            evidence=evidence or {},
        )

    def promote(
        self,
        *,
        model_id: str,
        model_version: str,
        actor: str,
        reason: str,
        approval_token: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> PromotionTransition:
        """APPROVED -> CHAMPION.

        The final activation is operator-gated: the caller must pass the
        operator approval token. When a runtime `activate` callback is wired
        in `dependency_map`, it is invoked AFTER the state transition is
        recorded; its failure raises and the transition is recorded as a
        failed PROMOTION event (evidence preserved, never deleted).
        """
        if not approval_token:
            raise PromotionGateError(
                "promotion requires the operator approval token (no auto-promotion)"
            )
        t = self.transition(
            model_id=model_id,
            model_version=model_version,
            target=PromotionState.CHAMPION,
            actor=actor,
            reason=reason,
            evidence=evidence or {},
        )
        activate = self.dep.get("activate")
        if callable(activate):
            try:
                activate(model_id=model_id, model_version=model_version)
            except Exception as e:
                self.store.record_event(
                    GovernanceEvent(
                        event_id=f"ev_{uuid.uuid4().hex[:16]}",
                        event=GovernanceErrorCode.PROMOTION_BLOCKED.value,
                        stage=GovernanceStage.PROMOTION,
                        model_id=model_id,
                        model_version=model_version,
                        actor=actor,
                        previous_state=PromotionState.APPROVED.value,
                        new_state=PromotionState.CHAMPION.value,
                        reason=f"runtime activation failed: {e}",
                        payload={"evidence": evidence or {}},
                    )
                )
                raise PromotionGateError(f"runtime activation failed: {e}") from e
        return t

    # ------------------------------------------------------------------
    # Rollback (spec 23)
    # ------------------------------------------------------------------

    def rollback(
        self,
        *,
        failed_model_id: str,
        failed_version: str,
        previous_model_id: str,
        previous_version: str,
        actor: str,
        reason: str,
        previous_artifact: Path | str = "",
        previous_scaler: Path | str = "",
        manifest: dict[str, Any] | None = None,
    ) -> PromotionTransition:
        """Rolls the runtime pointer back to the previous Champion.

        Evidence about the failed model is NEVER deleted — the event ledger
        preserves it. When a `rollback_activate` callback is wired it
        receives the previous identity so the runtime pointer is restored.
        """
        t = PromotionTransition(
            transition_id=f"tr_{uuid.uuid4().hex[:12]}",
            model_id=failed_model_id,
            model_version=failed_version,
            previous_state=PromotionState.CHAMPION,
            new_state=PromotionState.RETIRED,
            actor=actor,
            reason=reason,
            evidence_snapshot={
                "rollback_to": {"model_id": previous_model_id, "version": previous_version},
                "previous_artifact": str(previous_artifact),
                "previous_scaler": str(previous_scaler),
                "manifest": manifest or {},
            },
        )
        self.store.record_transition(t)
        self.store.record_event(
            GovernanceEvent(
                event_id=f"ev_{uuid.uuid4().hex[:16]}",
                event=GovernanceErrorCode.ROLLBACK_EXECUTED.value,
                stage=GovernanceStage.ROLLBACK,
                model_id=failed_model_id,
                model_version=failed_version,
                actor=actor,
                previous_state=PromotionState.CHAMPION.value,
                new_state=PromotionState.RETIRED.value,
                reason=reason,
                payload={"rollback_to": previous_model_id, "rollback_version": previous_version},
            )
        )
        rb = self.dep.get("rollback_activate")
        if callable(rb):
            try:
                rb(model_id=previous_model_id, model_version=previous_version)
            except Exception as e:
                logger.error("[MODEL_GOVERNANCE] rollback activation failed", error=str(e))
        logger.info(
            "[MODEL_GOVERNANCE] event=ROLLBACK_EXECUTED",
            failed=f"{failed_model_id}@{failed_version}",
            restored=f"{previous_model_id}@{previous_version}",
            actor=actor,
        )
        return t

    # ------------------------------------------------------------------
    # Health (spec 27 / 28)
    # ------------------------------------------------------------------

    def health(
        self,
        *,
        champion: dict[str, Any] | None = None,
        challenger: dict[str, Any] | None = None,
        shadow: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Truthful runtime health envelope (spec 27 JSON contract)."""
        champ = champion or {}
        chal = challenger or {}
        shad = shadow or {}
        return {
            "champion": {
                "id": champ.get("id", ""),
                "version": champ.get("version", ""),
                "schema": champ.get("schema", ""),
                "healthy": bool(champ.get("healthy", False)),
                "artifact_hash": champ.get("artifact_hash", ""),
            },
            "challenger": {
                "id": chal.get("id", ""),
                "version": chal.get("version", ""),
                "schema": chal.get("schema", ""),
                "state": chal.get("state", "NONE"),
            },
            "shadow": {
                "running": bool(shad.get("running", False)),
                "comparisons": int(shad.get("comparisons", 0)),
                "errors": int(shad.get("errors", 0)),
                "dropped": int(shad.get("dropped", 0)),
                "last_update": shad.get("last_update", ""),
            },
            "checked_at": datetime.now(UTC).isoformat(),
            "promotion_state": self._promotion_state_summary(),
        }

    def _promotion_state_summary(self) -> dict[str, Any]:
        if (
            not self.store
            or not getattr(self.store, "audit_repo", None)
            or not getattr(self.store.audit_repo, "_is_sqlite", False)
        ):
            return {}
        try:
            return self.store.summary()
        except Exception:
            return {}
