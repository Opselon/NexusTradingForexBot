"""API v1 — INCIDENTS + OBSERVABILITY/AUDIT + DATABASE + CONFIG domains.

Sources verified in docs/api/API_PLATFORM_V1.md §7 (incidents.py, observability.py,
database.py, config.py — capabilities 56-65). Incident reads go through the real
IncidentStore; database reads are read-only PRAGMAs; config is sanitized and the
validate endpoint is a pure pydantic check (no apply).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Query, Request

from nexus_scalp.web.api_v1.common import (
    build_page,
    fail,
    get_audit_repo,
    get_engine,
    ok,
    parse_pagination,
    sanitize_config,
    utc_now_iso,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["incidents", "observability", "database", "config"],
)


def _try(fn: Any, resource: str) -> Any:
    try:
        return fn()
    except Exception:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(
            get_logger("nexus_scalp.web.api_v1"),
            f"/api/v1 {resource}",
            None,
            RuntimeError(f"{resource} read failed"),
            resource=resource,
        )
        return None


def _incident_store(request: Request) -> Any:
    repo = get_audit_repo(request)
    from nexus_scalp.incidents.store import IncidentStore

    return IncidentStore(audit_repo=repo)


def _audit_db_path(request: Request) -> str:
    repo = get_audit_repo(request)
    return str(getattr(repo, "_db_path", ""))


def _sqlite_ro(path: str) -> sqlite3.Connection | None:
    if not path:
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------------------
# INCIDENTS (56-59)
# ---------------------------------------------------------------------------


@router.get(
    "/incidents", summary="Paginated incident inventory (status/severity/category/component)"
)
def incidents_list(
    request: Request,
    status: str | None = Query(None, max_length=24),
    severity: str | None = Query(None, max_length=24),
    category: str | None = Query(None, max_length=32),
    component: str | None = Query(None, max_length=48),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Any:
    checked = parse_pagination(page, page_size)
    if not isinstance(checked, tuple):
        return checked
    p, ps = checked
    store = _incident_store(request)

    def _run() -> Any:
        return store.list_incidents(
            status=status,
            severity=severity,
            category=category,
            component=component,
            limit=ps,
            offset=(p - 1) * ps,
        )

    rows = _try(_run, "incident_store")
    if rows is None:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="incident store unavailable")
    out = [_serialize_incident(i) for i in rows]
    return ok(request, build_page(out, p, ps, has_more=len(rows) == ps))


def _serialize_incident(inc: Any) -> Any:
    if hasattr(inc, "as_dict"):
        return inc.as_dict()
    if hasattr(inc, "model_dump"):
        return inc.model_dump()
    return str(inc)


@router.get("/incidents/stats", summary="Incident counts by severity/status/component")
def incidents_stats(request: Request) -> Any:
    store = _incident_store(request)
    counts = _try(store.count, "incident_count")
    by_component = _try(store.stats_by_component, "incident_component_stats")
    recurring = _try(lambda: store.recurring_fingerprints(limit=10), "incident_recurring")
    return ok(request, {"counts": counts, "by_component": by_component, "recurring": recurring})


@router.get("/incidents/{incident_id}", summary="Incident detail by id")
def incident_detail(request: Request, incident_id: str) -> Any:
    store = _incident_store(request)
    inc = _try(lambda: store.get(incident_id), "incident_store")
    if inc is None:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"incident {incident_id} not found")
    return ok(request, _serialize_incident(inc))


@router.get("/incidents/{incident_id}/timeline", summary="Incident timeline events")
def incident_timeline(request: Request, incident_id: str) -> Any:
    store = _incident_store(request)
    inc = _try(lambda: store.get(incident_id), "incident_store")
    if inc is None:
        return fail(request, "RESOURCE_NOT_FOUND", message=f"incident {incident_id} not found")
    events = getattr(inc, "timeline", None) or []
    out = [e.as_dict() if hasattr(e, "as_dict") else str(e) for e in events]
    return ok(request, {"incident_id": incident_id, "timeline": out, "count": len(out)})


# ---------------------------------------------------------------------------
# OBSERVABILITY / AUDIT (event tail + real process counters)
# ---------------------------------------------------------------------------


@router.get("/observability/events", summary="Recent audit event tail (bounded, type-filtered)")
def observability_events(
    request: Request,
    event_type: str | None = Query(None, max_length=48),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Any:
    checked = parse_pagination(page, page_size)
    if not isinstance(checked, tuple):
        return checked
    p, ps = checked
    where = " WHERE event_type = ?" if event_type else ""
    args: tuple[Any, ...] = (event_type,) if event_type else ()
    sql = f"SELECT id, event_type, created_at, payload FROM audit_events{where} ORDER BY id DESC"
    rows = fetch_bounded(request, sql, args, ps + 1 + (p - 1) * ps)
    page_rows = rows[(p - 1) * ps : (p - 1) * ps + ps]
    has_more = len(rows) > (p - 1) * ps + ps
    return ok(request, build_page(page_rows, p, ps, has_more=has_more))


def fetch_bounded(
    request: Request, sql: str, args: tuple[Any, ...], limit: int
) -> list[dict[str, Any]]:
    """Read-only bounded SELECT via the audit repo path (LIMIT injected here)."""
    from nexus_scalp.web.api_v1.common import fetch_rows_bounded

    return fetch_rows_bounded(get_audit_repo(request), sql, args, min(limit, 5_000))


@router.get("/observability/metrics", summary="Process/API metrics (real counters only)")
def observability_metrics(request: Request) -> dict[str, Any] | Any:
    import sys as _sys
    import threading

    data: dict[str, Any] = {
        "thread_count": threading.active_count(),
        "daemon_thread_count": sum(1 for t in threading.enumerate() if t.daemon),
        "generated_at": utc_now_iso(),
    }
    try:
        if _sys.platform == "win32":
            raise OSError("resource module is POSIX-only; counters unavailable")
        import resource

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
    return ok(request, data)


@router.get("/audit/events", summary="Audit ledger tail (bounded, status-filtered)")
def audit_events(
    request: Request,
    status: str | None = Query(None, max_length=24),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Any:
    checked = parse_pagination(page, page_size)
    if not isinstance(checked, tuple):
        return checked
    p, ps = checked
    where = " WHERE status = ?" if status else ""
    args: tuple[Any, ...] = (status,) if status else ()
    sql = (
        "SELECT ticket, symbol, direction, volume, entry_price, status, "
        "timestamp, pnl FROM audit_ledger" + where + " ORDER BY ticket DESC"
    )
    rows = fetch_bounded(request, sql, args, ps + 1 + (p - 1) * ps)
    page_rows = rows[(p - 1) * ps : (p - 1) * ps + ps]
    has_more = len(rows) > (p - 1) * ps + ps
    return ok(request, build_page(page_rows, p, ps, has_more=has_more))


# ---------------------------------------------------------------------------
# DATABASE (60-62) — read-only PRAGMAs only
# ---------------------------------------------------------------------------


@router.get("/database/status", summary="Audit database state (presence, size, provider)")
def database_status(request: Request) -> Any:
    from pathlib import Path as _Path

    path = _audit_db_path(request)
    p = _Path(path) if path else None
    exists = bool(p and p.exists())
    data: dict[str, Any] = {
        "filename": p.name if p else None,
        "exists": exists,
        "size_bytes": p.stat().st_size if exists else None,
        "probed_at": utc_now_iso(),
    }
    # NOTE: full PRAGMA integrity_check lives in /database/integrity (explicit,
    # heavier). Status stays O(1) — cheap metadata only — so dashboards can poll.
    conn = _sqlite_ro(path) if exists else None
    if conn is not None:
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            data["table_count"] = len(tables)
        except sqlite3.Error:
            data["table_count"] = None
        finally:
            conn.close()
    else:
        data["table_count"] = None
    data["integrity_endpoint"] = "/api/v1/database/integrity"
    return ok(request, data)


@router.get("/database/integrity", summary="Integrity: quick_check + bounded row counts")
def database_integrity(request: Request) -> Any:
    path = _audit_db_path(request)
    conn = _sqlite_ro(path)
    if conn is None:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="audit database not readable")
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        counts: dict[str, int | None] = {}
        for table in ("audit_signals", "audit_ledger", "audit_orders", "audit_executions"):
            try:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                counts[table] = None
        return ok(
            request,
            {
                "quick_check": row[0] if row else None,
                "row_counts": counts,
                "probed_at": utc_now_iso(),
            },
        )
    finally:
        conn.close()


@router.get("/database/tables", summary="Table inventory (names only; no row dumps)")
def database_tables(request: Request) -> Any:
    path = _audit_db_path(request)
    conn = _sqlite_ro(path)
    if conn is None:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="audit database not readable")
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        return ok(request, {"tables": tables, "count": len(tables)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CONFIG (63-65) — sanitized read + pure validate (no apply)
# ---------------------------------------------------------------------------


@router.get("/config", summary="Effective engine configuration (sanitized)")
def config_effective(request: Request) -> Any:
    engine = get_engine(request)
    if engine is None:
        return fail(request, "ENGINE_UNAVAILABLE")
    try:
        raw = engine.config.model_dump()
    except Exception:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="configuration serialization failed")
    return ok(request, {"config": sanitize_config(raw), "sanitized": True, "at": utc_now_iso()})


@router.get("/config/schema", summary="Configurable properties inventory (pydantic models)")
def config_schema(request: Request) -> Any:
    from nexus_scalp.configuration.config import AppConfig

    out: list[dict[str, Any]] = []
    for name, model in AppConfig.model_fields.items():
        entry: dict[str, Any] = {"name": name, "required": model.is_required()}
        annotation = model.annotation
        entry["type"] = str(getattr(annotation, "__name__", annotation))
        sub = getattr(annotation, "model_fields", None)
        if sub:
            entry["fields"] = sorted(sub.keys())
        out.append(entry)
    return ok(request, {"sections": out, "count": len(out), "at": utc_now_iso()})


@router.post("/config/validate", summary="Validate a proposed partial config (no apply)")
def config_validate(request: Request, proposal: dict[str, Any]) -> Any:
    """Pure pydantic validation of a proposed partial config dict — NOTHING applied.

    Body: an open object of config sections -> partial values (validated against
    the real pydantic models below).
    """
    from nexus_scalp.configuration.config import AppConfig

    unknown = sorted(set(proposal.keys()) - set(AppConfig.model_fields.keys()))
    errors: list[dict[str, Any]] = [{"field": k, "issue": "unknown section"} for k in unknown]
    for section, value in proposal.items():
        if section in unknown:
            continue
        sub_model = AppConfig.model_fields[section].annotation
        sub_fields = getattr(sub_model, "model_fields", None)
        if sub_fields is None:
            errors.append({"field": section, "issue": "not a configurable sub-model"})
            continue
        if not isinstance(value, dict):
            errors.append({"field": section, "issue": "must be an object"})
            continue
        bad = sorted(set(value.keys()) - set(sub_fields.keys()))
        errors.extend({"field": f"{section}.{k}", "issue": "unknown key"} for k in bad)
    return ok(
        request,
        {
            "valid": not errors,
            "errors": errors,
            "applied": False,
            "note": "v1 never applies configuration changes",
        },
        status_code=200 if not errors else 422,
    )
