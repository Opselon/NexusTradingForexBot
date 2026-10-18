"""ML Position Control Plane API (spec §13/§15/§26).

Backend truth only: lifecycle state, persisted config, ownership gate
status, and runtime tensor contract. No client-side active flags — the UI
renders whatever this returns.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from nexus_scalp.position_adviser.feature_schema import schema_contract
from nexus_scalp.web.errors import new_request_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/position-ml", tags=["position-ml"])

# ---------------------------------------------------------------------------
# Public failure labels (CWE-209 / CWE-497 — CodeQL #1178/#1179)
# ---------------------------------------------------------------------------
# The lifecycle records failures as ``f"MODEL_LOAD_FAILED: {exc}"``. The
# interpolated exception text can carry filesystem paths, internal service
# names and source locations, so it must never reach an HTTP response. These
# are the *only* values the public surface may emit for a failure; the raw
# text is logged server-side by ``_log_lifecycle_failure`` below.
_FAILURE_CODES = (
    "MODEL_LOAD_FAILED",
    "MODEL_LOAD_REJECTED",
    "MODEL_LOAD_FAILED_ON_RESTART",
)


def _public_error_code(raw: str | None) -> str:
    """Map an internal lifecycle error string to a stable public label.

    Returns a value from ``_FAILURE_CODES`` (or ``FAILED``) without ever
    propagating the raw exception text, so the response body carries no
    attacker-reachable internals.
    """
    if not raw:
        return ""
    for code in _FAILURE_CODES:
        if code in raw:
            return code
    return "FAILED"


def _log_lifecycle_failure(endpoint: str, raw: str | None) -> str:
    """Log the real failure detail with a correlation id; return that id.

    This is the only place the raw error text is written. Responses reference
    the request id so an operator can find the full record in the logs.
    """
    request_id = new_request_id()
    logger.error(
        "[WEB_ERROR] endpoint=%s request_id=%s event=ML_LIFECYCLE_FAILED detail=%s",
        endpoint,
        request_id,
        (raw or "")[:500],
    )
    return request_id


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
    # Failure detail may contain exception text (CWE-209): keep the stable
    # lifecycle_state public and route the raw text to the logs only.
    raw_error = st.get("last_error") or ""
    if raw_error:
        _log_lifecycle_failure("/api/position-ml/status", raw_error)
    return {
        "status": "OK",
        "lifecycle_state": st["lifecycle_state"],
        "restart_required": st["restart_required"],
        "last_error": _public_error_code(raw_error),
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
    if out.get("status") == "OK":
        # Never return the lifecycle's dict object itself: it carries a
        # "reason" element built from exception text (CWE-209). Rebuild the
        # response from the untainted keys only.
        resp: dict[str, Any] = {
            "status": "OK",
            "state": out.get("state", ""),
            "model_id": out.get("model_id", ""),
        }
        if lc.state.value == "RESTART_REQUIRED":
            resp["restart_required"] = True
        return resp
    if out.get("status") == "FAILED":
        # The lifecycle's "reason" is built from exception text (CWE-209):
        # the response is rebuilt from stable values only, so nothing leaks;
        # the real detail is logged under a request id the client can quote.
        raw_reason = out.get("reason", "")
        request_id = _log_lifecycle_failure("/api/position-ml/activate", raw_reason)
        return {
            "status": "FAILED",
            "error_code": _public_error_code(raw_reason),
            "request_id": request_id,
        }
    return {"status": str(out.get("status", "FAILED"))}


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
