"""API Platform v1 — Observability / Log Center read surface.

The log centre is a *read* plane over the persisted event sink
(:mod:`nexus_scalp.database.log_store`). It exists because the only log
surface that shipped before this router read ``research_events`` — a research
artifact table — so an operator asking "why did the engine fail at 03:14"
had no way to reach the operational events the DB sink was already
persisting.

Design rules enforced here:

- **Bounded everything.** Every query is limited by a server-side cap;
  ``page_size`` and the date range are validated, never trusted.
- **Server-side filtering.** Filtering happens in SQL (indexed columns) so a
  large table is not shipped to the browser to be filtered there.
- **Keyset pagination.** Paging walks the store's ``before_id`` cursor, so
  deep pages cost the same as the first. No ``OFFSET`` scan is ever issued.
- **Honest failures.** A read-plane outage returns a real error envelope, not
  an empty list — "no events" and "cannot reach the log source" are different
  operational facts and the UI must be able to tell them apart.
- **Redaction before egress.** Context is passed through the same
  secret-masking helper the rest of v1 uses, so a token that reached a log
  record by accident cannot reach a browser, an export or an MCP client.
- **No unrestricted SQL.** The filter vocabulary is a closed set; nothing
  here accepts a caller-supplied SQL fragment.

Route ordering matters: the static ``/logs/summary`` and ``/logs/export``
paths are declared *before* ``/logs/{event_id}`` so FastAPI cannot interpret
``summary`` or ``export`` as an event id.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.web.api_v1.common import (
    build_page,
    fail,
    fail_internal_logged,
    get_audit_repo,
    ok,
    sanitize_record_secrets,
    utc_now_iso,
)

router = APIRouter(prefix="/api/v1", tags=["observability"])
logger = get_logger("nexus_scalp.web.api_v1.observability")

#: Hard server-side ceiling on rows returned by any single log request.
LOG_PAGE_MAX = 200
#: Hard ceiling on the window an operator may scan in one request. A wider
#: window is rejected so the caller narrows it deliberately instead of
#: triggering an unbounded table scan.
LOG_MAX_RANGE_DAYS = 90
#: Hard ceiling on export rows; larger extractions belong in an archival job,
#: not in a synchronous HTTP request.
LOG_EXPORT_MAX = 5_000
#: Severity vocabulary, ordered. Anything else is a validation error.
_SEVERITIES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
#: How many cursor hops a ``page=N`` request may walk. Page-number paging is
#: a compatibility affordance; ``before_id`` is the real primitive.
_PAGE_HOP_MAX = 200


def _log_store(request: Request) -> Any:
    """Build the log store bound to the authoritative audit config.

    The store reads through the audit repository's config so the API always
    queries the SAME backend the engine writes to — never a second, hidden
    database of a different provider.
    """
    from nexus_scalp.database.log_store import DatabaseLogStore

    repo = get_audit_repo(request)
    cfg = getattr(repo, "config", None) or getattr(repo, "_config", None)
    return DatabaseLogStore(config=cfg)


def _parse_window(since: str | None, until: str | None) -> tuple[str, str] | JSONResponse:
    """Validate and normalize the requested time window.

    Defaults to the last 24 hours. Returns an error envelope (not a silently
    widened window) when a caller passes an unparseable timestamp or an
    inverted or over-wide range.
    """
    now = datetime.now(UTC)
    try:
        end = datetime.fromisoformat(until.replace("Z", "+00:00")) if until else now
    except ValueError:
        return fail(None, "VALIDATION_ERROR", message="'until' is not an ISO-8601 timestamp")
    try:
        start = (
            datetime.fromisoformat(since.replace("Z", "+00:00"))
            if since
            else end - timedelta(hours=24)
        )
    except ValueError:
        return fail(None, "VALIDATION_ERROR", message="'since' is not an ISO-8601 timestamp")
    if start >= end:
        return fail(None, "VALIDATION_ERROR", message="'since' must be earlier than 'until'")
    if (end - start) > timedelta(days=LOG_MAX_RANGE_DAYS):
        return fail(
            None,
            "VALIDATION_ERROR",
            message=f"date range exceeds {LOG_MAX_RANGE_DAYS} days; narrow the window",
        )
    return start.isoformat(), end.isoformat()


def _split(value: str | None) -> tuple[str, ...] | None:
    """Turn a comma-separated filter into a bounded tuple of tokens."""
    if not value:
        return None
    parts = tuple(p.strip() for p in value.split(",") if p.strip())
    return parts[:20] or None


def _normalize_levels(value: str | None) -> tuple[str, ...] | None:
    """Validate the severity filter against the closed vocabulary."""
    parts = _split(value)
    if not parts:
        return None
    unknown = [p for p in parts if p.upper() not in _SEVERITIES]
    if unknown:
        raise ValueError(f"unknown severity: {', '.join(unknown)}")
    return tuple(p.upper() for p in parts)


def _redact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mask secret-shaped context values before any record leaves the server."""
    out: list[dict[str, Any]] = []
    for row in rows:
        clean = dict(row)
        ctx = clean.get("context_json")
        if isinstance(ctx, str) and ctx.strip():
            try:
                clean["context_json"] = sanitize_record_secrets(json.loads(ctx))
            except (ValueError, TypeError):
                # Unparseable context is reported as such; it is never
                # forwarded raw (it may be a truncated blob mid-secret).
                clean["context_json"] = {"_unparseable": True}
        elif isinstance(ctx, (dict, list)):
            clean["context_json"] = sanitize_record_secrets(ctx)
        out.append(sanitize_record_secrets(clean))
    return out


