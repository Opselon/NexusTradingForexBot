"""API v1 — MARKET domain (snapshot/quote/bars/regime/symbols).

Sources verified in docs/api/API_PLATFORM_V1.md §7 (market.py, capabilities 15-19).
ENGINE_UNAVAILABLE (503) when no engine is attached; UNAVAILABLE fields are None.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from nexus_scalp.web.api_v1.common import (
    adapter_or_503,
    fail,
    get_engine,
    ok,
)

router = APIRouter(prefix="/api/v1/market", tags=["market"])


def _engine_or_fail(request: Request) -> tuple[Any, Any]:
    """(engine, None) when attached; (None, 503-envelope) otherwise."""
    engine = get_engine(request)
    if engine is None:
        return None, fail(request, "ENGINE_UNAVAILABLE")
    return engine, None


@router.get("/snapshot", summary="Account + symbol snapshot (real adapter)")
def market_snapshot(
    request: Request,
    symbol: str | None = Query(default=None),
) -> Any:
    adapter, resp = adapter_or_503(request)
    if resp is not None:
        return resp
    engine = get_engine(request)
    try:
        sym = symbol or engine.config.execution.symbol
    except Exception:
        sym = symbol
    if not sym:
        return fail(request, "VALIDATION_ERROR", details={"symbol": "required"})
    out: dict[str, Any] = {"symbol": sym, "account": None, "symbol_spec": None}
    try:
        acc = adapter.get_account_snapshot()
        out["account"] = _dataclass_dict(acc)
    except Exception:
        out["account"] = None
    try:
        spec = adapter.get_symbol_snapshot(sym)
        out["symbol_spec"] = _dataclass_dict(spec)
    except Exception:
        out["symbol_spec"] = None
    return ok(request, out)


@router.get("/quote", summary="Current bid/ask/spread from broker tick")
def market_quote(
    request: Request,
    symbol: str | None = Query(default=None),
) -> Any:
    adapter, resp = adapter_or_503(request)
    if resp is not None:
        return resp
    engine = get_engine(request)
    try:
        sym = symbol or engine.config.execution.symbol
    except Exception:
        sym = symbol
    if not sym:
        return fail(request, "VALIDATION_ERROR", details={"symbol": "required"})
    try:
        tick = adapter.get_broker_tick(sym)
    except Exception:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="broker tick unavailable")
    return ok(request, _dataclass_dict(tick))


@router.get("/bars", summary="Completed M-bars held by the engine aggregator")
def market_bars(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> Any:
    engine, resp = _engine_or_fail(request)
    if resp is not None:
        return resp
    agg = getattr(engine, "aggregator", None)
    if agg is None:
        return fail(request, "RESOURCE_UNAVAILABLE", message="bar aggregator not attached")
    try:
        bars = agg.get_completed_bars()[-limit:]
    except Exception:
        bars = []
    items = [
        {
            "timestamp": getattr(b, "timestamp", None),
            "open": getattr(b, "open", None),
            "high": getattr(b, "high", None),
            "low": getattr(b, "low", None),
            "close": getattr(b, "close", None),
            "volume": getattr(b, "volume", None),
        }
        for b in bars
    ]
    return ok(
        request,
        {
            "items": items,
            "count": len(items),
            "scope": "engine aggregator window only (not full history)",
        },
    )


@router.get("/regime", summary="Current regime + evidence (None before first inference)")
def market_regime(request: Request) -> Any:
    engine, resp = _engine_or_fail(request)
    if resp is not None:
        return resp
    state = getattr(engine, "_last_regime_state", None)
    if state is None:
        return ok(request, {"regime": None, "evidence": None, "note": "no inference yet"})
    if hasattr(state, "as_dict"):
        state = state.as_dict()
    elif hasattr(state, "model_dump"):
        state = state.model_dump()
    return ok(request, {"regime": state, "evidence": state})


@router.get("/symbols", summary="Configured trading symbol(s)")
def market_symbols(request: Request) -> Any:
    engine, resp = _engine_or_fail(request)
    if resp is not None:
        return resp
    try:
        symbol = engine.config.execution.symbol
    except Exception:
        symbol = None
    return ok(request, {"symbols": [symbol] if symbol else [], "configured": symbol})


def _dataclass_dict(obj: Any) -> Any:
    """SnapshotBase dataclasses -> plain dict (never fabricates; None stays None)."""
    if obj is None:
        return None
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict

        try:
            return asdict(obj)
        except Exception:
            return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return None
