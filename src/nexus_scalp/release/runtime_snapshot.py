"""Canonical runtime snapshot builder (CHG-0043, TASK-RUNTIME-TRUTH).

ONE function, ``build_runtime_snapshot()``, assembles the truthful runtime
identity consumed by ``nexus version``, ``nexus health``, ``nexus doctor``
and the Web API/UI. This is deliberately NOT a second source of truth: every
section reads the pre-existing authoritative source and merely normalizes
the vocabulary via ``state_taxonomy``.

Properties:
    * read-only: never mutates config, DB, artifacts or the champion;
    * offline: no network calls (update truth arrives via the update
      orchestrator's own status; absence -> UNKNOWN, never fabricated);
    * failure-isolated: a broken section degrades to UNKNOWN/UNAVAILABLE,
      never raises;
    * identity-only: model identity is READ from the artifact metadata /
      tensor shapes; no padding, no truncation, no compatibility verdicts
      here (that remains ``features.liquidity_runtime`` / model contract).

Truth chain honored (FEATURE ENABLED != FEATURE ACTIVE):

    registry selection -> resolved artifact -> loaded artifact ->
    serving bundle -> inference

Only the LOADED/configured bundle defines the effective feature contract
(live_engine.effective_feature_dim semantics); the registry champion row is
reported alongside it so REGISTERED-vs-SERVING divergence is visible.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.release import paths
from nexus_scalp.release.metadata import get_version_info

#: Snapshot cache TTL (seconds). Identity facts change on restart/promotion;
#: 30s cannot hide a real drift while keeping repeated CLI/UX calls cheap.
_SNAPSHOT_TTL_SECONDS = 30.0

_cache_state: dict[str, Any] = {}


def invalidate_snapshot_cache() -> None:
    """Force the next ``build_runtime_snapshot()`` to recompute."""
    _cache_state.clear()


def _safe(fn: Any, default: Any) -> Any:
    """Run ``fn``; on any exception return ``default`` (failure isolation)."""
    try:
        return fn()
    except Exception:
        return default


def _feature_contract_section() -> dict[str, Any]:
    """Canonical 70D contract identity (read-only view of the SSoT module)."""
    from nexus_scalp.release.state_taxonomy import UNKNOWN

    def _read() -> dict[str, Any]:
        from nexus_scalp.features.schema_contract import (
            DIMENSION,
            SCHEMA_ID,
            feature_schema_hash,
        )

        return {
            "schema_id": str(SCHEMA_ID),
            "dimension": int(DIMENSION),
            "hash_prefix": str(feature_schema_hash())[:16],
        }

    out = _safe(_read, None)
    if out is None:
        return {"schema_id": UNKNOWN, "dimension": 0, "hash_prefix": ""}
    return out


def _load_config() -> tuple[Any | None, str | None]:
    """Minimal config read mirroring release/health.py semantics (no engine)."""
    from nexus_scalp.release.state_taxonomy import UNKNOWN

    def _read() -> Any:
        from nexus_scalp.configuration.config import AppConfig

        p = paths.get_user_config_path()
        if not Path(p).exists():
            return False
        return AppConfig.load_from_yaml(p)

    cfg = _safe(_read, None)
    if cfg is None:
        return None, UNKNOWN
    if cfg is False:
        return None, "NOT_CONFIGURED"
    return cfg, None


def _resolve_configured_artifact(cfg: Any) -> dict[str, Any]:
    """Resolve the configured model_artifact_path to identity facts.

    Mirrors health.check_model resolution (relative -> workspace) and reads
    metadata + first-layer width from the artifact. Read-only; torch load
    failures degrade to metadata-present-but-unreadable, never raise.
    """
    from nexus_scalp.release.state_taxonomy import NOT_APPLICABLE, UNKNOWN

    out: dict[str, Any] = {
        "configured_artifact_path": None,
        "artifact_present": False,
        "source": UNKNOWN,
        "schema_id": UNKNOWN,
        "dimension": None,
        "tensor_input_dimension": None,
        "reason": None,
    }
    if cfg is None:
        out["source"] = NOT_APPLICABLE if cfg is None else UNKNOWN
        out["reason"] = "config unavailable"
        return out
    raw = getattr(getattr(cfg, "model", None), "model_artifact_path", None)
    out["configured_artifact_path"] = str(raw) if raw else None
    if not raw:
        out["reason"] = "model_artifact_path not set"
        return out
    p = Path(raw)
    if not p.is_absolute():
        p = paths.get_runtime_workspace() / p
    if not p.exists():
        out["reason"] = f"artifact file not found: {p}"
        return out
    out["artifact_present"] = True
    out["source"] = "CONFIGURED_ARTIFACT"

    def _probe() -> tuple[str | None, int | None, int | None]:
        import torch

        sd = torch.load(p, map_location="cpu", weights_only=True)
        meta: dict[str, Any] = {}
        dim: int | None = None
        tensor_in: int | None = None
        if isinstance(sd, dict):
            meta = sd.get("metadata") or sd.get("model_metadata") or {}
            state = sd.get("state_dict", sd) if isinstance(sd, dict) else sd
            first_w = next(
                (v for k, v in state.items() if "weight" in k and hasattr(v, "shape")),
                None,
            )
            if first_w is not None and len(first_w.shape) >= 2:
                tensor_in = int(first_w.shape[-1])
        dim = (
            meta.get("dimension")
            or meta.get("feature_dimension")
            or meta.get("feature_schema_dimension")
        )
        schema_id = (
            meta.get("schema_id")
            or meta.get("feature_schema_id")
            or meta.get("feature_schema_id_override")
        )
        return schema_id, (int(dim) if dim else None), tensor_in

    probe = _safe(_probe, None)
    if probe is None:
        out["reason"] = "artifact present but not introspectable"
        return out
    schema_id, dim, tensor_in = probe
    out["schema_id"] = str(schema_id) if schema_id else UNKNOWN
    out["dimension"] = dim
    out["tensor_input_dimension"] = tensor_in
    out["reason"] = None
    return out


def _registry_champion_section() -> dict[str, Any]:
    """Registry champion row (READ-ONLY; champion is never mutated here)."""
    from nexus_scalp.release.state_taxonomy import UNKNOWN

    def _read() -> dict[str, Any] | None:
        from nexus_scalp.adapters.database.audit_repository import AuditRepository
        from nexus_scalp.experience.provenance import ModelRegistry
        from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry
        from nexus_scalp.release import paths as _paths

        # BUG-196: this is a READ-ONLY identity probe on the version/doctor
        # --json path. Constructing a writable AuditRepository() with the
        # relative default created artifacts/audit.db (plus WAL chatter on
        # stdout) in whatever CWD the CLI ran from. An absent audit.db means
        # the engine never ran here: champion is NOT_INITIALIZED, and the
        # probe must not conjure a database into existence.
        if not (_paths.get_runtime_workspace() / "artifacts" / "audit.db").exists():
            return None

        repo = AuditRepository()
        reg = ModelLifecycleRegistry(audit_repo=repo, model_registry=ModelRegistry(audit_repo=repo))
        return reg.champion()

    row = _safe(_read, None)
    if not row:
        return {
            "available": False,
            "model_id": None,
            "model_version": None,
            "feature_schema_id": None,
            "feature_dimension": None,
            "staleness": UNKNOWN,
        }
    return {
        "available": True,
        "model_id": row.get("model_id"),
        "model_version": row.get("model_version"),
        "feature_schema_id": row.get("feature_schema_id"),
        "feature_dimension": row.get("feature_dimension"),
        # Registry rows do not carry a live artifact fingerprint; freshness
        # therefore stays UNKNOWN rather than a fabricated verdict.
        "staleness": UNKNOWN,
    }


def _feature_activation_block(cfg: Any, serving_dim: int | None) -> dict[str, Any]:
    """Feature ENABLED vs ACTIVE truth (forensic evidence, CHG-0043).

    The 70D assembly path (live_engine._build_live_feature_vector) is the
    ONLY route by which news/liquidity blocks enter the live tensor; the
    ``liquidity_features_enabled`` flag alone never changes the tensor.
    """
    from nexus_scalp.release.state_taxonomy import (
        ACTIVE,
        DISABLED,
        ENABLED,
        NOT_CONFIGURED,
        UNKNOWN,
    )

    is_70d = serving_dim == 70
    liq_enabled = (
        bool(getattr(getattr(cfg, "model", None), "liquidity_features_enabled", False))
        if cfg is not None
        else False
    )
    news_cfg = getattr(cfg, "news", None) if cfg is not None else None
    news_enabled = bool(getattr(news_cfg, "enabled", False)) if news_cfg else False

    def _entry(enabled: bool, configured: bool, block: str) -> dict[str, Any]:
        if not configured:
            state = NOT_CONFIGURED
            reason = "no configuration section recorded"
        elif enabled and is_70d:
            state = ACTIVE
            reason = f"active: 70D bundle loaded — indices {block} live"
        elif enabled:
            state = ENABLED
            reason = (
                "inactive: serving bundle is not a 70D scalp_v3 artifact "
                "(this block only enters the tensor through the 70D assembly path)"
            )
        else:
            state = DISABLED
            reason = "configured off (operator choice)"
        return {"state": state, "contributes_dimension": 10, "reason": reason}

    return {
        "base": {
            "state": ACTIVE,
            "contributes_dimension": 50,
            "reason": "base contract always present (indices 0..49)",
        },
        "news": _entry(news_enabled, news_cfg is not None, "50..59"),
        "liquidity": _entry(liq_enabled, cfg is not None, "60..69"),
        "tensor_dimension": serving_dim if serving_dim else UNKNOWN,
        "assembly_note": (
            "FEATURE ENABLED != FEATURE ACTIVE: enabled features enter the "
            "live tensor ONLY via the loaded bundle's 70D assembly path "
            "(live_engine._build_live_feature_vector)"
        ),
    }


def _model_section(cfg: Any) -> dict[str, Any]:
    """Serving-model identity + registered-vs-serving classification."""
    from nexus_scalp.release.state_taxonomy import UNKNOWN

    artifact = _resolve_configured_artifact(cfg)
    registry = _registry_champion_section()
    serving_key = (artifact.get("schema_id"), artifact.get("dimension"))
    registry_key = (
        registry.get("feature_schema_id"),
        registry.get("feature_dimension"),
    )
    if registry.get("available") and artifact.get("artifact_present"):
        if None in serving_key or None in registry_key or UNKNOWN in serving_key:
            alignment = UNKNOWN
        elif serving_key == registry_key:
            alignment = "ALIGNED"
        else:
            alignment = "MISMATCH_REGISTERED_VS_SERVING"
    else:
        alignment = UNKNOWN
    return {
        "configured_artifact": artifact,
        "registry_champion": registry,
        "alignment": alignment,
    }


def _database_section() -> dict[str, Any]:
    """Per-domain migration state + capability classification (read-only)."""
    from nexus_scalp.release.state_taxonomy import (
        NOT_APPLICABLE,
        NOT_INITIALIZED,
    )

    db_versions = _safe(
        lambda: __import__(
            "nexus_scalp.release.versioning", fromlist=["default_db_versions_provider"]
        ).default_db_versions_provider(),
        {},
    )
    ws = paths.get_runtime_workspace()
    audit_db = ws / "artifacts" / "audit.db"
    news_db = ws / "artifacts" / "news.db"
    return {
        "schema_versions": db_versions or {},
        "capability": {
            "audit": "AVAILABLE" if audit_db.exists() else NOT_INITIALIZED,
            "news": "AVAILABLE" if news_db.exists() else NOT_INITIALIZED,
            # Shadow tables are lazily created by design (shadow/store.py
            # ensure_schema) — absence is NOT a defect.
            "shadow_tables": NOT_INITIALIZED,
            "optional_feature_tables": NOT_APPLICABLE,
        },
    }


def _update_section() -> dict[str, Any]:
    """Update awareness placeholder (populated by the release status layer).

    Health/identity NEVER depend on network; without a completed check the
    truthful status is UNKNOWN.
    """
    from nexus_scalp.release.state_taxonomy import UNKNOWN

    return {"update_status": UNKNOWN}


def _runtime_mode_section(cfg: Any) -> dict[str, Any]:
    from nexus_scalp.release.state_taxonomy import UNKNOWN

    mode = getattr(getattr(cfg, "execution", None), "mode", None) if cfg else None
    mode_val = str(getattr(mode, "value", mode) or UNKNOWN)
    return {
        "configured_mode": mode_val,
        "execution_note": (
            "LIVE sends orders via the broker; PAPER/SIMULATION do not "
            "(paper fills are simulated); SHADOW records parallel decisions "
            "without execution"
        ),
    }


def build_runtime_snapshot(include_update: bool = True) -> dict[str, Any]:
    """The ONE canonical runtime snapshot (see module docstring)."""
    now = time.monotonic()
    cached_entry = _cache_state.get("snapshot")
    cached_at = _cache_state.get("at")
    if (
        cached_entry is not None
        and cached_at is not None
        and (now - cached_at) < _SNAPSHOT_TTL_SECONDS
    ):
        cached = dict(cached_entry)
        if include_update:
            return cached
        cached.pop("update", None)
        return cached

    cfg, _cfg_err = _load_config()
    artifact = _resolve_configured_artifact(cfg)
    serving_dim = artifact.get("tensor_input_dimension") or artifact.get("dimension")
    snapshot: dict[str, Any] = {
        "identity": get_version_info(),
        "feature_contract": _feature_contract_section(),
        "model": _model_section(cfg),
        "feature_activation": _feature_activation_block(cfg, serving_dim),
        "database": _database_section(),
        "update": _update_section(),
        "runtime_mode": _runtime_mode_section(cfg),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    _cache_state["snapshot"] = snapshot
    _cache_state["at"] = now
    out = dict(snapshot)
    if not include_update:
        out.pop("update", None)
    return out