def _read_failure(request: Request, resource: str, exc: BaseException) -> JSONResponse:
    """Report a log-source failure as such, never as an empty result page."""
    return fail_internal_logged(
        request,
        logger,
        f"/api/v1 {resource}",
        exc,
        code="DEPENDENCY_UNAVAILABLE",
        message="log source unavailable",
    )


# ---------------------------------------------------------------------------
# Static routes first — see the module docstring on route ordering.
# ---------------------------------------------------------------------------


@router.get("/observability/logs", summary="Search persisted operational log events")
def logs_search(
    request: Request,
    severity: Annotated[str | None, Query(max_length=120)] = None,
    component: Annotated[str | None, Query(max_length=200)] = None,
    event: Annotated[str | None, Query(max_length=200)] = None,
    correlation_id: Annotated[str | None, Query(max_length=128)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    since: Annotated[str | None, Query(max_length=40)] = None,
    until: Annotated[str | None, Query(max_length=40)] = None,
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=LOG_PAGE_MAX),
    before_id: int | None = Query(None, ge=1),
) -> Any:
    """Paginated, filtered log search over the persisted event sink.

    Filtering is server-side and bounded; ``q`` is a prefix match on the
    human-readable message. Row count is capped by :data:`LOG_PAGE_MAX`
    regardless of what the caller asks for.

    Prefer ``before_id`` (keyset) for deep paging; ``page`` is honoured by
    walking the cursor internally and is capped so a hostile ``page`` value
    cannot force a long scan.
    """
    ps = min(page_size, LOG_PAGE_MAX)
    window = _parse_window(since, until)
    if isinstance(window, JSONResponse):
        return window
    start, end = window

    try:
        levels = _normalize_levels(severity)
    except ValueError as exc:
        return fail(request, "VALIDATION_ERROR", message=str(exc))

    filters: dict[str, Any] = {
        "levels": levels,
        "components": _split(component),
        "event_names": _split(event),
        "correlation_id": correlation_id,
        "since": start,
        "until": end,
        "text": q,
    }

    try:
        store = _log_store(request)
        if before_id is not None:
            rows = store.query_events(**filters, limit=ps + 1, before_id=before_id)
            items, has_more = rows[:ps], len(rows) > ps
        else:
            # Page N is reached by N-1 cursor hops, never an OFFSET scan.
            cursor: int | None = None
            items = []
            has_more = False
            for _ in range(min(page, _PAGE_HOP_MAX)):
                batch = store.query_events(**filters, limit=ps + 1, before_id=cursor)
                items, has_more = batch[:ps], len(batch) > ps
                cursor = items[-1]["id"] if items else None
                if not has_more:
                    break
    except Exception as exc:
        return _read_failure(request, "observability/logs", exc)

    payload = build_page(_redact_rows(items), page, ps, has_more=has_more)
    payload["next_before_id"] = items[-1]["id"] if items else None
    payload["window"] = {"since": start, "until": end}
    return ok(request, payload)


