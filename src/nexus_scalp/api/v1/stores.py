"""API v1 RESEARCH + SHADOW + INCIDENTS + OBSERVABILITY + DATABASE routers (CHG-0043).

Truthful inventory/status surface over existing stores:
  GET /api/v1/research/status        - research health summary (research/store.py)
  GET /api/v1/research/runs          - paginated research run inventory
  GET /api/v1/research/registry      - strategy registry inventory (lifecycle states)
  GET /api/v1/shadow/status          - 60D + 70D shadow summaries
  GET /api/v1/shadow/runs            - shadow run inventory (paginated)
  GET /api/v1/shadow/comparisons     - primary vs shadow comparisons
  GET /api/v1/incidents              - paginated incident inventory (IncidentStore)
  GET /api/v1/incidents/{id}         - incident detail (404 truthfully)
  GET /api/v1/incidents/{id}/timeline - incident timeline events
  GET /api/v1/observability/events   - recent audit events (bounded)
  GET /api/v1/observability/metrics  - process/API metrics (real counters)
  GET /api/v1/database/status        - audit DB presence/size/integrity (PRAGMA)
  GET /api/v1/audit/events           - audit event tail (bounded, filtered)

Read-only everywhere; bounded queries; truthful 503/404 on failure.
USED BY: web/api_v1_wiring.py.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from nexus_scalp.api.v1.common import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    clamp_limit,
    envelope,
    iso_or_none,
    utc_now_iso,
)
from nexus_scalp.api.v1.deps import (
    db_path_for_audit,
    get_audit_repository,
    get_engine,
    get_incident_store,
    sqlite_query_bounded,
)

router = APIRouter(
    prefix="/api/v1", tags=["research", "shadow", "incidents", "observability", "database", "audit"]
)


def _try(fn: Any, what: str) -> Any:
    """Runs a store call; translates failure into truthful 503."""
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as exc:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(get_logger("nexus_scalp.api.v1"), what, None, exc, resource=what)
        raise HTTPException(
            status_code=503, detail={"code": "DEPENDENCY_UNAVAILABLE", "dependency": what}
        ) from exc


# ---------------------------------------------------------------------------
# RESEARCH
# ---------------------------------------------------------------------------


@router.get("/research/status", summary="Research subsystem status (health summary)")
def research_status(request: Request) -> dict[str, Any]:
    repo = get_audit_repository(request)

    def _run() -> Any:
        from nexus_scalp.research.store import research_health_summary

        return research_health_summary(repo)

    summary = _try(_run, "research_store")
    return envelope(request, {"available": summary is not None, "health": summary})


@router.get("/research/runs", summary="Paginated research run inventory")
def research_runs(
    request: Request,
    strategy_id: str | None = Query(None, max_length=64),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> dict[str, Any]:
    repo = get_audit_repository(request)

    def _run() -> Any:
        from nexus_scalp.research.store import list_research_runs

        return list_research_runs(repo, strategy_id=strategy_id, limit=MAX_PAGE_SIZE * 25)

    runs = _try(_run, "research_runs")
    start = (page - 1) * page_size
    page_rows = runs[start : start + page_size]
    for r in page_rows:
        for f in ("executed_at", "created_at", "timestamp"):
            if f in r:
                r[f] = iso_or_none(r.get(f))
    total = len(runs)
    pagination = {
        "page": page,
        "page_size": page_size,
        "total_items": total,
        "has_next": start + page_size < total,
        "has_prev": page > 1,
    }
    return envelope(request, {"runs": page_rows}, pagination=pagination)


@router.get("/research/registry", summary="Strategy registry inventory (lifecycle states)")
def research_registry(
    request: Request,
    lifecycle: str | None = Query(None, max_length=32),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    repo = get_audit_repository(request)

    def _run() -> Any:
        from nexus_scalp.research.registry import StrategyRegistry

        return StrategyRegistry(audit_repo=repo).list(lifecycle=lifecycle, limit=clamp_limit(limit))

    entries = _try(_run, "research_registry")
    out = []
    for e in entries:
        dump = e.model_dump() if hasattr(e, "model_dump") else getattr(e, "__dict__", {})
        out.append(dump)
    return envelope(request, {"entries": out, "count": len(out)})


# ---------------------------------------------------------------------------
# SHADOW
# ---------------------------------------------------------------------------


@router.get("/shadow/status", summary="Shadow system state (60D + 70D summaries)")
def shadow_status(request: Request) -> dict[str, Any]:
    repo = get_audit_repository(request)

    def _run60() -> Any:
        from nexus_scalp.shadow.store import ShadowStore

        return ShadowStore(audit_repo=repo).summary()

    def _run70() -> Any:
        from nexus_scalp.shadow.shadow70.store import Shadow70Store

        return Shadow70Store(audit_repo=repo).summary()

    return envelope(
        request,
        {
            "shadow_60d": _try(_run60, "shadow_store"),
            "shadow_70d": _try(_run70, "shadow70_store"),
            "generated_at": utc_now_iso(),
        },
    )


@router.get("/shadow/runs", summary="Shadow run inventory (paginated)")
def shadow_runs(
    request: Request,
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> dict[str, Any]:
    repo = get_audit_repository(request)

    def _run() -> Any:
        from nexus_scalp.shadow.store import ShadowStore

        return ShadowStore(audit_repo=repo).list_runs(limit=MAX_PAGE_SIZE * 25)

    runs = _try(_run, "shadow_runs")
    start = (page - 1) * page_size
    page_rows = runs[start : start + page_size]
    total = len(runs)
    pagination = {
        "page": page,
        "page_size": page_size,
        "total_items": total,
        "has_next": start + page_size < total,
        "has_prev": page > 1,
    }
    return envelope(request, {"runs": page_rows}, pagination=pagination)


@router.get("/shadow/comparisons", summary="Primary vs shadow comparisons (per run)")
def shadow_comparisons(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    repo = get_audit_repository(request)

    def _run() -> Any:
        from nexus_scalp.shadow.store import ShadowStore

        store = ShadowStore(audit_repo=repo)
        runs = store.list_runs(limit=clamp_limit(limit))
        out = []
        for r in runs:
            run_id = r.get("run_id") or r.get("id")
            if not run_id:
                continue
            comp = store.get_comparison(run_id)
            if comp is not None:
                out.append({"run_id": run_id, "comparison": comp})
        return out

    comparisons = _try(_run, "shadow_comparisons")
    return envelope(request, {"comparisons": comparisons, "count": len(comparisons)})


# ---------------------------------------------------------------------------
# INCIDENTS
# ---------------------------------------------------------------------------


@router.get("/incidents", summary="Paginated incident inventory")
def incidents_list(
    request: Request,
    status: str | None = Query(None, max_length=24),
    severity: str | None = Query(None, max_length=24),
    category: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> dict[str, Any]:
    store = get_incident_store(request)

    def _run() -> Any:
        return store.list_incidents(
            status=status,
            severity=severity,
            category=category,
            limit=page_size,
            offset=(page - 1) * page_size,
        )

    rows = _try(_run, "incident_store")
    out = [
        i.as_dict()
        if hasattr(i, "as_dict")
        else (i.model_dump() if hasattr(i, "model_dump") else str(i))
        for i in rows
    ]
    pagination = {
        "page": page,
        "page_size": page_size,
        "total_items": None,  # store counts bounded; exact total via filters endpoint
        "has_next": len(rows) == page_size,
        "has_prev": page > 1,
    }
    return envelope(request, {"incidents": out}, pagination=pagination)


@router.get("/incidents/stats", summary="Incident counts by severity/status/component")
def incidents_stats(request: Request) -> dict[str, Any]:
    store = get_incident_store(request)
    counts = _try(store.count, "incident_count")
    by_component = _try(store.stats_by_component, "incident_component_stats")
    return envelope(request, {"counts": counts, "by_component": by_component})


@router.get("/incidents/{incident_id}", summary="Incident detail by id")
def incident_detail(request: Request, incident_id: str) -> dict[str, Any]:
    store = get_incident_store(request)
    inc = _try(lambda: store.get(incident_id), "incident_store")
    if inc is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "resource": "incident", "id": incident_id}
        )
    data = inc.as_dict() if hasattr(inc, "as_dict") else inc.model_dump()
    return envelope(request, data)


@router.get("/incidents/{incident_id}/timeline", summary="Incident timeline events")
def incident_timeline(request: Request, incident_id: str) -> dict[str, Any]:
    store = get_incident_store(request)
    inc = _try(lambda: store.get(incident_id), "incident_store")
    if inc is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "resource": "incident", "id": incident_id}
        )
    events = getattr(inc, "timeline", None) or []
    out = [e.as_dict() if hasattr(e, "as_dict") else str(e) for e in events]
    return envelope(request, {"incident_id": incident_id, "timeline": out, "count": len(out)})


# ---------------------------------------------------------------------------
# OBSERVABILITY / AUDIT
# ---------------------------------------------------------------------------


@router.get("/observability/events", summary="Recent structured audit events (bounded tail)")
def observability_events(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    repo = get_audit_repository(request)
    rows = sqlite_query_bounded(
        repo._db_path,
        "SELECT id, event_type, created_at FROM audit_events ORDER BY id DESC LIMIT ?",
        (),
        limit=clamp_limit(limit),
    )
    for r in rows:
        r["created_at"] = iso_or_none(r.get("created_at"))
    return envelope(request, {"events": rows, "count": len(rows)})


@router.get("/observability/metrics", summary="Process/API metrics (real counters only)")
def observability_metrics(request: Request) -> dict[str, Any]:
    """Real, cheap, stdlib-only process counters. No fabricated numbers."""
    import resource
    import threading

    data: dict[str, Any] = {
        "thread_count": threading.active_count(),
        "daemon_thread_count": sum(1 for t in threading.enumerate() if t.daemon),
        "generated_at": utc_now_iso(),
    }
    try:
        import sys as _sys

        if _sys.platform == "win32":
            raise OSError("resource module is POSIX-only; counters unavailable")
        ru = resource.getrusage(resource.RUSAGE_SELF)  # type: ignore[attr-defined]
        data["cpu_user_sec"] = ru.ru_utime
        data["cpu_system_sec"] = ru.ru_stime
        data["max_rss_kb"] = ru.ru_maxrss
    except Exception:
        data["resource_usage"] = "UNAVAILABLE"  # truthful: not fabricated
    engine = get_engine(request)
    sse_diag = getattr(request.app.state, "sse_diag", None)
    if isinstance(sse_diag, dict):
        data["sse"] = {
            k: sse_diag.get(k)
            for k in ("connection", "event_count", "reconnect_count", "serialization_errors")
        }
    if engine is not None:
        peak = getattr(engine, "_peak_equity", None)
        if peak is not None:
            data["engine_peak_equity"] = peak
    return envelope(request, data)


@router.get("/audit/events", summary="Audit event tail (bounded, type-filtered)")
def audit_events(
    request: Request,
    event_type: str | None = Query(None, max_length=48),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    repo = get_audit_repository(request)
    where = "WHERE event_type = ?" if event_type else ""
    args: tuple[Any, ...] = (event_type,) if event_type else ()
    rows = sqlite_query_bounded(
        repo._db_path,
        f"SELECT * FROM audit_events {where} ORDER BY id DESC LIMIT ?",
        args,
        limit=clamp_limit(limit),
    )
    for r in rows:
        r["created_at"] = iso_or_none(r.get("created_at"))
    return envelope(request, {"events": rows, "count": len(rows)})


# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------


@router.get("/database/status", summary="Audit database state (presence, size, integrity)")
def database_status(request: Request) -> dict[str, Any]:
    path = db_path_for_audit(request)
    from pathlib import Path as _Path

    p = _Path(path)
    exists = p.exists()
    data: dict[str, Any] = {
        "path_filename_only": p.name,
        "exists": exists,
        "size_bytes": p.stat().st_size if exists else None,
        "probed_at": utc_now_iso(),
    }
    if exists:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
            try:
                row = conn.execute("PRAGMA integrity_check").fetchone()
                data["integrity_check"] = row[0] if row else None
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                data["table_count"] = len(tables)
                data["tables"] = [t[0] for t in tables][:60]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            data["integrity_check"] = "UNAVAILABLE"
            data["error_code"] = "DEPENDENCY_UNAVAILABLE"
            data["sqlite_error_class"] = type(exc).__name__
    return envelope(request, data)
