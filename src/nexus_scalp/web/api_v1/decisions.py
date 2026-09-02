"""API v1 — DECISIONS domain (decision_id = request_id).

Sources verified in docs/api/API_PLATFORM_V1.md §7. All reads go through the
AuditRepository SQLite path with bounded, parameterized queries (no string-built
SQL from user input; LIMIT always injected server-side).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query, Request

from nexus_scalp.web.api_v1.common import (
    build_page,
    fail,
    fetch_rows_bounded,
    get_audit_repo,
    ok,
    parse_pagination,
    sanitize_config,
)

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


_BASE_SELECT = (
    "SELECT request_id, symbol, action, confidence, proposed_entry, stop_loss, "
    "take_profit, regime, generated_at, payload, execution_mode, reason_code, "
    "decision_stage, blocked_by, htf_score, smc_score, confidence_before_filters, "
    "confidence_after_filters FROM audit_signals"
)


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


def _decision_summary(sig: dict[str, Any]) -> dict[str, Any]:
    payload = sig.get("payload", {})
    return {
        "decision_id": sig.get("request_id"),
        "symbol": sig.get("symbol"),
        "action": sig.get("action"),
        "confidence": sig.get("confidence"),
        "proposed_entry": sig.get("proposed_entry"),
        "stop_loss": sig.get("stop_loss"),
        "take_profit": sig.get("take_profit"),
        "regime": sig.get("regime"),
        "generated_at": sig.get("generated_at"),
        "execution_mode": sig.get("execution_mode"),
        "decision_stage": sig.get("decision_stage"),
        "blocked_by": sig.get("blocked_by"),
        "reason_code": sig.get("reason_code"),
        "confidence_before_filters": sig.get("confidence_before_filters"),
        "confidence_after_filters": sig.get("confidence_after_filters"),
        "risk_checks": {
            "guardian_status": payload.get("guardian_status"),
            "risk_allowed": payload.get("risk_allowed"),
        },
    }


def _filters(
    *,
    symbol: str | None = None,
    action: str | None = None,
    execution_mode: str | None = None,
    decision_stage: str | None = None,
    hours_back: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    args: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        args.append(symbol)
    if action:
        clauses.append("action = ?")
        args.append(action.upper())
    if execution_mode:
        clauses.append("execution_mode = ?")
        args.append(execution_mode)
    if decision_stage:
        clauses.append("decision_stage = ?")
        args.append(decision_stage)
    if hours_back is not None:
        clauses.append("generated_at >= ?")
        args.append(_cutoff(hours_back))
    return (" AND ".join(clauses), tuple(args)) if clauses else ("", ())


def _cutoff(hours_back: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(hours=hours_back)).isoformat()


@router.get("/latest", summary="Latest decision with full summary")
def decisions_latest(request: Request) -> Any:
    page = _query_signals(request, page=1, page_size=1)
    items = page["items"]
    if not items:
        return fail(request, "RESOURCE_NOT_FOUND", message="no decisions recorded yet")
    return ok(request, _decision_summary(items[0]))


@router.get("", summary="Paginated decision history")
def decisions_list(
    request: Request,
    page: int = Query(default=1),
    page_size: int = Query(default=50),
    symbol: str | None = Query(default=None),
    action: str | None = Query(default=None),
    execution_mode: str | None = Query(default=None),
    decision_stage: str | None = Query(default=None),
    hours_back: int = Query(default=168, ge=1, le=720),
) -> Any:
    pg = parse_pagination(page, page_size)
    if not isinstance(pg, tuple):
        return pg
    p, ps = pg
    where, args = _filters(
        symbol=symbol,
        action=action,
        execution_mode=execution_mode,
        decision_stage=decision_stage,
        hours_back=hours_back,
    )
    return ok(request, _query_signals(request, where=where, args=args, page=p, page_size=ps))


@router.get("/stats", summary="Decision statistics (bounded window)")
def decisions_stats(
    request: Request,
    hours_back: int = Query(default=168, ge=1, le=720),
) -> Any:
    repo = get_audit_repo(request)
    rows = fetch_rows_bounded(
        repo,
        "SELECT action, decision_stage, COUNT(*) AS n FROM audit_signals"
        " WHERE generated_at >= ? GROUP BY action, decision_stage ORDER BY n DESC",
        (_cutoff(hours_back),),
        500,
    )
    by_action: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    for r in rows:
        by_action[r["action"]] = by_action.get(r["action"], 0) + int(r["n"])
        stage = r["decision_stage"] or "UNKNOWN"
        by_stage[stage] = by_stage.get(stage, 0) + int(r["n"])
    return ok(
        request,
        {
            "window_hours": hours_back,
            "total": sum(by_action.values()),
            "by_action": by_action,
            "by_stage": by_stage,
        },
    )


@router.get("/no-trade", summary="NO_TRADE analytics")
def decisions_no_trade(request: Request) -> Any:
    page = _query_signals(
        request,
        where="action = 'NO_TRADE'",
        args=(),
        page=1,
        page_size=1,
    )
    total = fetch_rows_bounded(
        get_audit_repo(request),
        "SELECT COUNT(*) AS n FROM audit_signals WHERE action = 'NO_TRADE'",
        (),
        1,
    )
    reasons = fetch_rows_bounded(
        get_audit_repo(request),
        "SELECT COALESCE(reason_code, 'UNKNOWN') AS reason, COUNT(*) AS n FROM audit_signals"
        " WHERE action = 'NO_TRADE' GROUP BY reason ORDER BY n DESC",
        (),
        100,
    )
    return ok(
        request,
        {
            "total": int(total[0]["n"]) if total else 0,
            "reasons": [{"reason": r["reason"], "count": int(r["n"])} for r in reasons],
            "latest": page["items"][0] if page["items"] else None,
        },
    )


@router.get("/{decision_id}", summary="Complete decision detail by request_id")
def decisions_detail(request: Request, decision_id: str) -> Any:
    rows = fetch_rows_bounded(
        get_audit_repo(request),
        _BASE_SELECT + " WHERE request_id = ?",
        (decision_id,),
        10,
    )
    if not rows:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"decision {decision_id} not found")
    return ok(request, _decision_summary(_row_to_signal(rows[0])))


@router.get("/{decision_id}/evidence", summary="Raw decision evidence (payload)")
def decisions_evidence(request: Request, decision_id: str) -> Any:
    rows = fetch_rows_bounded(
        get_audit_repo(request),
        "SELECT payload FROM audit_signals WHERE request_id = ?",
        (decision_id,),
        1,
    )
    if not rows:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"decision {decision_id} not found")
    try:
        payload = json.loads(rows[0]["payload"]) if isinstance(rows[0]["payload"], str) else {}
    except Exception:
        payload = {}
    return ok(request, {"evidence": sanitize_config(payload)})


@router.get("/{decision_id}/gates", summary="Gate-by-gate decision trace")
def decisions_gates(request: Request, decision_id: str) -> Any:
    rows = fetch_rows_bounded(
        get_audit_repo(request),
        _BASE_SELECT + " WHERE request_id = ?",
        (decision_id,),
        1,
    )
    if not rows:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"decision {decision_id} not found")
    sig = _row_to_signal(rows[0])
    payload = sig.get("payload", {})
    gates: list[dict[str, Any]] = [
        {
            "gate": "decision_stage",
            "value": sig.get("decision_stage"),
            "passed": (sig.get("decision_stage") or "").upper() not in {"BLOCKED", "REJECTED"},
        },
        {
            "gate": "blocked_by",
            "value": sig.get("blocked_by"),
            "passed": not sig.get("blocked_by"),
        },
        {
            "gate": "reason_code",
            "value": sig.get("reason_code"),
            "passed": True,
        },
        {
            "gate": "guardian_status",
            "value": payload.get("guardian_status"),
            "passed": (payload.get("guardian_status") or "PASS") == "PASS",
        },
        {
            "gate": "risk_allowed",
            "value": payload.get("risk_allowed"),
            "passed": bool(payload.get("risk_allowed")),
        },
    ]
    return ok(request, {"decision_id": decision_id, "gates": gates})


@router.get("/{decision_id}/explanation", summary="Human-readable explanation")
def decisions_explanation(request: Request, decision_id: str) -> Any:
    rows = fetch_rows_bounded(
        get_audit_repo(request),
        _BASE_SELECT + " WHERE request_id = ?",
        (decision_id,),
        1,
    )
    if not rows:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"decision {decision_id} not found")
    sig = _row_to_signal(rows[0])
    parts = [
        f"Engine evaluated {sig.get('symbol')} ({sig.get('regime')}) at {sig.get('generated_at')}",
        f"model action={sig.get('action')} confidence={sig.get('confidence')}",
    ]
    if sig.get("decision_stage"):
        parts.append(f"stage={sig.get('decision_stage')}")
    if sig.get("blocked_by"):
        parts.append(f"BLOCKED by {sig.get('blocked_by')}")
    if sig.get("reason_code"):
        parts.append(f"reason={sig.get('reason_code')}")
    explanation = "; ".join(p for p in parts if p) + "."
    return ok(request, {"decision_id": decision_id, "explanation": explanation})
