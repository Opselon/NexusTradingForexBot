"""Shared contracts for the versioned /api/v1 platform (CHG-0043, TASK-API-PLATFORM).

WHERE/WHY: the /api/v1 surface is a versioned, read-only, developer-facing API
platform layered OVER the existing control routes (which stay untouched). This
module owns the cross-cutting concerns every v1 endpoint shares:

* response envelope    - one canonical shape (meta + data) for every endpoint
* error envelope       - reuses web.errors safe_error_payload (same codes the
                         existing dashboard already consumes) extended with
                         ``retryable`` + ``details``; never leaks stack traces
* pagination           - ONE model (page / page_size, hard-bounded) used by all
                         paginated v1 endpoints; protects against unbounded scans
* filtering            - validated, bounded query parameters (symbol, status,
                         severity, time ranges) - never raw SQL fragments
* request identity     - continues the X-Request-ID correlation contract from
                         web.errors so a v1 call and a legacy call correlate in
                         the same logs
* time semantics       - UTC ISO-8601 in transport, always timezone-aware

BOUNDARY: no business logic lives here. This module never imports engine or
repository modules; routes resolve their own data sources lazily (same late-import
discipline the existing web modules follow) so a missing dependency surfaces as
the truthful DEPENDENCY_UNAVAILABLE error instead of an import-time crash.

USED BY: src/nexus_scalp/api/v1/*.py routers, web/api_v1_wiring.py,
tests/unit/test_api_v1_*.py.

DO-NOT-PUT-HERE: route handlers, domain models, repository access.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Query, Request
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants (single source for docs + tests)
# ---------------------------------------------------------------------------

API_VERSION = "v1"
API_VERSION_TAG = "api-v1"

#: Pagination hard bounds: protects every list endpoint from unbounded queries.
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
MAX_LIMIT = 500  # single-shot list endpoints (bounded by store conventions)

#: Stable ISO-8601 UTC "now" helper - the ONLY time source v1 responses use.


def utc_now_iso() -> str:
    """Current UTC time as ISO-8601 with explicit Z-style +00:00 offset."""
    return datetime.now(UTC).isoformat()


def iso_or_none(value: Any) -> str | None:
    """Best-effort ISO-8601 normalization of a stored timestamp value.

    Returns None for missing/invalid values (never fabricates a time).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        from datetime import datetime as _dt

        parsed = _dt.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.isoformat()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------


class MetaBlock(BaseModel):
    """Envelope metadata present on every v1 response."""

    request_id: str = Field(description="Correlation id (echoes X-Request-ID when supplied).")
    generated_at: str = Field(description="Server-side UTC ISO-8601 generation timestamp.")
    api_version: str = API_VERSION


class Envelope(BaseModel):
    """Canonical success envelope: ``{meta, data}`` (+ ``pagination`` on lists)."""

    meta: MetaBlock
    data: Any


