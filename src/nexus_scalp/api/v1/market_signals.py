"""API v1 MARKET + SIGNALS/DECISIONS routers (CHG-043).

Truthful market + decision surface over REAL backends:
  GET /api/v1/market/quote       - last observed bid/ask/spread (engine tick state)
  GET /api/v1/market/freshness   - engine live-freshness contract (G29)
  GET /api/v1/market/regime      - current regime + supporting evidence
  GET /api/v1/signals/latest     - latest recorded signal (audit_signals tail)
  GET /api/v1/signals/history    - paginated/filtered signal history
  GET /api/v1/decisions/latest   - latest decision summary
  GET /api/v1/decisions          - paginated decision history
  GET /api/v1/decisions/stats    - decision statistics (real SQL aggregates)
  GET /api/v1/decisions/no-trade - NO_TRADE analytics (reason distribution)

A "decision" in NSE v1 = one recorded signal row (audit_signals: the immutable
per-M1-decision ledger). Evidence/gates trace fields come from the row payload
decision_stage / reason_code / blocked_by - the REAL gate fields the engine
writes. No synthesized probabilities.

USED BY: web/api_v1_wiring.py.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from nexus_scalp.api.v1.common import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    TimeRange,
    envelope,
    iso_or_none,
    time_range_dep,
    utc_now_iso,
)
from nexus_scalp.api.v1.deps import get_audit_repository, get_engine

router = APIRouter(prefix="/api/v1", tags=["market", "signals", "decisions"])


# ---------------------------------------------------------------------------
# Shared internals
# ---------------------------------------------------------------------------

_SIGNAL_COLS = (
    "id, request_id, symbol, action, confidence, proposed_entry, stop_loss, "
    "take_profit, regime, generated_at, execution_mode, reason_code, "
    "decision_stage, blocked_by, htf_score, smc_score, confidence_before_filters, "
    "confidence_after_filters"
)


def _parse_payload(row: dict[str, Any]) -> dict[str, Any]:
    """audit_signals.payload -> dict (bounded; malformed payload -> {})."""
    raw = row.pop("payload", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _serialize_signal(row: dict[str, Any], *, with_payload: bool = False) -> dict[str, Any]:
    out = dict(row)
    out["generated_at"] = iso_or_none(out.get("generated_at"))
    if with_payload:
        out["payload"] = _parse_payload(out)
    else:
        out.pop("payload", None)
    return out


def _fetch_signals(
    repo: Any,
    *,
    symbol: str | None,
    action: str | None,
    time_range: TimeRange | None,
    limit: int,
    offset: int = 0,
    with_payload: bool = False,
    count_total: bool = False,
) -> tuple[list[dict[str, Any]], int | None]:
    """Bounded, parameterized audit_signals query (read-only)."""
    from nexus_scalp.api.v1.deps import sqlite_query_bounded

    where: list[str] = []
    args: list[Any] = []
    if symbol:
        where.append("symbol = ?")
        args.append(symbol)
    if action:
        where.append("action = ?")
        args.append(action)
    if time_range is not None:
        if time_range.date_from:
            where.append("generated_at >= ?")
            args.append(time_range.date_from)
        if time_range.date_to:
            where.append("generated_at <= ?")
            args.append(time_range.date_to)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT {_SIGNAL_COLS}{', payload' if with_payload else ''} FROM audit_signals {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?"
    rows = sqlite_query_bounded(repo._db_path, sql, tuple(args), limit=limit)
    # OFFSET injection: sqlite_query_bounded appends LIMIT only when the SQL
    # ends with LIMIT ?; here OFFSET ? must be bound too -> do a second call.
    total: int | None = None
    if count_total:
        count_sql = f"SELECT COUNT(*) AS c FROM audit_signals {where_sql}"
        counted = sqlite_query_bounded(repo._db_path, count_sql, tuple(args), limit=1)
        total = int(counted[0]["c"]) if counted else 0
    return [_serialize_signal(r, with_payload=with_payload) for r in rows], total


def _require_repo(request: Request) -> Any:
    try:
        return get_audit_repository(request)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE"}) from exc


# ---------------------------------------------------------------------------
# MARKET
# ---------------------------------------------------------------------------


@router.get("/market/quote", summary="Last observed bid/ask/spread (engine tick state)")
def market_quote(request: Request, symbol: str | None = None) -> dict[str, Any]:
    engine = get_engine(request)
    if engine is None:
        raise HTTPException(
            status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE", "dependency": "engine"}
        )
    tick = getattr(engine, "_last_tick", None)
    if tick is None:
        return envelope(
            request,
            {
                "available": False,
                "reason": "no tick observed yet (engine warmup or no feed)",
                "probed_at": utc_now_iso(),
            },
        )
    data: dict[str, Any] = {
        "available": True,
        "symbol": getattr(tick, "symbol", symbol),
        "bid": getattr(tick, "bid", None),
        "ask": getattr(tick, "ask", None),
        "time": iso_or_none(getattr(tick, "timestamp", None)),
    }
    if data["bid"] is not None and data["ask"] is not None:
        data["spread"] = round(data["ask"] - data["bid"], 10)
    return envelope(request, data)


@router.get("/market/freshness", summary="Live data freshness (engine G29 freshness contract)")
def market_freshness(request: Request) -> dict[str, Any]:
    engine = get_engine(request)
    if engine is None or not hasattr(engine, "compute_live_freshness"):
        raise HTTPException(
            status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE", "dependency": "engine"}
        )
    try:
        fresh = engine.compute_live_freshness()
    except Exception as exc:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(
            get_logger("nexus_scalp.api.v1"),
            "/api/v1/market/freshness",
            None,
            exc,
            resource="freshness",
        )
        raise HTTPException(status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE"}) from exc
    return envelope(request, fresh)


@router.get("/market/regime", summary="Current regime + supporting evidence")
def market_regime(request: Request) -> dict[str, Any]:
    engine = get_engine(request)
    state = getattr(engine, "_last_regime_state", None) if engine is not None else None
    if state is None:
        return envelope(
            request,
            {
                "available": False,
                "reason": "no regime classification observed yet",
                "probed_at": utc_now_iso(),
            },
        )
    to_dict = getattr(state, "as_dict", None) or getattr(state, "model_dump", None)
    data = to_dict() if callable(to_dict) else {"regime": str(state)}
    return envelope(request, {"available": True, "regime": data})


# ---------------------------------------------------------------------------
# SIGNALS
# ---------------------------------------------------------------------------


@router.get("/signals/latest", summary="Latest recorded signal (audit_signals tail)")
def signals_latest(request: Request, symbol: str | None = None) -> dict[str, Any]:
    repo = _require_repo(request)
    rows, _ = _fetch_signals(repo, symbol=symbol, action=None, time_range=None, limit=1)
    if not rows:
        return envelope(
            request,
            {"available": False, "reason": "no signals recorded"},
        )
    return envelope(request, {"available": True, "signal": rows[0]})


@router.get("/signals/history", summary="Paginated/filtered signal history")
def signals_history(
    request: Request,
    symbol: str | None = Query(None, max_length=32, description="Filter by symbol."),
    action: str | None = Query(
        None, pattern="^(BUY|SELL|NO_TRADE|CLOSE)$", description="Filter by action."
    ),
    time: TimeRange = Depends(time_range_dep),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> dict[str, Any]:
    repo = _require_repo(request)
    rows, total = _fetch_signals(
        repo,
        symbol=symbol,
        action=action,
        time_range=time,
        limit=page_size,
        offset=(page - 1) * page_size,
        count_total=True,
    )
    pagination = {
        "page": page,
        "page_size": page_size,
        "total_items": total,
        "has_next": total is not None and page * page_size < total,
        "has_prev": page > 1,
    }
    return envelope(request, {"signals": rows}, pagination=pagination)


# ---------------------------------------------------------------------------
# DECISIONS (v1 = recorded signal rows; gates = real decision_stage/reason fields)
# ---------------------------------------------------------------------------


@router.get("/decisions/latest", summary="Latest decision with summary")
def decisions_latest(request: Request) -> dict[str, Any]:
    repo = _require_repo(request)
    rows, _ = _fetch_signals(
        repo, symbol=None, action=None, time_range=None, limit=1, with_payload=True
    )
    if not rows:
        return envelope(request, {"available": False, "reason": "no decisions recorded"})
    row = rows[0]
    payload = row.pop("payload", {})
    return envelope(
        request,
        {
            "available": True,
            "decision": row,
            "summary": {
                "action": row.get("action"),
                "confidence": row.get("confidence"),
                "regime": row.get("regime"),
                "decision_stage": row.get("decision_stage"),
                "reason_code": row.get("reason_code"),
                "ai_probabilities": {
                    k: payload.get(k)
                    for k in (
                        "ai_buy_probability",
                        "ai_sell_probability",
                        "ai_no_trade_probability",
                    )
                    if k in payload
                },
            },
        },
    )


@router.get("/decisions", summary="Paginated decision history")
def decisions_history(
    request: Request,
    symbol: str | None = Query(None, max_length=32),
    action: str | None = Query(None, pattern="^(BUY|SELL|NO_TRADE|CLOSE)$"),
    time: TimeRange = Depends(time_range_dep),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> dict[str, Any]:
    repo = _require_repo(request)
    rows, total = _fetch_signals(
        repo,
        symbol=symbol,
        action=action,
        time_range=time,
        limit=page_size,
        offset=(page - 1) * page_size,
        count_total=True,
    )
    pagination = {
        "page": page,
        "page_size": page_size,
        "total_items": total,
        "has_next": total is not None and page * page_size < total,
        "has_prev": page > 1,
    }
    return envelope(request, {"decisions": rows}, pagination=pagination)


@router.get("/decisions/stats", summary="Decision statistics (real aggregates)")
def decisions_stats(
    request: Request,
    symbol: str | None = Query(None, max_length=32),
    limit: int = Query(5000, ge=100, le=20_000, description="Tail window for aggregate."),
) -> dict[str, Any]:
    repo = _require_repo(request)
    from nexus_scalp.api.v1.deps import sqlite_query_bounded

    where = "WHERE symbol = ?" if symbol else ""
    args: tuple[Any, ...] = (symbol,) if symbol else ()
    rows = sqlite_query_bounded(
        repo._db_path,
        f"SELECT action, COUNT(*) AS n, AVG(confidence) AS avg_confidence FROM audit_signals {where} "
        "AND id > (SELECT COALESCE(MAX(id),0) - ? FROM audit_signals) GROUP BY action"
        if symbol
        else "SELECT action, COUNT(*) AS n, AVG(confidence) AS avg_confidence FROM audit_signals "
        "WHERE id > (SELECT COALESCE(MAX(id),0) - ?) GROUP BY action",
        args,
        limit=50,
    )
    by_action = {
        r["action"]: {"count": r["n"], "avg_confidence": r["avg_confidence"]} for r in rows
    }
    return envelope(
        request, {"window_tail": limit, "by_action": by_action, "generated_at": utc_now_iso()}
    )


@router.get("/decisions/no-trade", summary="NO_TRADE analytics over the recorded decision tail")
def decisions_no_trade(
    request: Request,
    limit: int = Query(5000, ge=100, le=20_000),
) -> dict[str, Any]:
    repo = _require_repo(request)
    from nexus_scalp.api.v1.deps import sqlite_query_bounded

    rows = sqlite_query_bounded(
        repo._db_path,
        "SELECT reason_code, COUNT(*) AS n FROM audit_signals "
        "WHERE action = 'NO_TRADE' AND id > (SELECT COALESCE(MAX(id),0) - ?) "
        "GROUP BY reason_code ORDER BY n DESC",
        (),
        limit=100,
    )
    total = sum(r["n"] for r in rows)
    reasons = [
        {
            "reason_code": r["reason_code"] or "UNSPECIFIED",
            "count": r["n"],
            "share": (r["n"] / total) if total else None,
        }
        for r in rows
    ]
    return envelope(
        request,
        {
            "window_tail": limit,
            "no_trade_total": total,
            "reasons": reasons,
            "generated_at": utc_now_iso(),
        },
    )
