"""API v1 — RUNTIME domain: /mode/validate + /mode/preview (pure functions, no apply).

Sources verified in docs/api/API_PLATFORM_V1.md §7 (runtime.py, capabilities 12-13).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from nexus_scalp.web.api_v1.common import engine_or_503, fail, get_engine, ok, utc_now_iso

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])


@router.get("/mode", summary="Configured/effective execution mode")
def runtime_mode(request: Request) -> Any:
    engine = get_engine(request)
    if engine is None:
        return ok(
            request,
            {
                "mode": None,
                "effective_mode": None,
                "engine_attached": False,
                "replaying": None,
            },
        )
    try:
        mode = engine.config.execution.mode.value
    except Exception:
        mode = None
    effective = getattr(engine, "_runtime_mode", None)
    replaying = getattr(request.app.state, "is_replaying", None)
    return ok(
        request,
        {
            "mode": mode,
            "effective_mode": effective,
            "engine_attached": True,
            "replaying": replaying,
        },
    )


@router.get("/freshness", summary="Live data freshness contract (engine model)")
def runtime_freshness(request: Request) -> Any:
    engine, resp = engine_or_503(request)
    if resp is not None:
        return resp
    try:
        fresh = engine.compute_live_freshness()
    except Exception:
        fresh = None
    return ok(
        request,
        {
            "freshness": fresh,
            "available": fresh is not None,
            "probed_at": utc_now_iso(),
        },
    )


_VALID_MODES = {"BACKTEST", "REPLAY", "PAPER", "SHADOW", "LIVE"}

#: Allowed transitions (conservative read-only matrix; mirrors the ExecutionMode
#: semantics in domain/enums.py — PAPER<->LIVE/ShadOW style moves, replay belongs
#: to its own controller). This endpoint NEVER applies anything.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "BACKTEST": {"REPLAY", "PAPER", "SHADOW", "LIVE"},
    "REPLAY": {"PAPER", "SHADOW"},
    "PAPER": {"LIVE", "SHADOW", "REPLAY"},
    "SHADOW": {"LIVE", "PAPER"},
    "LIVE": {"PAPER", "SHADOW"},
}


class ModeProposal(BaseModel):
    """A PROPOSED mode change. Validation/preview only — never applied."""

    mode: str


def _current_mode(request: Request) -> str | None:
    engine = get_engine(request)
    if engine is None:
        return None
    try:
        return engine.config.execution.mode.value  # type: ignore[union-attr]
    except Exception:
        return None


def _validate_transition(current: str | None, proposed: str) -> dict[str, Any]:
    """Pure transition validator (spec capability 12)."""
    proposed = proposed.strip().upper()
    errors: list[str] = []
    warnings: list[str] = []
    if proposed not in _VALID_MODES:
        errors.append(f"mode must be one of {sorted(_VALID_MODES)}")
    if current is not None and not errors:
        if proposed == current:
            warnings.append("proposed mode equals current mode (no-op)")
        elif proposed not in _ALLOWED_TRANSITIONS.get(current, set()):
            errors.append(f"transition {current} -> {proposed} is not allowed")
    if proposed == "LIVE":
        warnings.append(
            "LIVE engages real execution; the API surface stays read-only and "
            "mode changes must go through the operator console/CLI"
        )
    return {
        "valid": not errors,
        "current_mode": current,
        "proposed_mode": proposed,
        "errors": errors,
        "warnings": warnings,
    }


def _mode_impact(proposed: str) -> dict[str, Any]:
    """Pure impact preview (spec capability 13): which subsystems a change touches."""
    proposed = proposed.strip().upper()
    matrix = {
        "execution": proposed in {"PAPER", "LIVE"},
        "inference": proposed in {"PAPER", "SHADOW", "LIVE", "REPLAY", "BACKTEST"},
        "data_feed": proposed in {"PAPER", "SHADOW", "LIVE"},
        "simulated_fills": proposed == "PAPER",
        "real_orders": proposed == "LIVE",
        "replay_controller": proposed == "REPLAY",
        "offline_deterministic": proposed in {"BACKTEST", "REPLAY"},
    }
    return {
        "proposed_mode": proposed,
        "touches": [k for k, v in matrix.items() if v],
        "matrix": matrix,
        "api_mutations_unlocked": [],  # v1 stays read-only regardless of mode
    }


@router.post("/mode/validate", summary="Validate a proposed mode change (no apply)")
def runtime_mode_validate(request: Request, proposal: ModeProposal) -> Any:
    result = _validate_transition(_current_mode(request), proposal.mode)
    status = 200 if result["valid"] else 422
    return ok(request, result, status_code=status)


@router.post("/mode/preview", summary="Impact preview of a proposed mode change (no apply)")
def runtime_mode_preview(request: Request, proposal: ModeProposal) -> Any:
    proposed = proposal.mode.strip().upper()
    if proposed not in _VALID_MODES:
        return fail(
            request,
            "VALIDATION_ERROR",
            details={"mode": f"must be one of {sorted(_VALID_MODES)}"},
        )
    validation = _validate_transition(_current_mode(request), proposed)
    impact = _mode_impact(proposed)
    payload: dict[str, Any] = {"validation": validation, "impact": impact}
    if not validation["valid"]:
        payload["applies"] = False
    return ok(request, payload)
