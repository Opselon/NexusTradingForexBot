"""ML Position Control Plane API (spec §13/§15/§26).

Backend truth only: lifecycle state, persisted config, ownership gate
status, and runtime tensor contract. No client-side active flags — the UI
renders whatever this returns.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from nexus_scalp.position_adviser.feature_schema import schema_contract

router = APIRouter(prefix="/api/position-ml", tags=["position-ml"])


class _Holder:
    lifecycle: Any = None


def _ml_lifecycle() -> Any:
    """The ML lifecycle bound to the REAL adviser service + settings store.

    Reuses AdviserSettingsStore (position_adviser.* keys) as the single
    authoritative persistence (§25 — no parallel state store) and the
    process-wide adviser service singleton. Built lazily on first use; the
    OrderManager's ownership gate is wired when the engine composes it.
    """
    if _Holder.lifecycle is None:
        from nexus_scalp.position_adviser.feature_schema import (
            POSITION_FEATURE_SCHEMA_VERSION,
        )
        from nexus_scalp.position_adviser.ml_lifecycle import (
            MLPositionControllerLifecycle,
        )
        from nexus_scalp.position_adviser.ownership import PositionOwnershipGate
        from nexus_scalp.position_adviser.settings_store import AdviserSettingsStore
        from nexus_scalp.web.position_adviser_routes import (
            _settings_database,
            get_position_adviser_service,
        )

        svc = get_position_adviser_service()
        lc = MLPositionControllerLifecycle(
            settings_service=AdviserSettingsStore(_settings_database()),
            gate=PositionOwnershipGate(),
            schema_version=POSITION_FEATURE_SCHEMA_VERSION,
        )
        # the adviser service's own state machine remains the model holder;
        # the lifecycle delegates load/unload to it so there is ONE runtime.
        lc.wire_model_runtime(
            loader=svc.load,
            unloader=svc.unload,
        )
        _Holder.lifecycle = lc
    return _Holder.lifecycle


def register_position_ml_routes(app: Any, lifecycle: Any | None = None) -> None:
    if lifecycle is not None:
        _Holder.lifecycle = lifecycle
    app.include_router(router)


@router.get("/status")
def route_ml_status() -> dict[str, Any]:
    """Full lifecycle + ownership + tensor-contract state (§26 contract)."""
    lc = _ml_lifecycle()
    gate = lc._gate
    contract = schema_contract()
    st = lc.status()
    return {
        "status": "OK",
        "lifecycle_state": st["lifecycle_state"],
        "restart_required": st["restart_required"],
        "last_error": st["last_error"],
        "controller_mode": st["controller_mode"],
        "model_version": st["loaded_model_id"] or st["persisted"].get("model_id", ""),
        "schema_version": contract["schema_version"],
        "tensor_shape": contract["shape"],
        "d": contract["d"],
        "sequence_length": 1,
        "dtype": contract["dtype"],
        "device": _device(),
        "parameter_count": _param_count(),
        "artifact_status": {
            "model_path": st["persisted"].get("model_path", ""),
            "scaler_path": st["persisted"].get("scaler_path", ""),
            "load_status": str(st["lifecycle_state"]),
            "load_timestamp": st["load_timestamp"],
            "last_inference_timestamp": st["last_inference_timestamp"],
        },
        "ownership": gate.status() if gate is not None else {},
        "persisted": st["persisted"],
    }


@router.get("/schema")
def route_ml_schema() -> dict[str, Any]:
    """The versioned feature contract (§12) — what the model was trained on."""
    return schema_contract()


@router.get("/ownership/audit")
def route_ml_ownership_audit(limit: int = 50) -> dict[str, Any]:
    lc = _ml_lifecycle()
    return {"status": "OK", "audit": lc._gate.audit_tail(limit)}


@router.post("/activate")
def route_ml_activate(req: dict[str, Any]) -> dict[str, Any]:
    """Persist + load + activate (§14). Returns restart_required when the
    runtime cannot hot-load (e.g. torch lazy-init mid-loop)."""
    lc = _ml_lifecycle()
    out = lc.activate(
        model_id=str(req.get("model_id", "")),
        model_path=str(req.get("model_path", "")),
        scaler_path=str(req.get("scaler_path", "")),
    )
    if out.get("status") == "OK" and lc.state.value == "RESTART_REQUIRED":
        out["restart_required"] = True
    return out


@router.post("/disable")
def route_ml_disable() -> dict[str, Any]:
    lc = _ml_lifecycle()
    return lc.disable()


# ---- lazy introspection helpers (torch may be absent in some deploys) ----


def _device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "unavailable"


def _param_count() -> int | None:
    try:
        import torch  # noqa: F401

        lc = _ml_lifecycle()
        model = getattr(lc, "_loaded_model", None)
        if model is None:
            return None
        return sum(p.numel() for p in model.parameters())
    except Exception:
        return None
