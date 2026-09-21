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

import contextlib
import os
import uuid
from typing import TYPE_CHECKING, Any

from nexus_scalp.governance.models import GovernanceEvent, GovernanceStage
from nexus_scalp.model_lifecycle.models import ModelStatus
from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:  # import-cycle breaker
    from nexus_scalp.application.live_engine import LiveEngine

logger = get_logger("nexus_scalp.application.live.champion_sync")


class ChampionSync:
    """Champion-registry truth sync (composition root: LiveEngine).

    UNBOUND-DELEGATION CONTRACT: methods are invoked as
    ``ChampionSync.method(engine, ...)`` — ``self`` IS the LiveEngine.
    The structural declaration below declares the engine surface these
    methods touch so mypy can type the seam (no runtime import cycle).
    """

    if TYPE_CHECKING:
        governance_store: Any
        champion_manager: Any
        audit: Any
        model_registry: Any
        config: Any
        FEATURE_SCHEMA_ID: str
        FEATURE_DIM: int
        runtime_config: Any
        _bundle_lock: Any
        _active_model_registered: bool
        trainer: Any
        _rolling_feature_records: list

    # Stateless mixin (caller contract): this class is never constructed.
    # LiveEngine invokes these methods UNBOUND with itself as the state
    # surface — ChampionSync.sync_champion_registry_state(engine) — exactly
    # as documented in the module docstring and exercised by
    # tests/unit/test_agent3_champion_registry_sync.py. Storing ``self.om``
    # would be shadow state nothing reads; every access below consumes the
    # engine attributes (governance_store, champion_manager, audit, ...)
    # directly off the ``self`` the caller passes.

    @staticmethod
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

        def canon(p: str) -> str:
            # BUG-xxx: rows written by older boots may store a RELATIVE
            # artifact path (e.g. 'artifacts/models/...'), while the live
            # sync passes the absolute resolved path. Compare canonical
            # absolute form so a truthful row is never declared
            # path-mismatched over a slash/case/relative spelling
            # difference. Relative paths anchor to the repo-root workspace
            # when resolvable (the engine's convention), NOT the raw CWD.
            q = norm(p)
            if not q:
                return ""
            from pathlib import Path

            path = Path(q)
            if not path.is_absolute():
                _repo_root = Path(__file__).resolve().parents[4]
                cand = _repo_root / path
                if cand.exists():
                    path = cand
            return os.path.normcase(str(path.resolve()))

        serving_path = canon(serving_artifact_path)
        model_id = f"primary_scalp_{serving_schema_id}_{serving_dimension}d"
        base: dict[str, Any] = {
            "serving_path": serving_path,
            "serving_schema_id": serving_schema_id,
            "serving_dimension": int(serving_dimension),
            "new_champion_model_id": model_id,
        }
        if current_row is None:
            return {**base, "action": "BOOTSTRAP", "reason": "no champion row"}

        row_path = canon(current_row.get("artifact_path", ""))
        row_schema = str(current_row.get("feature_schema_id", "") or "")
        row_dim = int(current_row.get("feature_dimension", 0) or 0)
        path_match = row_path == serving_path
        contract_match = (
            path_match and row_schema == serving_schema_id and row_dim == int(serving_dimension)
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

    def sync_champion_registry_state(self: Any) -> None:
        """Makes the registry truthful about the CURRENT Champion (spec 3).

        BUG-xxx: the serving contract is the LOADED BUNDLE's effective
        schema/dimension (same authoritative accessors _register_active_model
        uses), NOT the class-level bootstrap defaults. When a 70D scalp_v3
        bundle serves, reading the class defaults (scalp_v1/50D) made every
        boot REPAIR (demote) the truthful champion row and re-register a
        FALSE scalp_v1@50D row — the registry churned 70d↔50d every start.
        """
        try:
            if self.governance_store is None:
                return
            champ = self.champion_manager.champion_or_none()
            if champ is None or not champ.artifact_hash:
                return
            serving_schema_id = str(self.effective_feature_schema_id)
            serving_dimension = int(self.effective_feature_dim)
            from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry

            lifecycle = ModelLifecycleRegistry(
                audit_repo=self.audit, model_registry=self.model_registry
            )
            rows = lifecycle.list_models(status=ModelStatus.CHAMPION, limit=5)
            current = rows[0] if rows else None
            decision = ChampionSync.evaluate_champion_registry_sync(
                None,
                current_row=current,
                serving_artifact_path=self.config.model.model_artifact_path,
                serving_schema_id=serving_schema_id,
                serving_dimension=serving_dimension,
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
                                f"serving {serving_schema_id}"
                                f"@{serving_dimension}D)"
                            ),
                        )
            self.model_registry.register_model(
                artifact_path=self.config.model.model_artifact_path,
                model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                feature_schema_id=serving_schema_id,
                feature_dimension=serving_dimension,
                config_version=str(getattr(self.runtime_config, "get_version", lambda: 0)()),
                replaced=False,
            )
            iid = f"{self.model_registry.current.model_role.lower()}_{serving_schema_id}_{serving_dimension}d"
            try:
                lifecycle.set_status(
                    model_id=iid,
                    model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                    status=ModelStatus.CHAMPION,
                    reason="registry truthfulness sync: live Champion row",
                )
            except Exception as e:
                logger.error("[MODEL_GOVERNANCE] champion registry sync failed", error=str(e))
            self.governance_store.record_event(
                GovernanceEvent(
                    event_id=f"ev_{uuid.uuid4().hex[:16]}",
                    event="REGISTRY_RECONCILED",
                    stage=GovernanceStage.REGISTRY,
                    model_id=self.champion_manager.model_id,
                    model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                    schema_id=serving_schema_id,
                    reason="live Champion registry truthfulness correction",
                    payload={"artifact_path": self.config.model.model_artifact_path},
                )
            )
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] registry sync failed (isolated)", error=str(e))
