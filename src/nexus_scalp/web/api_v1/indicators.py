"""API v1 — INDICATORS domain (SOLID, additive).

Routes (all GET, read-only, share the /api/v1 envelope):
  GET /api/v1/indicators            → full snapshot (oscillators + MAs + pivots + gauges)
  GET /api/v1/indicators/oscillators
  GET /api/v1/indicators/moving-averages
  GET /api/v1/indicators/pivots
  GET /api/v1/indicators/gauges
  GET /api/v1/indicators/summary

Bars source: engine.aggregator.get_completed_bars() when an engine is
attached (live truth). When no engine, a bounded 503 — no synthetic bars.
Query params: timeframe (M1/M5/…; validated, defaults to M1), limit (1..500 for
bar window fed into the calculators), symbol (optional; defaults to configured).

Never fabricates indicator values: if insufficient bars, value=None + Neutral.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from nexus_scalp.web.api_v1.common import fail, get_engine, ok

router = APIRouter(prefix="/api/v1/indicators", tags=["indicators"])

_ALLOWED_TFS = {"M1", "M5", "M15", "M30", "H1", "H2", "H4", "D1", "W1", "MN1", "1M", "1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"}


def _tf_or_fail(request: Request, timeframe: str | None) -> str | Any:
    tf = (timeframe or "M1").strip()
    up = tf.upper()
    # keep comparison case-insensitive but store canonical
    allowed_up = {t.upper() for t in _ALLOWED_TFS}
    if up not in allowed_up:
        return fail(request, "VALIDATION_ERROR", details={"timeframe": f"must be one of {sorted(_ALLOWED_TFS)}"})
    return tf


def _get_bars(request: Request, limit: int) -> tuple[list[Any] | None, Any]:
    engine = get_engine(request)
    if engine is None:
        return None, fail(request, "ENGINE_UNAVAILABLE")
    agg = getattr(engine, "aggregator", None)
    if agg is None:
        return None, fail(request, "RESOURCE_UNAVAILABLE", message="bar aggregator not attached")
    try:
        bars = agg.get_completed_bars()[-int(limit):]
    except Exception:
        bars = []
    return list(bars), None


def _symbol(request: Request, symbol: str | None) -> str:
    if symbol and symbol.strip():
        return symbol.strip().upper()
    engine = get_engine(request)
    try:
        return str(engine.config.execution.symbol).upper() if engine is not None else "XAUUSD"
    except Exception:
        return "XAUUSD"


def _build_snapshot(request: Request, timeframe: str, limit: int, symbol: str | None) -> tuple[Any, Any]:
    tf_checked = _tf_or_fail(request, timeframe)
    if hasattr(tf_checked, "status_code"):  # fail response
        return None, tf_checked
    tf: str = tf_checked  # type: ignore[assignment]
    bars, err = _get_bars(request, limit)
    if err is not None:
        return None, err
    sym = _symbol(request, symbol)
    from nexus_scalp.indicators.service import IndicatorService

    svc = IndicatorService()
    snap = svc.snapshot(sym, bars or [], timeframe=tf)
    api = svc.to_api_dict(snap)
    api["source_bar_count"] = len(bars or [])
    return api, None


@router.get("", summary="Full indicator snapshot (oscillators + MAs + pivots + gauges)")
def indicators_snapshot(
    request: Request,
    timeframe: str | None = Query(default="M1", description="M1/M5/M15/M30/H1/H2/H4/D1/W1/MN1"),
    limit: int = Query(default=500, ge=1, le=2000),
    symbol: str | None = Query(default=None),
) -> Any:
    api, err = _build_snapshot(request, timeframe or "M1", limit, symbol)
    if err is not None:
        return err
    return ok(request, api)


@router.get("/oscillators", summary="Oscillator breakdown")
def indicators_oscillators(
    request: Request,
    timeframe: str | None = Query(default="M1"),
    limit: int = Query(default=500, ge=1, le=2000),
    symbol: str | None = Query(default=None),
) -> Any:
    api, err = _build_snapshot(request, timeframe or "M1", limit, symbol)
    if err is not None:
        return err
    return ok(request, {"oscillators": api["oscillators"], "gauges": {"oscillators": api["gauges"]["oscillators"]}})


@router.get("/moving-averages", summary="Moving-average breakdown")
def indicators_moving_averages(
    request: Request,
    timeframe: str | None = Query(default="M1"),
    limit: int = Query(default=500, ge=1, le=2000),
    symbol: str | None = Query(default=None),
) -> Any:
    api, err = _build_snapshot(request, timeframe or "M1", limit, symbol)
    if err is not None:
        return err
    return ok(request, {"moving_averages": api["moving_averages"], "gauges": {"moving_averages": api["gauges"]["moving_averages"]}})


@router.get("/pivots", summary="Pivot matrix (Classic/Fibonacci/Camarilla/Woodie/DM)")
def indicators_pivots(
    request: Request,
    timeframe: str | None = Query(default="M1"),
    limit: int = Query(default=500, ge=1, le=2000),
    symbol: str | None = Query(default=None),
) -> Any:
    api, err = _build_snapshot(request, timeframe or "M1", limit, symbol)
    if err is not None:
        return err
    return ok(request, {"pivots": api["pivots"]})


@router.get("/gauges", summary="Gauge states (oscillators / moving_averages / summary)")
def indicators_gauges(
    request: Request,
    timeframe: str | None = Query(default="M1"),
    limit: int = Query(default=500, ge=1, le=2000),
    symbol: str | None = Query(default=None),
) -> Any:
    api, err = _build_snapshot(request, timeframe or "M1", limit, symbol)
    if err is not None:
        return err
    return ok(request, {"gauges": api["gauges"], "summary": api["summary"]})


@router.get("/summary", summary="Summary counts + gauge labels")
def indicators_summary(
    request: Request,
    timeframe: str | None = Query(default="M1"),
    limit: int = Query(default=500, ge=1, le=2000),
    symbol: str | None = Query(default=None),
) -> Any:
    api, err = _build_snapshot(request, timeframe or "M1", limit, symbol)
    if err is not None:
        return err
    return ok(
        request,
        {
            "symbol": api["symbol"],
            "timeframe": api["timeframe"],
            "bar_count": api["bar_count"],
            "last_close": api["last_close"],
            "gauges": api["gauges"],
            "summary": api["summary"],
        },
    )
