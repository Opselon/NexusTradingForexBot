"""ChampionSync — champion-registry truth synchronization.

P1 seam L11 (god-file decomposition, extraction 11 of the live-engine wave).
The champion-registry sync pair leaves ``application/live_engine.py``
verbatim (behavior-preserving extraction):

    _evaluate_champion_registry_sync : PURE decision (no I/O) computing the
        registry-repair action {NOOP|REPAIR|BOOTSTRAP} from the current
        registry row vs the serving artifact identity.
    _sync_champion_registry_state    : reads the registry + serving state and
        applies the evaluated action (governance-safe, idempotent).

Test contract preserved: the pure evaluator is invoked UNBOUND with
``self=None`` (see test_agent3_champion_registry_sync); the applier receives
the engine as the state surface.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live.champion_sync")


class ChampionSync:
    """Champion-registry truth sync (composition root: LiveEngine)."""

    def __init__(self, om: Any) -> None:
        self.om = om


    def evaluate_champion_registry_sync(
        _self: LiveEngine | None,
        *,
        current_row: dict[str, Any] | None,
        serving_artifact_path: str,
        serving_schema_id: str,
        serving_dimension: int,
        serving_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """Pure decision for _sync_champion_registry_state (no I/O).
    
        Returns {"action": NOOP|REPAIR|BOOTSTRAP, ...} describing exactly
        what the caller must do to make the registry truthful.
        """
        norm = lambda p: str(p or "").replace("\\\\", "/")  # noqa: E731
        serving_path = norm(serving_artifact_path)
        model_id = f"primary_scalp_{serving_schema_id}_{serving_dimension}d"
        base: dict[str, Any] = {
            "serving_path": serving_path,
            "serving_schema_id": serving_schema_id,
            "serving_dimension": int(serving_dimension),
            "new_champion_model_id": model_id,
        }
        if current_row is None:
            return {**base, "action": "BOOTSTRAP", "reason": "no champion row"}
    
        row_path = norm(current_row.get("artifact_path", ""))
        row_schema = str(current_row.get("feature_schema_id", "") or "")
        row_dim = int(current_row.get("feature_dimension", 0) or 0)
        contract_match = (
            row_path == serving_path
            and row_schema == serving_schema_id
            and row_dim == int(serving_dimension)
        )
        if contract_match:
            return {**base, "action": "NOOP", "reason": "already_truthful"}
    
        # Path matches but the CONTRACT is contradictory: the row claims
        # the serving artifact under the wrong schema/dimension. Repair =
        # demote the stale row to ARCHIVED and re-register truthfully.
        stale_model_id = str(current_row.get("model_id", "") or "")
        return {
            **base,
            "action": "REPAIR",
            "reason": "champion_row_contract_mismatch",
            "stale_row_model_id": stale_model_id,
            "stale_row_schema": row_schema,
            "stale_row_dimension": row_dim,
            "demote_stale_to": ModelStatus.ARCHIVED.value,
        }


    def sync_champion_registry_state(self) -> None:
        """Makes the registry truthful about the CURRENT Champion (spec 3)."""
        try:
            if self.om.governance_store is None:
                return
            champ = self.om.champion_manager.champion_or_none()
            if champ is None or not champ.artifact_hash:
                return
            from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry
    
            lifecycle = ModelLifecycleRegistry(
                audit_repo=self.om.audit, model_registry=self.om.model_registry
            )
            rows = lifecycle.list_models(status=ModelStatus.CHAMPION, limit=5)
            current = rows[0] if rows else None
            decision = self.om._evaluate_champion_registry_sync(
                None,
                current_row=current,
                serving_artifact_path=self.om.config.model.model_artifact_path,
                serving_schema_id=self.om.FEATURE_SCHEMA_ID,
                serving_dimension=self.om.FEATURE_DIM,
                serving_fingerprint=champ.artifact_hash,
            )
            action = decision.get("action")
            if action == "NOOP":
                return
            if action == "REPAIR":
                # Demote the contradictory CHAMPION row first (append-only:
                # ARCHIVED preserves history, never deletes evidence).
                stale_model_id = str(decision.get("stale_row_model_id", "") or "")
                stale_version = str(current.get("model_version", "") or "") if current else ""
                if stale_model_id and stale_version:
                    with contextlib.suppress(Exception):
                        lifecycle.set_status(
                            model_id=stale_model_id,
                            model_version=stale_version,
                            status=ModelStatus.ARCHIVED,
                            reason=(
                                "AGENT-3 registry truth repair: champion row "
                                "contract mismatch (declared "
                                f"{decision.get('stale_row_schema')}"
                                f"@{decision.get('stale_row_dimension')}D vs "
                                f"serving {self.om.FEATURE_SCHEMA_ID}"
                                f"@{self.om.FEATURE_DIM}D)"
                            ),
                        )
            self.om.model_registry.register_model(
                artifact_path=self.om.config.model.model_artifact_path,
                model_version=str(getattr(self.om.config.model, "feature_schema_version", "v1.0")),
                feature_schema_id=self.om.FEATURE_SCHEMA_ID,
                feature_dimension=self.om.FEATURE_DIM,
                config_version=str(getattr(self.om.runtime_config, "get_version", lambda: 0)()),
                replaced=False,
            )
            iid = f"{self.om.model_registry.current.model_role.lower()}_{self.om.FEATURE_SCHEMA_ID}_{self.om.FEATURE_DIM}d"
            try:
                lifecycle.set_status(
                    model_id=iid,
                    model_version=str(getattr(self.om.config.model, "feature_schema_version", "v1.0")),
                    status=ModelStatus.CHAMPION,
                    reason="registry truthfulness sync: live Champion row",
                )
            except Exception as e:
                logger.error("[MODEL_GOVERNANCE] champion registry sync failed", error=str(e))
            self.om.governance_store.record_event(
                GovernanceEvent(
                    event_id=f"ev_{uuid.uuid4().hex[:16]}",
                    event="REGISTRY_RECONCILED",
                    stage=GovernanceStage.REGISTRY,
                    model_id=self.om.champion_manager.model_id,
                    model_version=str(getattr(self.om.config.model, "feature_schema_version", "v1.0")),
                    schema_id=self.om.FEATURE_SCHEMA_ID,
                    reason="live Champion registry truthfulness correction",
                    payload={"artifact_path": self.om.config.model.model_artifact_path},
                )
            )
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] registry sync failed (isolated)", error=str(e))

