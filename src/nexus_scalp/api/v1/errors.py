"""API v1 exception -> error-envelope translation (CHG-0043).

Registers FastAPI exception handlers on the v1 router's parent app so every
error leaving /api/v1 uses the single v1 error contract (code/message/details/
request_id/retryable) with NO stack traces and NO internal text. Request
validation failures (FastAPI/Pydantic 422) are translated from their verbose
default body into the same envelope, keeping only field-level summaries.

USED BY: web/api_v1_wiring.py (register_v1_exception_handlers).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from nexus_scalp.api.v1.common import ERROR_CODES, error_envelope


def _is_v1_path(path: str) -> bool:
    return path.startswith("/api/v1") or path in {"/api/v1", "/api/v1/openapi.json"}


def register_v1_exception_handlers(app: FastAPI) -> None:
    """Attach v1-scoped exception handlers (path-guarded; legacy routes unaffected)."""

    @app.exception_handler(RequestValidationError)
    async def _v1_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        if not _is_v1_path(request.url.path):
            return JSONResponse(status_code=422, content={"detail": list(exc.errors())})
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
        body = error_envelope(
            request,
            "VALIDATION_ERROR",
            message="Request validation failed.",
            details={"errors": details},
        )
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(StarletteHTTPException)
    async def _v1_http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if not _is_v1_path(request.url.path):
            detail = exc.detail if isinstance(exc.detail, (str, dict, list)) else None
            return JSONResponse(status_code=exc.status_code, content={"detail": detail})
        # Map status -> v1 code; use the status-semantic code, keep v1 message.
        code = next(
            (c for c, spec in ERROR_CODES.items() if spec["status"] == exc.status_code),
            "INTERNAL_ERROR" if exc.status_code >= 500 else "BAD_REQUEST",
        )
        # 404/405 are NOT_FOUND / METHOD_NOT_ALLOWED specifically:
        if exc.status_code == 404:
            code = "NOT_FOUND"
        elif exc.status_code == 405:
            code = "METHOD_NOT_ALLOWED"
        elif exc.status_code == 409:
            code = "CONFLICT"
        elif exc.status_code == 413:
            code = "PAYLOAD_TOO_LARGE"
        elif exc.status_code == 504:
            code = "TIMEOUT"
        body = error_envelope(request, code)
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def _v1_unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        if not _is_v1_path(request.url.path):
            raise exc
        # Full detail to logs only (same discipline as web.errors.log_web_error).
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.web.errors import log_web_error

        rid = getattr(request.state, "request_id", None)
        log_web_error(get_logger("nexus_scalp.api.v1"), request.url.path, rid, exc)
        body = error_envelope(request, "INTERNAL_ERROR", message="Internal server error.")
        return JSONResponse(status_code=500, content=body)
