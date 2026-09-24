"""AI provider ecosystem API routes (ECOSYSTEM-001).

Backend is the source of truth (Section 61): every UI and CLI action goes
through these endpoints. No provider logic lives in the frontend or the CLI.

Convention follows the position-adviser lane: raw JSON, server-authoritative
status words, HTTP 4xx with a ``detail`` string the client surfaces verbatim.

Endpoints (all under /api/ai-providers)
    GET  /                          providers + activation + versions
    GET  /providers                 provider list with health (Section 19)
    GET  /providers/{id}/status     one provider's detail
    POST /providers/{id}/configure  save config (Sections 20, 27)
    POST /providers/{id}/enable     enable
    POST /providers/{id}/disable    disable
    POST /providers/{id}/test       test center (Section 22)
    GET  /providers/{id}/models     model listing (Section 21)
    POST /switch                    the switch workflow (Section 26)
    POST /activate                  set primary/secondary/fallback/mode
    GET  /activation                current activation
    POST /decision/evaluate         evaluate a position snapshot (Section 22)
    POST /decision/compare          side-by-side comparison (Section 24)
    GET  /decisions                 recent decisions (Section 23)
    GET  /decision/{id}             decision trace (Section 41)
    POST /export                    config export, secrets stripped (Section 48)
    POST /import                    config import, keys re-entered (Section 48)
    POST /reset                     reset provider settings (Section 54)
    GET  /restart-matrix            hot-reload vs restart (Section 28)
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from nexus_scalp.ai_providers.contract import PositionDecisionRequest
from nexus_scalp.ai_providers.orchestrator import ProviderOrchestrator
from nexus_scalp.ai_providers.registry import (
    ActivationState,
    ProviderRegistryStore,
)
from nexus_scalp.ai_providers.sample import SIMULATED_SAMPLE_REQUEST
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.settings.paths import settings_db_path
from nexus_scalp.settings.secret_store import SecureSecretStore

logger = get_logger("nexus_scalp.web.ai_providers_routes")

router = APIRouter(prefix="/api/ai-providers", tags=["ai-providers"])

_LOCK = threading.RLock()
_ORCHESTRATOR: ProviderOrchestrator | None = None


def _secret_store() -> SecureSecretStore:
    return SecureSecretStore()


def get_ai_provider_orchestrator() -> ProviderOrchestrator:
    """The canonical orchestrator shared by API, CLI and engine.

    Lazy: built on first use so the routes never break server startup when the
    settings DB is not yet provisioned. Same singleton semantics as the
    position-adviser service it sits beside.
    """
    global _ORCHESTRATOR  # noqa: PLW0603  # module-level singleton, mirrors the position-adviser lane
    with _LOCK:
        if _ORCHESTRATOR is None:
            _ORCHESTRATOR = ProviderOrchestrator(
                registry=ProviderRegistryStore(settings_db_path()),
                secret_store=_secret_store(),
            )
        return _ORCHESTRATOR


def _reset_orchestrator() -> None:
    """Drop the cached singleton (tests / explicit reset)."""
    global _ORCHESTRATOR  # noqa: PLW0603  # module-level singleton, mirrors the position-adviser lane
    with _LOCK:
        _ORCHESTRATOR = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ConfigureProviderRequest(BaseModel):
    endpoint: str | None = None
    model: str | None = None
    timeout: float | None = Field(default=None, ge=1.0, le=300.0)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    enabled: bool | None = None
    provider_name: str | None = None
    #: Secret VALUE. Written straight to the DPAPI store; never persisted in the
    #: provider config row, never echoed back (Section 37).
    api_key: str | None = None


class ActivationRequest(BaseModel):
    primary: str
    secondary: str | None = None
    fallback: str | None = None
    mode: str = "INTERNAL_ONLY"
    shadow: str | None = None


class SwitchRequest(ActivationRequest):
    """The switch flow (Section 26): same fields, preconditions enforced."""


class EvaluateRequest(BaseModel):
    snapshot: PositionDecisionRequest | None = None
    #: When true, use the built-in SIMULATED TEST DATA snapshot (Section 56).
    simulated: bool = True


class CompareRequest(BaseModel):
    snapshot: PositionDecisionRequest | None = None
    simulated: bool = True
    providers: list[str] | None = None


class ImportRequest(BaseModel):
    config: dict[str, Any]
    #: Keys are re-entered separately on import (Section 48).
    api_keys: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Provider management
# ---------------------------------------------------------------------------


@router.get("")
@router.get("/")
def route_index() -> dict[str, Any]:
    """Operator control state (Section 53): active provider, model, mode,
    fallback, shadow, health. No hidden switches."""
    return {"status": "OK", **get_ai_provider_orchestrator().to_public_dict()}


@router.get("/providers")
def route_providers() -> dict[str, Any]:
    """The provider table with live health (Section 19)."""
    return {
        "status": "OK",
        "providers": get_ai_provider_orchestrator().list_providers(),
    }


@router.get("/providers/{provider_id}/status")
def route_provider_status(provider_id: str) -> dict[str, Any]:
    orch = get_ai_provider_orchestrator()
    out = orch.provider_status(provider_id)
    if not out.get("available"):
        raise HTTPException(
            status_code=404, detail=f"provider {provider_id} not configured or disabled"
        )
    return {"status": "OK", **out}


@router.post("/providers/{provider_id}/configure")
def route_configure(provider_id: str, req: ConfigureProviderRequest) -> dict[str, Any]:
    """Save provider configuration (Sections 20, 27).

    The API key is written to the DPAPI store and referenced by name; it is
    never stored in the config row and never returned by any endpoint.
    """
    orch = get_ai_provider_orchestrator()
    if req.api_key:
        try:
            store = _secret_store()
            store.set_secret(f"ai_provider_{provider_id}_key", req.api_key)
        except Exception as exc:
            logger.error("[AI-PROV] failed to store secret for %s: %s", provider_id, exc)
            raise HTTPException(
                status_code=500, detail="failed to store credential securely"
            ) from exc
    values: dict[str, Any] = req.model_dump(exclude_none=True)
    values.pop("api_key", None)
    if req.api_key:
        values["secret_name"] = f"ai_provider_{provider_id}_key"
    ok = orch.configure_provider(provider_id, values, actor="ui")
    if not ok:
        raise HTTPException(status_code=400, detail="configuration validation failed")
    return {
        "status": "OK",
        "provider_id": provider_id,
        "restart_requirement": ProviderOrchestrator.restart_requirement("endpoint"),
    }


@router.post("/providers/{provider_id}/enable")
def route_enable(provider_id: str) -> dict[str, Any]:
    orch = get_ai_provider_orchestrator()
    if not orch.configure_provider(provider_id, {"enabled": True}, actor="ui"):
        raise HTTPException(status_code=400, detail="cannot enable provider")
    return {"status": "OK", "provider_id": provider_id, "enabled": True}


@router.post("/providers/{provider_id}/disable")
def route_disable(provider_id: str) -> dict[str, Any]:
    orch = get_ai_provider_orchestrator()
    if not orch.configure_provider(provider_id, {"enabled": False}, actor="ui"):
        raise HTTPException(status_code=400, detail="cannot disable provider")
    return {"status": "OK", "provider_id": provider_id, "enabled": False}


@router.post("/providers/{provider_id}/test")
def route_test(provider_id: str) -> dict[str, Any]:
    """Test Center entry point (Section 22). Never triggers a real trade."""
    result = get_ai_provider_orchestrator().test_provider(provider_id)
    return {
        "status": "PASS" if result.passed else "FAIL",
        "passed": result.passed,
        "stage": result.stage,
        "detail": result.detail,
        "latency_ms": result.latency_ms,
        "raw_response": result.raw_response,
        "normalized_response": result.normalized_response,
        "error": result.error,
    }


@router.get("/providers/{provider_id}/models")
def route_models(provider_id: str) -> dict[str, Any]:
    """Model listing (Section 21). Empty when unsupported."""
    return {
        "status": "OK",
        "provider_id": provider_id,
        "models": get_ai_provider_orchestrator().list_models(provider_id),
    }


# ---------------------------------------------------------------------------
# Activation / switching
# ---------------------------------------------------------------------------


@router.get("/activation")
def route_activation() -> dict[str, Any]:
    orch = get_ai_provider_orchestrator()
    return {"status": "OK", "activation": orch.to_public_dict()}


@router.post("/activate")
def route_activate(req: ActivationRequest) -> dict[str, Any]:
    """Set primary/secondary/fallback/mode (Section 14). No preconditions: this
    is the raw setter. Use /switch when preconditions must be enforced."""
    orch = get_ai_provider_orchestrator()
    state = ActivationState(
        primary_provider=req.primary,
        secondary_provider=req.secondary,
        fallback_provider=req.fallback,
        decision_mode=req.mode,
        shadow_provider=req.shadow,
    )
    if not orch.set_activation(state, actor="ui"):
        raise HTTPException(status_code=400, detail="activation rejected; see server logs")
    return {"status": "OK", **state.to_public_dict()}


@router.post("/switch")
def route_switch(req: SwitchRequest) -> dict[str, Any]:
    """The explicit switch workflow (Sections 26, 43).

    Preconditions are enforced: provider healthy, credentials valid, test
    successful. A failed precondition switches nothing.
    """
    orch = get_ai_provider_orchestrator()
    result = orch.switch(
        primary=req.primary,
        secondary=req.secondary,
        fallback=req.fallback,
        mode=req.mode,
        shadow=req.shadow,
        actor="ui",
    )
    if not result.get("switched"):
        raise HTTPException(
            status_code=412, detail=result.get("reason", "switch preconditions failed")
        )
    return {"status": "OK", **result}


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def _resolve_snapshot(req: EvaluateRequest | CompareRequest) -> PositionDecisionRequest:
    if req.snapshot is not None:
        return req.snapshot
    if req.simulated:
        return SIMULATED_SAMPLE_REQUEST
    raise HTTPException(status_code=400, detail="no snapshot supplied and simulated=false")


@router.post("/decision/evaluate")
def route_evaluate(req: EvaluateRequest) -> dict[str, Any]:
    """Evaluate one position snapshot (Section 22).

    A decision from this endpoint is advisory evidence, never an order. When the
    snapshot is simulated, the outcome is tagged ``is_test_data``.
    """
    snapshot = _resolve_snapshot(req)
    outcome = get_ai_provider_orchestrator().evaluate_position(snapshot)
    return {"status": "OK", "decision": outcome.to_dict()}


@router.post("/decision/compare")
def route_compare(req: CompareRequest) -> dict[str, Any]:
    """Side-by-side provider comparison over one snapshot (Section 24).

    For analysis, not automatic voting (Section 47).
    """
    orch = get_ai_provider_orchestrator()
    snapshot = _resolve_snapshot(req)
    wanted = req.providers or list(ADAPTER_TYPES_LAZY())
    per_provider: dict[str, Any] = {}
    for pid in wanted:
        adapter = orch._adapter(pid)
        if adapter is None:
            per_provider[pid] = {"available": False, "reason": "disabled or not configured"}
            continue
        try:
            resp = adapter.evaluate_position(snapshot)
            per_provider[pid] = {
                "available": True,
                "action": resp.decision.action.value,
                "p_hold": resp.decision.p_hold,
                "p_close": resp.decision.p_close,
                "p_reduce": resp.decision.p_reduce,
                "expected_remaining_r": resp.decision.expected_remaining_r,
                "uncertainty": resp.decision.uncertainty,
                "tp": resp.tp.model_dump(),
                "sl": resp.sl.model_dump(),
                "latency_ms": resp.latency_ms,
                "model": resp.model,
                "regime": resp.decision.regime_change_probability,
            }
        except Exception as exc:
            per_provider[pid] = {"available": False, "reason": str(exc)[:200]}
    return {"status": "OK", "providers": per_provider, "snapshot_id": snapshot.provider_request_id}


def ADAPTER_TYPES_LAZY() -> list[str]:
    from nexus_scalp.ai_providers.adapters import ADAPTER_TYPES

    return list(ADAPTER_TYPES)


@router.get("/decisions")
def route_decisions(limit: int = 50) -> dict[str, Any]:
    """Recent decisions for the live AI panel (Section 23)."""
    return {"status": "OK", "decisions": get_ai_provider_orchestrator().history(limit)}


@router.get("/decision/{decision_id}")
def route_decision_detail(decision_id: str) -> dict[str, Any]:
    """Decision trace (Section 41)."""
    for d in get_ai_provider_orchestrator().history(200):
        if d.get("decision_id") == decision_id:
            return {"status": "OK", "decision": d}
    raise HTTPException(status_code=404, detail="decision not found")


# ---------------------------------------------------------------------------
# Import / export / reset (Sections 48, 54)
# ---------------------------------------------------------------------------


@router.post("/export")
def route_export() -> dict[str, Any]:
    """Export provider configuration. NEVER exports API keys (Section 48)."""
    orch = get_ai_provider_orchestrator()
    cfgs = []
    for pid in orch.list_providers():
        row = orch._registry.get(pid)
        if row is not None:
            cfgs.append(row.to_public_dict())
    return {
        "status": "OK",
        "exported_at": _now_iso(),
        "providers": cfgs,
        "activation": (
            orch._registry.get_activation() or ActivationState(primary_provider="internal_nse_ml")
        ).to_public_dict(),
        "note": "API keys are never exported; re-enter credentials after import.",
    }


@router.post("/import")
def route_import(req: ImportRequest) -> dict[str, Any]:
    """Import provider configuration (Section 48).

    Imported credentials must be separately entered: the imported blob carries
    no keys, and any key supplied here goes straight to the DPAPI store.
    """
    orch = get_ai_provider_orchestrator()
    imported: list[str] = []
    for row in req.config.get("providers", []):
        pid = row.get("provider_id")
        if not pid:
            continue
        values = {
            k: v
            for k, v in row.items()
            if k in ("endpoint", "model", "timeout", "max_retries", "provider_name")
        }
        if values:
            orch.configure_provider(pid, values, actor="import")
        key = (req.api_keys or {}).get(pid)
        if key:
            _secret_store().set_secret(f"ai_provider_{pid}_key", key)
            orch.configure_provider(pid, {"secret_name": f"ai_provider_{pid}_key"}, actor="import")
        imported.append(pid)
    act = req.config.get("activation")
    if isinstance(act, dict) and act.get("primary"):
        orch.set_activation(
            ActivationState(
                primary_provider=act["primary"],
                secondary_provider=act.get("secondary"),
                fallback_provider=act.get("fallback"),
                decision_mode=act.get("mode", "INTERNAL_ONLY"),
                shadow_provider=act.get("shadow"),
            ),
            actor="import",
        )
    return {"status": "OK", "imported": imported}


@router.post("/reset")
def route_reset() -> dict[str, Any]:
    """Reset provider configuration only (Section 54).

    Deliberately distinct from resetting ALL NSE settings: this touches provider
    rows and activation only, and never deletes user models, datasets, trades or
    account history.
    """
    orch = get_ai_provider_orchestrator()
    removed: list[str] = []
    for pid in list(ADAPTER_TYPES_LAZY()):
        if orch._registry.delete(pid):
            removed.append(pid)
    _reset_orchestrator()
    return {
        "status": "OK",
        "removed_providers": removed,
        "note": "Provider settings only. Models, datasets, trades and account history untouched.",
    }


@router.get("/restart-matrix")
def route_restart_matrix() -> dict[str, Any]:
    """Hot-reload vs restart classification (Sections 28, 66)."""
    fields_ = [
        "endpoint",
        "model",
        "timeout",
        "max_retries",
        "enabled",
        "provider_name",
        "secret_name",
        "primary",
        "secondary",
        "fallback",
        "mode",
        "shadow",
    ]
    return {
        "status": "OK",
        "matrix": {f: ProviderOrchestrator.restart_requirement(f) for f in fields_},
    }


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def register_ai_providers_routes(app: Any) -> None:
    """Mount the router. Called from web/server.py (additive, no reordering)."""
    app.include_router(router)
