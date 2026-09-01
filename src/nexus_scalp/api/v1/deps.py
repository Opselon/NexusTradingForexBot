"""API v1 dependency resolution helpers (CHG-0043).

Centralizes HOW v1 routes reach the real backends (audit repository, incident
store, research/shadow stores, engine observability) with the truthful-failure
contract: a missing/broken dependency becomes DEPENDENCY_UNAVAILABLE (503,
retryable) - never a fabricated empty success.

Design rules:
* Late imports only (route call time) - a broken optional import must not kill
  the whole v1 surface.
* One engine accessor shared by all routes (app.state.engine, same as legacy).
* Bounded, read-only repository access via the EXISTING store classes.

USED BY: src/nexus_scalp/api/v1/*.py routers.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException, Request

from nexus_scalp.api.v1.common import utc_now_iso


def _dep_unavailable(what: str, exc: Exception | None = None) -> HTTPException:
    """503 with stable v1 code; internal detail stays in logs."""
    if exc is not None:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(
            get_logger("nexus_scalp.api.v1"),
            "dependency",
            None,
            exc,
            resource=what,
        )
    return HTTPException(
        status_code=503,
        detail={"code": "DEPENDENCY_UNAVAILABLE", "dependency": what},
    )


def get_engine(request: Request) -> Any:
    """Live engine reference (may be None when the web server runs standalone).

    Returns the engine object or None; routes decide their own
    available/degraded semantics (mirrors the legacy /api/status behavior).
    """
    return getattr(request.app.state, "engine", None)


def get_audit_repository(request: Request) -> Any:
    """Authoritative audit repository.

    Resolution order: engine-attached repository (the runtime authority) ->
    a standalone repository over the canonical audit DB path (read-only
    operations only). Raises 503 DEPENDENCY_UNAVAILABLE when neither exists.
    """
    engine = get_engine(request)
    repo = getattr(engine, "audit", None) if engine is not None else None
    if repo is not None:
        return repo
    try:
        from nexus_scalp.adapters.database.audit_repository import AuditRepository
        from nexus_scalp.database.config import load_database_config

        config = load_database_config("audit")
        return AuditRepository(config=config)
    except Exception as exc:  # pragma: no cover - depends on local env
        raise _dep_unavailable("audit_repository", exc) from exc


def get_incident_store(request: Request) -> Any:
    """Incident store (TASK-12). Engine-attached first, standalone fallback."""
    engine = get_engine(request)
    worker = getattr(engine, "incident_worker", None) if engine is not None else None
    store = getattr(worker, "store", None) if worker is not None else None
    if store is not None:
        return store
    try:
        from nexus_scalp.incidents.store import IncidentStore

        return IncidentStore(db_path_for_audit(request))
    except Exception as exc:  # pragma: no cover - depends on local env
        raise _dep_unavailable("incident_store", exc) from exc


def db_path_for_audit(request: Request) -> str:
    """Canonical audit DB path (server helper reused, falls back to engine db)."""
    try:
        from nexus_scalp.web.server import db_path_for_audit as _srv_path

        return _srv_path()
    except Exception:
        engine = get_engine(request)
        repo = getattr(engine, "audit", None) if engine is not None else None
        path = getattr(repo, "_db_path", None)
        if isinstance(path, str) and path:
            return path
        raise _dep_unavailable("audit_db_path") from None


def sqlite_query_bounded(
    db_path: str,
    sql: str,
    args: tuple[Any, ...],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Executes a parameterized, LIMIT-bounded read-only query.

    SECURITY: sql is ALWAYS a module-internal constant with ``?`` placeholders;
    user input never reaches this function as SQL text (only as bound args).
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                sql, (*args, limit) if sql.rstrip().endswith("LIMIT ?") else args
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        raise _dep_unavailable("sqlite", exc) from exc


def subsystem_block(name: str, fn: Any) -> dict[str, Any]:
    """Wraps a subsystem probe into {status, detail} without fake fallbacks."""
    try:
        value = fn()
    except Exception as exc:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        log_web_error(get_logger("nexus_scalp.api.v1"), f"probe:{name}", None, exc)
        return {
            "status": "UNAVAILABLE",
            "error_code": "DEPENDENCY_UNAVAILABLE",
            "probed_at": utc_now_iso(),
        }
    if value is None:
        return {"status": "UNAVAILABLE", "probed_at": utc_now_iso()}
    return {"status": "AVAILABLE", "probed_at": utc_now_iso(), "detail": value}
