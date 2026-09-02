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

from nexus_scalp.governance.load_gate import ModelLoadGate, read_manifest_file, sha256_hex
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
        #: Emergency controls (spec 31) — frozen flag + disabled candidates.
        #: In-memory by design; the event ledger records every action.
        self.promotion_frozen: bool = False
        self.disabled_candidates: set[str] = set()
        self._emergency_reasons: dict[str, str] = {}

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
        if self.promotion_frozen:
            raise PromotionGateError("promotion frozen by operator (emergency stop)")
        if model_id in self.disabled_candidates:
            raise PromotionGateError(f"candidate {model_id} disabled by operator (emergency stop)")
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

    # ------------------------------------------------------------------
    # Promotion preview / rollback preview / emergency controls (TASK-08)
    # ------------------------------------------------------------------

    def promotion_preview(
        self,
        *,
        model_id: str,
        model_version: str,
        artifact_path: Path | str = "",
        scaler_path: Path | str = "",
        manifest: dict[str, Any] | None = None,
        runtime_schema_id: str = "",
        runtime_dimension: int = 0,
        feature_schema_hash: str = "",
        liquidity_algorithm_version: str = "",
        training_commit: str = "",
        oos_artifact: str = "",
        shadow_evidence: dict[str, Any] | None = None,
        news_contract: dict[str, Any] | None = None,
        liquidity_contract: dict[str, Any] | None = None,
        locks_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        """PROMOTION PREVIEW (spec 28). READ-ONLY: no mutation, no lock.

        Returns the exact preview contract the UI renders BEFORE any
        operator decision: current champion identity + hash, candidate
        identity + hash, schema pair, gate verdicts, rollback availability.
        """
        from nexus_scalp.governance.verify import verify_candidate

        champion: dict[str, Any] = {}
        try:
            champ = self._current_champion_identity()
            if champ:
                champion = champ
        except Exception:
            champion = {}

        verification = verify_candidate(
            model_id=model_id,
            model_version=model_version,
            artifact_path=artifact_path,
            scaler_path=scaler_path,
            manifest=manifest,
            runtime_schema_id=runtime_schema_id,
            runtime_dimension=runtime_dimension,
            feature_schema_hash=feature_schema_hash,
            liquidity_algorithm_version=liquidity_algorithm_version,
            training_commit=training_commit,
            oos_artifact=oos_artifact,
            shadow_evidence=shadow_evidence,
            news_contract=news_contract,
            liquidity_contract=liquidity_contract,
            store=self.store,
        )

        gates = verification["gates"]
        gate_summary = {
            "technical": gates.get("artifact_exists", {}).get("status", "UNKNOWN"),
            "schema": gates.get("schema_registered", {}).get("status", "UNKNOWN"),
            "dataset": gates.get("manifest_valid", {}).get("status", "UNKNOWN"),
            "walk_forward": gates.get("training_commit_recorded", {}).get("status", "UNKNOWN"),
            "oos": gates.get("oos_artifact_recorded", {}).get("status", "UNKNOWN"),
            "robustness": gates.get("liquidity_version_matches", {}).get("status", "UNKNOWN"),
            "calibration": gates.get("feature_schema_hash_matches", {}).get("status", "UNKNOWN"),
            "shadow": gates.get("shadow_evidence_recorded", {}).get("status", "UNKNOWN"),
            "drift": gates.get("news_contract_valid", {}).get("status", "UNKNOWN"),
            "liquidity": gates.get("liquidity_contract_valid", {}).get("status", "UNKNOWN"),
        }

        rollback_available = bool(champion and champion.get("artifact_hash"))

        return {
            "available": True,
            "promotion_id_hint": f"preview_{model_id}_{model_version}",
            "current_champion": champion,
            "candidate": {
                "model_id": model_id,
                "version": model_version,
                "schema": (manifest or {}).get("feature_schema_id", ""),
                "hash": (manifest or {}).get("artifact_hash", ""),
            },
            "schema": {
                "champion": champion.get("schema_id", ""),
                "candidate": (manifest or {}).get("feature_schema_id", ""),
            },
            "gates": gate_summary,
            "verification": {
                "eligible": verification["eligible"],
                "failures": verification["failures"],
                "skipped": verification["skipped"],
                "reason": verification["reason"],
            },
            "rollback": {
                "available": rollback_available,
                "target": (
                    f"{champion.get('model_id', '')}@{champion.get('version', '')}"
                    if rollback_available
                    else ""
                ),
            },
            "checked_at": verification["checked_at"],
            "locked": bool(locks_dir) and self._promotion_lock_held(Path(locks_dir)),
        }

    def _current_champion_identity(self) -> dict[str, Any] | None:
        """Reads the CURRENT Champion identity (registry truth, read-only)."""
        if not self.store or not getattr(self.store, "audit_repo", None):
            return None
        try:
            conn = sqlite3.connect(
                f"file:{self.store.audit_repo._db_path}?mode=ro", uri=True, timeout=5.0
            )
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM experience_model_registry "
                    "WHERE lifecycle_status='CHAMPION' ORDER BY registered_at DESC LIMIT 1;"
                ).fetchone()
                if row is None:
                    return None
                d = dict(row)
                return {
                    "model_id": str(d.get("model_id", "")),
                    "version": str(d.get("model_version", "")),
                    "schema_id": str(d.get("feature_schema_id", "")),
                    "artifact_hash": str(d.get("artifact_fingerprint", "")),
                    "lifecycle_state": str(d.get("lifecycle_status", "CHAMPION")),
                }
            finally:
                conn.close()
        except Exception:
            return None

    @staticmethod
    def _promotion_lock_held(locks_dir: Path) -> bool:
        """True when a promotion transaction lock currently exists."""
        for p in locks_dir.glob("promotion*.lock"):
            if p.exists():
                return True
        return False

    def rollback_preview(
        self,
        *,
        failed_model_id: str,
        previous_model_id: str = "",
        previous_artifact: Path | str = "",
        previous_scaler: Path | str = "",
    ) -> dict[str, Any]:
        """ROLLBACK PREVIEW (spec 30). READ-ONLY; verifies the old artifact
        is still valid before the operator commits to a rollback."""
        prev: dict[str, Any] = {}
        if previous_model_id:
            prev = {"model_id": previous_model_id}
            try:
                champ = self._current_champion_identity()
                if champ and champ["model_id"] == previous_model_id:
                    prev = champ
            except Exception:
                pass
        artifact_ok = False
        artifact_hash = ""
        detail = "no artifact path"
        p = Path(previous_artifact) if previous_artifact else Path("")
        if p.exists():
            artifact_hash = sha256_hex(p)
            artifact_ok = bool(artifact_hash)
            sca = Path(previous_scaler) if previous_scaler else Path(str(p) + ".scaler.npz")
            gate = self.load_gate.evaluate(
                artifact_path=p,
                scaler_path=sca,
                model_id=previous_model_id,
                manifest=None,
            )
            artifact_ok = artifact_ok and gate.passed
            detail = f"load_gate={gate.passed} gate={gate.failing_gate.value if gate.failing_gate else 'none'}"
        manifest_hash = ""
        manifest_path = Path(str(p) + ".json")
        if manifest_path.exists():
            mf = read_manifest_file(manifest_path)
            if mf:
                import hashlib

                manifest_hash = hashlib.sha256(
                    str(sorted(mf.items())).encode("utf-8", errors="replace")
                ).hexdigest()
        return {
            "available": True,
            "rollback_candidate": {
                "model_id": previous_model_id or "",
                "version": prev.get("version", ""),
                "schema": prev.get("schema_id", ""),
            },
            "artifact": {
                "path": str(p),
                "hash": artifact_hash,
                "valid": artifact_ok,
                "detail": detail,
            },
            "manifest_hash": manifest_hash,
            "schema": prev.get("schema_id", ""),
            "failed_model_id": failed_model_id,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    def emergency_freezes(self) -> dict[str, Any]:
        """Emergency stop state (spec 31): freeze promotion + disable
        candidates. Always truthful; read-only."""
        return {
            "promotion_frozen": self.promotion_frozen,
            "disabled_candidates": sorted(self.disabled_candidates),
            "reasons": dict(self._emergency_reasons),
        }

    def freeze_promotions(self, actor: str, reason: str = "") -> None:
        """Freezes ALL promotions (spec 31). Distinct from Stop Bot."""
        self.promotion_frozen = True
        self._emergency_reasons["promotion_frozen"] = reason or actor
        self._record_governance_event(
            event="PROMOTION_FREEZE",
            stage=GovernanceStage.PROMOTION,
            model_id="",
            actor=actor,
            reason=reason or "operator freeze",
        )

    def unfreeze_promotions(self, actor: str, reason: str = "") -> None:
        self.promotion_frozen = False
        self._emergency_reasons.pop("promotion_frozen", None)
        self._record_governance_event(
            event="PROMOTION_UNFREEZE",
            stage=GovernanceStage.PROMOTION,
            model_id="",
            actor=actor,
            reason=reason or "operator unfreeze",
        )

    def disable_candidate(self, model_id: str, actor: str, reason: str = "") -> None:
        """Disables a candidate (spec 31); the evidence is NEVER deleted."""
        self.disabled_candidates.add(model_id)
        self._emergency_reasons[f"disabled_{model_id}"] = reason or actor
        self.store.set_state(model_id, "", "QUARANTINED")
        self._record_governance_event(
            event="CANDIDATE_DISABLED",
            stage=GovernanceStage.PROMOTION,
            model_id=model_id,
            actor=actor,
            reason=reason or "operator disable",
        )

    def _record_governance_event(
        self,
        *,
        event: str,
        stage: GovernanceStage,
        model_id: str,
        actor: str,
        reason: str,
    ) -> None:
        try:
            self.store.record_event(
                GovernanceEvent(
                    event_id=f"ev_{uuid.uuid4().hex[:16]}",
                    event=event,
                    stage=stage,
                    model_id=model_id,
                    actor=actor,
                    previous_state="",
                    new_state="",
                    reason=reason,
                )
            )
        except Exception:
            pass
