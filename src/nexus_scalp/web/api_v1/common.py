"""API Platform v1 — shared envelope, errors, pagination, accessors.

API_V1_ENVELOPE v1 / API_V1_ERRORS v1 / API_V1_PAGINATION v1 (CHG-0043).

BOUNDARY: this is the ONLY module in ``web/api_v1`` that knows about
- the envelope shape (``{data, meta}`` / ``{error}``),
- the request-id plumbing (reuses the existing correlation middleware from
  ``web/errors.py`` so v1 responses share one ``X-Request-ID`` stream with
  the legacy surface),
- engine / repository accessors.

Domain routers (system.py, market.py, ...) contain route bodies only and
import helpers from here. No god router, no god schema.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Error contract
# ---------------------------------------------------------------------------

#: code -> (HTTP status, retryable)
_VALIDATION: tuple[int, bool] = (422, False)
#: code -> (HTTP status, retryable); METHOD_NOT_ALLOWED / PAYLOAD_TOO_LARGE are
#: aliases of the validation family for the handler mapping above.
ERROR_SEMANTICS: dict[str, tuple[int, bool]] = {
    **{c: _VALIDATION for c in ("METHOD_NOT_ALLOWED", "PAYLOAD_TOO_LARGE")},
    "VALIDATION_ERROR": (422, False),
    "RESOURCE_NOT_FOUND": (404, False),
    "CONFLICT": (409, False),
    "FORBIDDEN": (403, False),
    "ENGINE_UNAVAILABLE": (503, True),
    "DEPENDENCY_UNAVAILABLE": (503, True),
    "RESOURCE_UNAVAILABLE": (503, True),
    "TIMEOUT": (504, True),
    "INTERNAL_ERROR": (500, False),
}

_ERROR_MESSAGES: dict[str, str] = {
    "VALIDATION_ERROR": "The request parameters were invalid.",
    "RESOURCE_NOT_FOUND": "The requested resource was not found.",
    "CONFLICT": "The request conflicts with the current state.",
    "FORBIDDEN": "The requested operation is not allowed.",
    "ENGINE_UNAVAILABLE": "The trading engine is not attached to the API server.",
    "DEPENDENCY_UNAVAILABLE": "A required dependency is not available.",
    "RESOURCE_UNAVAILABLE": "The requested resource is temporarily unavailable.",
    "TIMEOUT": "The request timed out.",
    "INTERNAL_ERROR": "The server could not complete this request.",
}


def _request_id(request: Request | None) -> str:
    """Reuse the correlation middleware's request_id when present."""
    if request is not None:
        rid = getattr(request.state, "request_id", None)
        if rid:
            return str(rid)[:64]
    from nexus_scalp.web.errors import new_request_id

    return new_request_id()


def utc_now_iso() -> str:
    """Transport timestamp contract: ISO-8601, timezone-aware, UTC."""
    return datetime.now(UTC).isoformat()


def fail(
    request: Request | None,
    code: str,
    *,
    message: str | None = None,
    details: dict[str, Any] | None = None,
    status_code: int | None = None,
) -> JSONResponse:
    """Standard v1 error envelope. Never exposes tracebacks, paths or secrets."""
    http, retryable = ERROR_SEMANTICS.get(code, ERROR_SEMANTICS["INTERNAL_ERROR"])
    if status_code is not None:
        http = status_code
    rid = _request_id(request)
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message or _ERROR_MESSAGES.get(code, _ERROR_MESSAGES["INTERNAL_ERROR"]),
            "details": details or {},
            "request_id": rid,
            "retryable": retryable,
        }
    }
    return JSONResponse(status_code=http, content=body)


def fail_internal_logged(
    request: Request | None,
    logger: Any,
    endpoint: str,
    exc: BaseException,
    *,
    code: str = "INTERNAL_ERROR",
    message: str | None = None,
) -> JSONResponse:
    """Full detail to logs (via the hardened web-error logger), safe envelope out."""
    from nexus_scalp.web.errors import log_web_error

    rid = _request_id(request)
    log_web_error(logger, endpoint, rid, exc)
    return fail(request, code, message=message)