def envelope(
    request: Request, data: Any, *, pagination: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Builds the canonical success payload for a v1 endpoint."""
    rid = getattr(request.state, "request_id", None) or "req_unknown"
    body: dict[str, Any] = {
        "meta": MetaBlock(request_id=rid, generated_at=utc_now_iso()).model_dump(),
        "data": data,
    }
    if pagination is not None:
        body["pagination"] = pagination
    return body


# ---------------------------------------------------------------------------
# Error envelope (extends the existing dashboard-safe contract)
# ---------------------------------------------------------------------------

#: Complete v1 error code set. Codes mirror web.errors.ERROR_CODES where they
#: overlap so legacy clients and logs keep one vocabulary; the v1-only codes
#: (NOT_FOUND vs RESOURCE_NOT_FOUND naming, DEPENDENCY_UNAVAILABLE, ...) are
#: explicit here and documented in docs/API_PLATFORM.md.
ERROR_CODES: dict[str, dict[str, Any]] = {
    "VALIDATION_ERROR": {"status": 422, "retryable": False},
    "BAD_REQUEST": {"status": 400, "retryable": False},
    "NOT_FOUND": {"status": 404, "retryable": False},
    "METHOD_NOT_ALLOWED": {"status": 405, "retryable": False},
    "CONFLICT": {"status": 409, "retryable": False},
    "READ_ONLY": {"status": 405, "retryable": False},
    "PAYLOAD_TOO_LARGE": {"status": 413, "retryable": False},
    "UNAVAILABLE": {"status": 503, "retryable": True},
    "DEPENDENCY_UNAVAILABLE": {"status": 503, "retryable": True},
    "TIMEOUT": {"status": 504, "retryable": True},
    "INTERNAL_ERROR": {"status": 500, "retryable": False},
}


def error_envelope(
    request: Request,
    code: str,
    *,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds the sanitized v1 error body.

    The exception TEXT never enters this function on purpose: callers pass a
    stable message; internal detail goes to logs via web.errors.log_web_error.
    """
    spec = ERROR_CODES.get(code, ERROR_CODES["INTERNAL_ERROR"])
    rid = getattr(request.state, "request_id", None) or "req_unknown"
    return {
        "error": {
            "code": code,
            "message": message or code,
            "details": details or {},
            "request_id": rid,
            "retryable": bool(spec["retryable"]),
        },
        "meta": MetaBlock(request_id=rid, generated_at=utc_now_iso()).model_dump(),
    }


# ---------------------------------------------------------------------------
# Pagination (the ONE v1 model)
# ---------------------------------------------------------------------------


class PageParams:
    """Dependency capturing validated page/page_size query parameters."""

    __slots__ = ("page", "page_size")

    def __init__(
        self,
        page: int = Query(1, ge=1, le=10_000, description="1-based page number."),
        page_size: int = Query(
            DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page."
        ),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    def as_pagination(self, total_items: int) -> dict[str, Any]:
        """Pagination block for the envelope. ``total`` is exact when known, else None."""
        safe_total: int | None
        if total_items < 0 or (math.isinf(total_items) or math.isnan(total_items)):
            safe_total = None
        else:
            safe_total = int(total_items)
        if safe_total is None:
            total_pages = None
        else:
            total_pages = max(1, math.ceil(safe_total / self.page_size)) if safe_total else 1
        return {
            "page": self.page,
            "page_size": self.page_size,
            "total_items": safe_total,
            "total_pages": total_pages,
            "has_next": (total_pages is not None and self.page < total_pages),
            "has_prev": self.page > 1,
        }


def clamp_limit(limit: int, maximum: int = MAX_LIMIT) -> int:
    """Bounds a single-shot limit parameter (store-convention compatible)."""
    return max(1, min(int(limit), maximum))


# ---------------------------------------------------------------------------
# Shared filter primitives (validated; never raw SQL)
# ---------------------------------------------------------------------------


def parse_iso_bound(value: str | None, *, bound: Literal["from", "to"]) -> str | None:
    """Validates an ISO-8601 time-range bound; raises ValueError when invalid.

    Returns the normalized ISO string (UTC-assumed when naive) or None.
    """
    if not value:
        return None
    from datetime import datetime as _dt

    try:
        parsed = _dt.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid ISO-8601 {bound} bound: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


class TimeRange:
    """Validated from/to ISO-8601 range for list endpoints (via time_range_dep)."""

    __slots__ = ("date_from", "date_to")

    def __init__(self, date_from: str | None = None, date_to: str | None = None) -> None:
        self.date_from = date_from
        self.date_to = date_to

    def covers(self, iso_value: str | None) -> bool:
        """True when an ISO timestamp falls inside the range (None passes only open bounds)."""
        if iso_value is None:
            return self.date_from is None and self.date_to is None
        if self.date_from and iso_value < self.date_from:
            return False
        if self.date_to and iso_value > self.date_to:
            return False
        return True


def time_range_dep(
    date_from: str | None = Query(None, description="Inclusive lower bound, ISO-8601 UTC."),
    date_to: str | None = Query(None, description="Inclusive upper bound, ISO-8601 UTC."),
) -> TimeRange:
    """FastAPI dependency validating the from/to ISO-8601 range."""
    frm = parse_iso_bound(date_from, bound="from")
    to = parse_iso_bound(date_to, bound="to")
    if frm and to and frm > to:  # lexicographic == chronological for same-offset ISO
        raise ValueError("date_from must be <= date_to")
    return TimeRange(date_from=frm, date_to=to)
