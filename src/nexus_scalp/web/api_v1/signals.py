"""API v1 — SIGNALS domain.

Sources verified in docs/api/API_PLATFORM_V1.md §7 (signals.py, capabilities 20-21).
All reads go through the AuditRepository SQLite path with bounded, parameterized
queries (no string-built SQL from user input; LIMIT always injected server-side).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from nexus_scalp.web.api_v1.common import (
    build_page,
    fail,
    fetch_rows_bounded,
    get_audit_repo,
    ok,
    parse_pagination,
    sanitize_config,
)

_BASE_SELECT = (
    "SELECT request_id, symbol, action, confidence, proposed_entry, stop_loss, "
    "take_profit, regime, generated_at, payload, execution_mode, reason_code, "
    "decision_stage, blocked_by, htf_score, smc_score, confidence_before_filters, "
    "confidence_after_filters FROM audit_signals"
)

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])


@router.get("/latest", summary="Latest signal (most recent audit_signals row)")
def signals_latest(request: Request) -> Any:
    rows = fetch_rows_bounded(get_audit_repo(request), _BASE_SELECT, (), 1)
    if not rows:
        return fail(request, "RESOURCE_NOT_FOUND", message="no signals recorded yet")
    return ok(request, _row_to_signal(rows[0]))


@router.get("/history", summary="Paginated signal history (symbol/action filters)")
def signals_history(
    request: Request,
    page: int = Query(default=1),
    page_size: int = Query(default=50),
    symbol: str | None = Query(default=None),
    action: str | None = Query(default=None),
    hours_back: int = Query(default=168),
) -> Any:
    checked = parse_pagination(page, page_size)
    if isinstance(checked, JSONResponse):
        return checked
    p, ps = checked
    clauses: list[str] = []
    args: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        args.append(symbol)
    if action:
        clauses.append("action = ?")
        args.append(action.upper())
    cutoff = _cutoff(hours_back)
    if cutoff is not None:
        clauses.append("generated_at >= ?")
        args.append(cutoff)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return ok(
        request,
        _query_signals(request, where=where, args=tuple(args), page=p, page_size=ps),
    )


def _cutoff(hours_back: int) -> str | None:
    """UTC ISO lower bound for generated_at; capped at 720h (spec §5)."""
    from datetime import UTC, datetime, timedelta

    bounded = max(1, min(int(hours_back), 720))
    return (datetime.now(UTC) - timedelta(hours=bounded)).isoformat()


def _row_to_signal(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.pop("payload", None)
    try:
        parsed = json.loads(payload) if isinstance(payload, str) else (payload or {})
    except Exception:
        parsed = {}
    row["payload"] = sanitize_config(parsed)
    return row


def _query_signals(
    request: Request,
    *,
    where: str = "",
    args: tuple[Any, ...] = (),
    page: int = 1,
    page_size: int = 50,
) -> Any:
    """Bounded paginated query; fetch page_size+1 to compute has_more."""
    repo = get_audit_repo(request)
    limit = page_size + 1
    offset = (page - 1) * page_size
    sql = _BASE_SELECT + (f" WHERE {where}" if where else "") + " ORDER BY id DESC"
    rows = fetch_rows_bounded(repo, sql, args, limit + offset)
    rows = rows[offset : offset + limit]
    has_more = len(rows) > page_size
    items = [_row_to_signal(r) for r in rows[:page_size]]
    return build_page(items, page, page_size, has_more=has_more)