@router.get("/observability/logs/summary", summary="Severity counts for the log centre header")
def logs_summary(
    request: Request,
    since: Annotated[str | None, Query(max_length=40)] = None,
    until: Annotated[str | None, Query(max_length=40)] = None,
) -> Any:
    """Per-severity counts over a bounded window, for the dashboard header.

    A count that cannot be read reports ``None`` (not zero) so the UI can say
    "count unavailable" instead of falsely claiming a quiet period.
    """
    window = _parse_window(since, until)
    if isinstance(window, JSONResponse):
        return window
    start, end = window
    counts: dict[str, Any] = {}
    try:
        store = _log_store(request)
        for level in _SEVERITIES:
            n = store.count_events(levels=(level,), since=start, until=end)
            counts[level] = None if n < 0 else n
    except Exception as exc:
        return _read_failure(request, "observability/logs/summary", exc)
    total = None if any(v is None for v in counts.values()) else sum(counts.values())
    return ok(
        request,
        {
            "counts": counts,
            "total": total,
            "window": {"since": start, "until": end},
            "generated_at": utc_now_iso(),
        },
    )


@router.get("/observability/logs/export", summary="Bounded structured export of log events")
def logs_export(
    request: Request,
    format: Literal["json", "csv"] = "json",
    severity: Annotated[str | None, Query(max_length=120)] = None,
    component: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    since: Annotated[str | None, Query(max_length=40)] = None,
    until: Annotated[str | None, Query(max_length=40)] = None,
    limit: int = Query(1_000, ge=1, le=LOG_EXPORT_MAX),
) -> Any:
    """Export a bounded slice of the log, redacted, as JSON or CSV.

    Exports are capped at :data:`LOG_EXPORT_MAX` rows. Redaction happens
    server-side *before* serialization so an export can never contain more
    than the on-screen view does.
    """
    window = _parse_window(since, until)
    if isinstance(window, JSONResponse):
        return window
    start, end = window
    try:
        levels = _normalize_levels(severity)
    except ValueError as exc:
        return fail(request, "VALIDATION_ERROR", message=str(exc))
    try:
        store = _log_store(request)
        rows = _redact_rows(
            store.query_events(
                levels=levels,
                components=_split(component),
                since=start,
                until=end,
                text=q,
                limit=min(limit, LOG_EXPORT_MAX),
            )
        )
    except Exception as exc:
        return _read_failure(request, "observability/logs/export", exc)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if format == "csv":
        buf = io.StringIO()
        # Column names match the persisted schema so an export round-trips
        # into an analysis tool without a renaming step.
        cols = [
            "id",
            "timestamp",
            "level",
            "logger_name",
            "operation",
            "event_name",
            "error_message",
            "correlation_id",
            "error_code",
            "repeat_count",
        ]
        writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in cols})
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="nse-logs-{stamp}.csv"',
                "X-Content-Type-Options": "nosniff",
            },
        )
    return Response(
        content=json.dumps(
            {
                "generated_at": utc_now_iso(),
                "window": {"since": start, "until": end},
                "events": rows,
            },
            default=str,
        ),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="nse-logs-{stamp}.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------------------------------------------------------------------------
# Parameterised routes last.
# ---------------------------------------------------------------------------


@router.get(
    "/observability/logs/{event_id}",
    summary="Full detail for one log event (redacted context and trace)",
)
def logs_detail(request: Request, event_id: int) -> Any:
    """One event by id, including the sanitized exception trace.

    The trace is included here and not in search results because it is the
    single most useful triage field but would bloat a 200-row page.
    """
    try:
        store = _log_store(request)
        row = store.get_event(event_id)
    except Exception as exc:
        return _read_failure(request, "observability/logs/detail", exc)
    if not row:
        return fail(request, "RESOURCE_NOT_FOUND", message="no log event with that id")
    return ok(request, _redact_rows([row])[0])


@router.get(
    "/observability/logs/{event_id}/correlate",
    summary="All events sharing one correlation or request id",
)
def logs_correlate(
    request: Request,
    event_id: int,
    limit: int = Query(100, ge=1, le=LOG_PAGE_MAX),
) -> Any:
    """Drill-down: every event that shares this event's correlation id.

    This is the "what else happened around this failure" query. It resolves
    the seed event first so the caller only needs the id it already has.
    """
    try:
        store = _log_store(request)
        seed = store.get_event(event_id)
        if not seed:
            return fail(request, "RESOURCE_NOT_FOUND", message="no log event with that id")
        corr = str(seed.get("correlation_id") or "")
        if not corr:
            return ok(
                request,
                {
                    "correlation_id": "",
                    "events": [],
                    "note": "this event carries no correlation id",
                },
            )
        rows = store.query_events(correlation_id=corr, limit=limit)
    except Exception as exc:
        return _read_failure(request, "observability/logs/correlate", exc)
    return ok(request, {"correlation_id": corr, "events": _redact_rows(rows)})
