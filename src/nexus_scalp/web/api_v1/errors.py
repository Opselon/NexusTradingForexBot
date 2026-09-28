"""API v1 exception -> error-envelope translation (CHG-0043, TASK-API-PLATFORM).

WHERE/WHY: the canonical v1 tree is ``nexus_scalp.web.api_v1`` (spec of record:
docs/api/API_PLATFORM_V1.md). This module is the port of the exception-handler
layer from the consolidated predecessor (nexus_scalp.api.v1.errors, removed in
the consolidation) so that EVERY error leaving /api/v1 uses the single v1 error
contract (code/message/details/request_id/retryable) with NO stack traces and NO
internal text. Request validation failures (FastAPI/Pydantic 422) are translated
from their verbose default body into the same envelope, keeping only bounded
field-level summaries.

Path-guarded: legacy routes (the 257-route dashboard surface) are unaffected —
handlers pass through to the legacy behavior for any non-/api/v1 path.

USED BY: web/api_v1_wiring.py (register_v1_exception_handlers).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from nexus_scalp.web.api_v1.common import ERROR_SEMANTICS, _request_id, fail

#: JSON-serializable placeholder for any request-body bytes that reach the
#: validation error payload. Raw binary must never be echoed back in a JSON
#: error response: ``json.dumps`` raises ``TypeError: Object of type bytes is
#: not JSON serializable`` on it, which converts a clean 4xx client error into
#: a 500 with an empty body and an ASGI traceback (observed live on
#: ``/api/db/console/query`` with an ``application/x-www-form-urlencoded``
#: body: FastAPI records the raw body as ``input=b'sql=...&database=audit'``
#: and the handler then failed at serialization time).
_BINARY_BODY_PLACEHOLDER = "<binary payload>"


def _json_safe(value: Any) -> Any:
    """Recursively coerce a validation-error payload to JSON-serializable form.

    ``RequestValidationError.errors()`` carries the raw request body under its
    ``input`` key. A body sent with a non-JSON content type (form-encoded,
    octet-stream, or a raw byte string) arrives as ``bytes``; a deeply nested
    or oversized body arrives as arbitrarily nested containers. Both must be
    rendered safe BEFORE they reach ``JSONResponse``, because the response
    builder runs OUTSIDE any exception handler and its ``TypeError`` is the
    secondary crash the operator sees instead of the real 4xx.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return _BINARY_BODY_PLACEHOLDER
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value][:50]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in list(value.items())[:50]}
    # Any other object (set, custom type, ...): a stable textual form, never
    # an exception from the JSON encoder.
    return _BINARY_BODY_PLACEHOLDER


def _safe_validation_detail(exc: RequestValidationError) -> list[dict[str, Any]]:
    """``exc.errors()`` with every ``input`` value made JSON-safe."""
    safe: list[dict[str, Any]] = []
    for err in list(exc.errors())[:20]:  # bounded: no huge payloads
        entry = dict(err)
        entry["input"] = _json_safe(err.get("input"))
        loc = err.get("loc", ())
        entry["loc"] = [str(p) for p in loc]
        safe.append(entry)
    return safe


def _is_v1_path(path: str) -> bool:
    return path.startswith("/api/v1")


def register_v1_exception_handlers(app: FastAPI) -> None:
    """Attach v1-scoped exception handlers (path-guarded; legacy routes unaffected)."""

    @app.exception_handler(RequestValidationError)
    async def _v1_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        if not _is_v1_path(request.url.path):
            # Legacy surface keeps FastAPI's default 422 body — but serialized
            # safely. ``exc.errors()`` embeds the raw request body under
            # ``input``; a body delivered with a non-JSON content type
            # (form-encoded, octet-stream) arrives as ``bytes`` and the default
            # ``JSONResponse`` cannot encode bytes. The response then fails
            # AFTER the handler returns, surfacing as HTTP 500 + empty body +
            # an ASGI traceback instead of the real 4xx. This handler is the
            # last point that can still translate the failure safely.
            return JSONResponse(
                status_code=422,
                content={"detail": _safe_validation_detail(exc)},
            )
        details: list[dict[str, Any]] = []
        for err in list(exc.errors())[:20]:  # bounded: no huge payloads
            details.append(
                {
                    "field": ".".join(str(p) for p in err.get("loc", [])[1:])
                    or str(err.get("loc", [])),
                    "issue": err.get("type", "validation"),
                    "input_present": err.get("input") is not None,
                }
            )
        return fail(
            request,
            "VALIDATION_ERROR",
            message="Request validation failed.",
            details={"errors": details},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _v1_http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if not _is_v1_path(request.url.path):
            detail = exc.detail if isinstance(exc.detail, (str, dict, list)) else None
            return JSONResponse(status_code=exc.status_code, content={"detail": detail})
        # Map HTTP status onto the v1 code family (spec §3).
        code = next(
            (c for c, (status, _retryable) in ERROR_SEMANTICS.items() if status == exc.status_code),
            "INTERNAL_ERROR" if exc.status_code >= 500 else "BAD_REQUEST",
        )
        if exc.status_code == 404:
            code = "RESOURCE_NOT_FOUND"
        elif exc.status_code == 405:
            code = "METHOD_NOT_ALLOWED"
        elif exc.status_code == 409:
            code = "CONFLICT"
        elif exc.status_code == 413:
            code = "PAYLOAD_TOO_LARGE"
        elif exc.status_code == 504:
            code = "TIMEOUT"
        return fail(request, code)

    @app.exception_handler(Exception)
    async def _v1_unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        if not _is_v1_path(request.url.path):
            raise exc
        # Full detail to logs only (same discipline as web.errors.log_web_error).
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        rid = _request_id(request)
        log_web_error(get_logger("nexus_scalp.web.api_v1"), request.url.path, rid, exc)
        return fail(request, "INTERNAL_ERROR", message="Internal server error.")