def ok(
    request: Request | None,
    data: Any,
    *,
    meta_extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    idempotency_key: str | None = None,
    status_code: int = 200,
) -> JSONResponse:
    """Standard v1 success envelope.

    ``Idempotency-Key`` request headers on POSTs are echoed in
    ``meta.idempotency_key`` (spec §8: safe mutation semantics; no state is
    created twice — refresh/diagnostics/validate are naturally idempotent).
    All payload leaves pass through ``jsonable`` so datetimes/enums serialize
    to ISO-8601 UTC / string values (spec §12 time semantics).
    """
    meta: dict[str, Any] = {"request_id": _request_id(request), "generated_at": utc_now_iso()}
    if idempotency_key:
        meta["idempotency_key"] = idempotency_key[:128]
    if meta_extra:
        meta.update(meta_extra)
    headers = headers or {}
    rid = meta["request_id"]
    headers.setdefault("X-Request-ID", rid)
    return JSONResponse(
        status_code=status_code,
        content={"data": jsonable(data), "meta": meta},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Pagination (API_V1_PAGINATION v1): page/page_size, has_more, no counts
# ---------------------------------------------------------------------------

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def parse_pagination(page: int, page_size: int) -> tuple[int, int] | JSONResponse:
    """Validates page/page_size. Returns (page, page_size) or a 422 response."""
    if page < 1:
        return fail(None, "VALIDATION_ERROR", details={"page": "must be >= 1"})
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        return fail(
            None,
            "VALIDATION_ERROR",
            details={"page_size": f"must be between 1 and {MAX_PAGE_SIZE}"},
        )
    return page, page_size


def build_page(
    rows: list[dict[str, Any]],
    page: int,
    page_size: int,
    *,
    has_more: bool,
) -> dict[str, Any]:
    """Assembles the single canonical pagination shape."""
    return {
        "items": rows[:page_size],
        "page": page,
        "page_size": page_size,
        "has_more": bool(has_more),
    }


# ---------------------------------------------------------------------------
# Sanitization (secrets & leakage)
# ---------------------------------------------------------------------------

_SECRET_KEY_RE = re.compile(
    r"(token|password|secret|apikey|api_key|credential|login|account_number)",
    re.IGNORECASE,
)
_MASK = "***"


def sanitize_config(obj: Any) -> Any:
    """Recursively masks secret-shaped keys; leaves structure otherwise intact."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = _MASK
            else:
                out[k] = sanitize_config(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitize_config(v) for v in obj]
    return obj


def sanitize_record_secrets(obj: Any) -> Any:
    """Record-level variant: same masking without recursing into enum leaves."""
    return sanitize_config(obj)


# ---------------------------------------------------------------------------
# Shared accessors (engine / repository) — lazily built, cached on app.state
# ---------------------------------------------------------------------------


def get_engine(request: Request) -> Any:
    return getattr(request.app.state, "engine", None)


def get_versioner(request: Request) -> Any:
    return getattr(request.app.state, "versioner", None)


def get_audit_repo(request: Request) -> Any:
    """Shared AuditRepository for v1 (cached on app.state).

    Uses the authoritative audit DatabaseConfig resolved by the web layer
    (DATABASE PORTABILITY). Builds lazily; raises to caller on failure.
    """
    repo = getattr(request.app.state, "audit_v1_repo", None)
    if repo is None:
        from nexus_scalp.adapters.database.audit_repository import AuditRepository
        from nexus_scalp.web.server import _default_audit_config

        repo = AuditRepository(config=_default_audit_config())
        request.app.state.audit_v1_repo = repo
    return repo


def engine_or_503(request: Request) -> tuple[Any, JSONResponse | None]:
    """(engine, None) when attached; (None, 503-envelope) otherwise."""
    engine = get_engine(request)
    if engine is None:
        return None, fail(request, "ENGINE_UNAVAILABLE")
    return engine, None


def adapter_or_503(request: Request) -> tuple[Any, JSONResponse | None]:
    """(adapter, None) when engine attached; (None, 503) otherwise."""
    adapter = get_engine_adapter(request)
    if adapter is None:
        return None, fail(request, "ENGINE_UNAVAILABLE")
    return adapter, None


def get_engine_adapter(request: Request) -> Any:
    """Engine's adapter or None — for routes that degrade truthfully instead of 503."""
    engine = get_engine(request)
    return getattr(engine, "adapter", None) if engine is not None else None


# ---------------------------------------------------------------------------
# Bounded DB helpers (repository-level reads; parameterized SQL only)
# ---------------------------------------------------------------------------


def fetch_rows_bounded(
    repo: Any,
    sql: str,
    args: tuple[Any, ...],
    limit: int,
) -> list[dict[str, Any]]:
    """Runs a bounded parameterized SELECT via the repo's SQLite path.

    Used by v1 routes whose store layer lacks a paginated API; LIMIT is
    always injected server-side (never client-controlled beyond the cap).
    """
    if not getattr(repo, "_is_sqlite", False):
        return []
    bounded = max(1, min(int(limit), 200 * 25))  # hard safety ceiling
    try:
        conn = sqlite3.connect(getattr(repo, "_db_path", ""), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(sql + " LIMIT ?", (*args, bounded))
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def iso_or_none(value: Any) -> str | None:
    """Best-effort ISO conversion for DB timestamps (already UTC in NSE)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def jsonable(obj: Any) -> Any:
    """Recursively converts datetime/enum leaves to transport-safe values."""
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return iso_or_none(obj)
    if isinstance(obj, Enum):
        return obj.value
    return obj


def make_data_transform(fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Placeholder-free identity helper kept for explicitness in routes."""
    return fn
