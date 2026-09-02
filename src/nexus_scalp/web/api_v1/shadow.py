"""API v1 — SHADOW domain (status/runs/run detail/decisions/70d).

Sources verified in docs/api/API_PLATFORM_V1.md §7 (shadow.py, capabilities 46-50).
Reads ShadowStore (PHASE-11 comparisons) + Shadow70Store (70D observer) over the
authoritative audit DB. Every block fails independently and truthfully.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from nexus_scalp.web.api_v1.common import (
    build_page,
    fail,
    get_audit_repo,
    iso_or_none,
    ok,
    parse_pagination,
    utc_now_iso,
)

router = APIRouter(prefix="/api/v1/shadow", tags=["shadow"])


def _try(fn: Any, resource: str) -> Any:
    try:
        return fn()
    except Exception:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(
            get_logger("nexus_scalp.web.api_v1"),
            f"/api/v1/shadow {resource}",
            None,
            RuntimeError(f"{resource} read failed"),
            resource=resource,
        )
        return None


@router.get("/status", summary="Shadow system state (both stores' real summaries)")
def shadow_status(request: Request) -> Any:
    repo = get_audit_repo(request)

    def _run60() -> Any:
        from nexus_scalp.shadow.store import ShadowStore

        return ShadowStore(audit_repo=repo).summary()

    def _run70() -> Any:
        from nexus_scalp.shadow.shadow70.store import Shadow70Store

        return Shadow70Store(audit_repo=repo).summary()

    return ok(
        request,
        {
            "shadow_60d": _try(_run60, "shadow_store"),
            "shadow_70d": _try(_run70, "shadow70_store"),
            "generated_at": utc_now_iso(),
        },
    )


@router.get("/runs", summary="Paginated shadow run inventory")
def shadow_runs(
    request: Request,
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Any:
    checked = parse_pagination(page, page_size)
    if not isinstance(checked, tuple):
        return checked
    p, ps = checked
    repo = get_audit_repo(request)

    def _run() -> Any:
        from nexus_scalp.shadow.store import ShadowStore

        return ShadowStore(audit_repo=repo).list_runs(limit=ps + 1 + (p - 1) * ps)

    runs = _try(_run, "shadow_runs")
    if runs is None:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="shadow store unavailable")
    start = (p - 1) * ps
    page_rows = runs[start : start + ps]
    has_more = len(runs) > start + ps
    for r in page_rows:
        for f in ("started_at", "finished_at", "created_at", "timestamp"):
            if f in r:
                r[f] = iso_or_none(r.get(f))
    return ok(request, build_page(page_rows, p, ps, has_more=has_more))


@router.get("/runs/{run_id}", summary="Shadow run detail (comparison + promotion when present)")
def shadow_run_detail(request: Request, run_id: str) -> Any:
    repo = get_audit_repo(request)

    def _run() -> Any:
        from nexus_scalp.shadow.store import ShadowStore

        return ShadowStore(audit_repo=repo).get_run(run_id)

    def _cmp() -> Any:
        from nexus_scalp.shadow.store import ShadowStore

        return ShadowStore(audit_repo=repo).get_comparison(run_id)

    def _promo() -> Any:
        from nexus_scalp.shadow.store import ShadowStore

        return ShadowStore(audit_repo=repo).get_promotion(run_id)

    run = _try(_run, "shadow_run")
    if run is None:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"shadow run {run_id} not found")
    return ok(
        request,
        {
            "run": run,
            "comparison": _try(_cmp, "shadow_comparison"),
            "promotion": _try(_promo, "shadow_promotion"),
        },
    )


@router.get("/decisions", summary="Paginated shadow decision records (run_id filter)")
def shadow_decisions(
    request: Request,
    run_id: str | None = Query(None, max_length=64),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Any:
    checked = parse_pagination(page, page_size)
    if not isinstance(checked, tuple):
        return checked
    p, ps = checked
    repo = get_audit_repo(request)

    def _run() -> Any:
        from nexus_scalp.shadow.store import ShadowStore

        return ShadowStore(audit_repo=repo).list_decisions(
            run_id=run_id, limit=ps + 1 + (p - 1) * ps
        )

    rows = _try(_run, "shadow_decisions")
    if rows is None:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="shadow store unavailable")
    start = (p - 1) * ps
    page_rows = rows[start : start + ps]
    has_more = len(rows) > start + ps
    return ok(request, build_page(page_rows, p, ps, has_more=has_more))


@router.get("/70d", summary="70D shadow observer: health + disagreements + drift")
def shadow_70d(request: Request) -> Any:
    repo = get_audit_repo(request)

    def _summary() -> Any:
        from nexus_scalp.shadow.shadow70.store import Shadow70Store

        return Shadow70Store(audit_repo=repo).summary()

    def _disagree() -> Any:
        from nexus_scalp.shadow.shadow70.store import Shadow70Store

        return Shadow70Store(audit_repo=repo).disagreement_counts()

    def _drift() -> Any:
        from nexus_scalp.shadow.shadow70.store import Shadow70Store

        return Shadow70Store(audit_repo=repo).latest_drift_alerts(limit=25)

    def _health() -> Any:
        from nexus_scalp.shadow.shadow70.store import Shadow70Store

        return Shadow70Store(audit_repo=repo).latest_feature_health()

    return ok(
        request,
        {
            "summary": _try(_summary, "shadow70_summary"),
            "disagreement_counts": _try(_disagree, "shadow70_disagreements"),
            "drift_alerts": _try(_drift, "shadow70_drift"),
            "feature_health": _try(_health, "shadow70_feature_health"),
            "generated_at": utc_now_iso(),
        },
    )
