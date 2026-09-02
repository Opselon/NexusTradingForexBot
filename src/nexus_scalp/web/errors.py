"""Centralized safe error handling for the web control surface (DASHBOARD HARDENING).

Purpose
-------
Every FastAPI route, SSE generator and WebSocket handler reports failures through
this module so that:

* PUBLIC RESPONSES are sanitized: stable ``error.code``, generic ``message``,
  a correlation ``request_id`` and an HTTP status. No exception text, traceback,
  filesystem path, SQL statement, exception class name or secret ever reaches
  the browser.
* INTERNAL LOGS are detailed: endpoint, request_id, resource context and the
  real exception (+ traceback via ``logger.exception``) go to the structured
  logging system only.

Compatibility
-------------
Existing clients read ``available`` / ``success`` boolean fields. This module
preserves those fields and adds the ``error`` object. Legacy endpoints that
previously returned ``{"error": "<str>"}`` now receive ``{"error": {code,
message, request_id}}`` - code and message stay stable, the raw exception text
is never included.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Request-correlation ID plumbing
# ---------------------------------------------------------------------------


def new_request_id() -> str:
    """Returns a short, human-usable correlation id (e.g. ``req_ab12cd34``)."""
    return "req_" + uuid.uuid4().hex[:10]


def request_id_from_request(request: Request | None) -> str:
    """Reads an incoming ``X-Request-ID`` header when present, else generates one.

    The browser API client attaches the same id it shows the user, so log
    correlation with a specific UI error message is possible.
    """
    if request is not None:
        header = request.headers.get("x-request-id")
        if header and header.strip():
            return header.strip()[:64]
    return new_request_id()


# ---------------------------------------------------------------------------
# Public-safe error codes
# ---------------------------------------------------------------------------

ERROR_CODES = {
    "INTERNAL_ERROR": "The server could not complete this request.",
    "RESOURCE_NOT_FOUND": "The requested resource was not found.",
    "RESOURCE_UNAVAILABLE": "The requested resource is temporarily unavailable.",
    "BAD_REQUEST": "The request could not be processed.",
    "VALIDATION_ERROR": "The request payload was invalid.",
    "ENGINE_UNAVAILABLE": "The trading engine is not connected.",
    "OPERATION_FAILED": "The operation could not be completed.",
    "SSE_STREAM_ERROR": "The live stream was interrupted.",
    "WS_STREAM_ERROR": "The realtime session was interrupted.",
}


def safe_error_payload(
    code: str,
    message: str | None = None,
    request_id: str | None = None,
    available: bool = False,
    success: bool = False,
    extra: dict[str, Any] | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Builds the stable public error envelope.

    Example
    -------
    >>> safe_error_payload("RESOURCE_UNAVAILABLE", request_id="req_abc")
    {"available": False, "success": False, "error": {"code": ..., "message": ..., "request_id": ...}}
    """
    body: dict[str, Any] = {
        "available": available,
        "success": success,
        "error": {
            "code": code,
            "message": message or ERROR_CODES.get(code, ERROR_CODES["INTERNAL_ERROR"]),
            "request_id": request_id or new_request_id(),
        },
    }
    if extra:
        for k, v in extra.items():
            if k not in body:
                body[k] = v
    for k, v in kw.items():
        if k not in body:
            body[k] = v
    return body


def http_error_response(
    code: str,
    request_id: str | None = None,
    status_code: int = 500,
    message: str | None = None,
) -> JSONResponse:
    """JSONResponse variant for paths that need an explicit HTTP status."""
    return JSONResponse(
        status_code=status_code,
        content=safe_error_payload(code=code, message=message, request_id=request_id),
    )


# ---------------------------------------------------------------------------
# Logging helper - internal detail, never public
# ---------------------------------------------------------------------------


def log_web_error(
    logger: Any,
    endpoint: str,
    request_id: str | None,
    exc: BaseException,
    *,
    resource: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Logs the FULL internal failure with traceback.

    Emits one structured ``[WEB_ERROR]`` record: endpoint, request_id, error
    code, exception type, resource and the real traceback. This is the only
    place where exception internals are written (to logs, never to responses).
    Accepts either ``logging.Logger`` or a structlog ``BoundLogger``.
    """
    details: dict[str, Any] = {
        "event": "WEB_ERROR",
        "endpoint": endpoint,
        "request_id": request_id or "none",
        "exception_type": type(exc).__name__,
        "error": str(exc)[:500],
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:],
    }
    if resource:
        details["resource"] = resource
    if context:
        details.update({f"ctx_{k}": v for k, v in context.items()})
    logger.error(
        "WEB_ERROR endpoint=%s request_id=%s error_code=%s exception_type=%s resource=%s",
        endpoint,
        details["request_id"],
        details.get("error_code", ""),
        details["exception_type"],
        resource or "-",
        extra={"web_error": details},
    )


def make_error_handler(endpoint: str, logger: Any, request_id: str | None = None):
    """Partial builder for route-local error envelopes.

    Returns ``(log_fn, payload_fn)`` where ``log_fn`` writes the detailed record
    and ``payload_fn`` produces the safe public dict. Keeps every route uniform
    without twenty slightly different implementations.
    """
    rid = request_id or new_request_id()

    def log_fn(
        exc: BaseException, *, resource: str | None = None, ctx: dict[str, Any] | None = None
    ) -> None:
        log_web_error(logger, endpoint, rid, exc, resource=resource, context=ctx)

    def payload_fn(
        code: str = "INTERNAL_ERROR",
        *,
        available: bool = False,
        success: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return safe_error_payload(
            code=code, request_id=rid, available=available, success=success, extra=extra
        )

    return log_fn, payload_fn


# ---------------------------------------------------------------------------
# FastAPI middleware: attach request_id + sanitize 500s
# ---------------------------------------------------------------------------


async def attach_request_id_middleware(request: Request, call_next: Callable):
    """Attaches ``request.state.request_id`` and sanitizes unhandled 500s.

    Every response carries ``X-Request-ID`` so the browser/client can correlate
    a displayed error with the server log. Unhandled exceptions (which FastAPI
    would otherwise convert into a bare 500 with no body) become the standard
    safe error envelope - the traceback goes to ``logging`` only.
    """
    rid = request_id_from_request(request)
    request.state.request_id = rid

    # Do not attach a request_id / sanitize SSE streams: SSE has its own
    # error contract and the generator manages disconnects.
    if request.url.path.endswith("/api/ticks/stream"):
        try:
            return await call_next(request)
        except Exception:  # pragma: no cover - defensive
            logger = logging.getLogger("nexus_scalp.web.server")
            logger.exception("[WEB_ERROR] SSE stream middleware failure request_id=%s", rid)
            return JSONResponse(
                status_code=500,
                content=safe_error_payload("SSE_STREAM_ERROR", request_id=rid),
            )

    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response
