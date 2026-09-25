"""Decision Trace — observer REST + SSE streaming layer (§75/§76).

Purely additive to the existing web surface: registered once in
``web/server.py::create_app`` right after the operator routes, touching no
existing route. Reuses the project's conventions:

- ``_err`` / ``_log_err`` closures for structured errors (web/errors.py)
- ``StreamingResponse(media_type="text/event-stream")`` — the same SSE
  transport already used by ``/api/ticks/stream`` (no second API
  architecture, no new dependency)
- ``canonical_json`` for deterministic payload serialization

Hot-path contract (§7): no handler here touches the trading engine. The
observer is a passive in-memory store; these routes READ it. A request can
never stall a tick, and an exception here can never reach the engine — every
handler is guarded and returns an honest error payload instead.

Stream protocol: named SSE events ``hello`` (session+schema+resume point),
``batch`` (events since last_seq), ``heartbeat``, and ``error``. The client
sends the last seen sequence via the ``?last_seq=`` query param on (re)connect
so a resume is explicit — when the ring has evicted the requested point the
batch carries ``gap: true`` and the UI must show TRACE GAP rather than pretend
continuity (§77).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.observability.trace_contract import TRACE_SCHEMA_VERSION, sanitize_detail
from nexus_scalp.observability.trace_observer import trace_observer
from nexus_scalp.web.errors import log_web_error, new_request_id

logger = get_logger("nexus_scalp.web.trace_routes")

#: Bounded batch size per SSE tick — a slow client receives progress, never a
#: multi-megabyte frame (§43 backpressure + §55 payload size safety).
_MAX_BATCH = 500
#: SSE tick cadence (seconds). Bounded polling of the in-memory ring; the
#: engine never waits on it.
_TICK_S = 0.15
_HEARTBEAT_EVERY = 20  # ticks between keepalives


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _err_payload(code: str, **kw: Any) -> dict[str, Any]:
    from nexus_scalp.web.errors import safe_error_payload

    return safe_error_payload(code=code, request_id=new_request_id(), **kw)


def _as_text(value: Any) -> str | None:
    """Normalize a query param to a non-empty trimmed word, else None.

    Empty string / whitespace means "filter not set" (so ``?stage=`` keeps the
    legacy unfiltered behavior), never a filter for an unnamed stage.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _filter_echo(*filters: Any) -> dict[str, Any]:
    """Echo the ACTIVE filters so the UI can show what is narrowing the page."""
    names = ("trace_id", "stage", "status", "mode", "position_id")
    return {n: v for n, v in zip(names, filters, strict=False) if v}


def _apply_event_filters(
    events: list[dict[str, Any]],
    *,
    trace_id: str | None = None,
    stage: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    position_id: str | None = None,
) -> list[dict[str, Any]]:
    """AND-filter a page of STORED events over verbatim runtime words/ids.

    ``mode`` matches the mode word the emit site recorded (contract field or
    the ``detail.engine_mode`` legacy value, upper-cased for a stable
    comparison). A filter that matches nothing yields an empty list — the UI
    renders the honest empty state (§73), never a fabricated page.
    """
    tid = _as_text(trace_id)
    stg = _as_text(stage)
    sta = _as_text(status)
    md = _as_text(mode)
    pid = _as_text(position_id)
    if not any((tid, stg, sta, md, pid)):
        return list(events)
    out = []
    for e in events:
        if tid is not None and e.get("trace_id") != tid:
            continue
        if stg is not None and e.get("stage") != stg:
            continue
        if sta is not None and e.get("status") != sta:
            continue
        if (
            pid is not None
            and str(e.get("position_id") or e.get("detail", {}).get("position_id") or "") != pid
        ):
            continue
        if md is not None:
            ev_mode = e.get("mode") or e.get("detail", {}).get("engine_mode")
            ev_mode = str(getattr(ev_mode, "value", ev_mode)).upper() if ev_mode else ""
            if ev_mode != md.upper():
                continue
        out.append(e)
    return out


def _stored_event(event_id: str) -> dict[str, Any] | None:
    """Locate one STORED event by id (linear scan of the bounded ring).

    Returns None when the id was never emitted or the ring evicted it —
    callers answer 404 / NOT OBSERVED, never a synthesized record.
    """
    with trace_observer._lock:
        return next((e for e in trace_observer._events if e["event_id"] == event_id), None)


def _next_event(after: dict[str, Any]) -> dict[str, Any] | None:
    """The next STORED event of the SAME trace after ``after`` (insertion order).

    None when this was the last retained event of its trace.
    """
    trace_id = after.get("trace_id")
    if not trace_id:
        return None
    with trace_observer._lock:
        refs = trace_observer._trace_events.get(trace_id)
        if not refs:
            return None
        seq = after.get("sequence")
        for e in refs:
            if e.get("sequence", 0) > (seq or 0):
                return e
    return None


def _why_reasons(ev: dict[str, Any]) -> dict[str, Any]:
    """The event's OWN reason-bearing evidence (§16): verbatim runtime words.

    Only fields the runtime actually recorded are surfaced (reason_code, the
    rejection/block codes, the frozen ``state`` word). An empty dict means NO
    REASON OBSERVED — the WHY panel renders UNKNOWN, never an invented
    explanation (§60).
    """
    detail = ev.get("detail") or {}
    out: dict[str, Any] = {}

    def _put(key: str, *candidates: Any) -> None:
        if key in out:
            return
        for candidate in candidates:
            if candidate is None:
                continue
            text = str(candidate).strip()
            if text:
                out[key] = text
                return

    _put(
        "reason_code",
        ev.get("reason_code"),
        detail.get("reason_code"),
        detail.get("reason"),
        detail.get("suppressed_by"),
    )
    _put("error_code", ev.get("error_code"), detail.get("error_code"))
    _put("state", ev.get("state"), detail.get("state"))
    _put("rejection_reason", detail.get("rejection_reason"))
    _put("blocked_by", detail.get("blocked_by"))
    _put("decision_stage", detail.get("decision_stage"))
    return out


def _why_payload(ev: dict[str, Any]) -> dict[str, Any]:
    """Runtime-derived WHY / NEXT answer for one STORED event (§37/§38).

    WHY  = the event's own reason/state/detail evidence, verbatim.
    NEXT = the next observed event of the same trace; TERMINATED (with this
           event's terminal status) when the path observably ended here; or
           NOT OBSERVED when nothing further was retained — a missing link is
           reported as missing evidence, never as a fabricated continuation.
    provenance is ``observed`` for the stored record itself and for an edge the
    observer explicitly linked (``parent_event_id``); a same-trace successor
    without that link is marked ``inferred`` (§9/§56), and TERMINATED/NOT
    OBSERVED carry no edge, so ``provenance`` stays absent (PROVENANCE GAP).
    """
    detail = ev.get("detail") or {}
    reasons = _why_reasons(ev)
    next_ev = _next_event(ev)
    if next_ev is not None:
        linked = next_ev.get("parent_event_id") == ev.get("event_id")
        nxt: dict[str, Any] = {
            "observed": True,
            "destination": next_ev.get("stage"),
            "event_id": next_ev.get("event_id"),
            "stage": next_ev.get("stage"),
            "event_type": next_ev.get("event_type"),
            "status": next_ev.get("status"),
            "timestamp": next_ev.get("timestamp"),
            "terminal": bool(next_ev.get("terminal")),
            "provenance": "observed" if linked else "inferred",
            "link": "parent_event_id" if linked else "trace_sequence",
        }
        next_reasons = _why_reasons(next_ev)
        if next_reasons:
            nxt["why"] = next_reasons
    elif ev.get("terminal"):
        nxt = {
            "observed": True,
            "destination": "TERMINATED",
            "terminal_status": ev.get("status"),
        }
        if reasons:
            nxt["why"] = reasons
    else:
        nxt = {"observed": False, "destination": "NOT OBSERVED"}
    payload: dict[str, Any] = {
        "found": True,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "event_id": ev.get("event_id"),
        "trace_id": ev.get("trace_id"),
        "sequence": ev.get("sequence"),
        "timestamp": ev.get("timestamp"),
        "stage": ev.get("stage"),
        "component": ev.get("component"),
        "event_type": ev.get("event_type"),
        "status": ev.get("status"),
        "terminal": bool(ev.get("terminal")),
        "why": reasons,
        "detail": sanitize_detail(detail),
        "next": nxt,
        "provenance": "observed",
    }
    # Optional contract fields (present only when the emit site recorded them).
    for key in (
        "symbol",
        "decision_id",
        "latency_us",
        "state",
        "reason_code",
        "error_code",
        "mode",
        "position_id",
        "order_id",
        "deal_id",
        "model",
        "provider",
        "freshness",
        "parent_event_id",
    ):
        if ev.get(key) is not None:
            payload[key] = ev[key]
    return payload


def register_trace_routes(app: Any, _err: Any, _log_err: Any) -> None:
    """Attach the Decision Trace observer surface (additive; read-only)."""

    # ---------------------------------------------------------------- status
    @app.get("/api/trace/observer")
    def get_trace_observer() -> dict[str, Any]:
        """Observer lifecycle + counters (§09): OFF/STARTING/ACTIVE/STOPPING/ERROR."""
        try:
            snap = trace_observer.snapshot()
            snap["trace_schema_version"] = TRACE_SCHEMA_VERSION
            return snap
        except Exception as exc:  # observability failure isolation (§47)
            _log_err(exc, "observer status failed", endpoint="/api/trace/observer")
            return _err_payload("TRACE_OBSERVER_ERROR")

    @app.post("/api/trace/observer/start")
    def post_trace_observer_start() -> dict[str, Any]:
        """Activate detailed tracing (UI opened). Never alters trading."""
        try:
            return trace_observer.start_session()
        except Exception as exc:  # pragma: no cover - isolation guard
            _log_err(exc, "observer start failed", endpoint="/api/trace/observer/start")
            return _err_payload("TRACE_OBSERVER_ERROR")

    @app.post("/api/trace/observer/stop")
    def post_trace_observer_stop() -> dict[str, Any]:
        """Return to the low-overhead inactive path (UI closed)."""
        try:
            return trace_observer.stop_session()
        except Exception as exc:  # pragma: no cover - isolation guard
            _log_err(exc, "observer stop failed", endpoint="/api/trace/observer/stop")
            return _err_payload("TRACE_OBSERVER_ERROR")

    # ------------------------------------------------------------- read views
    @app.get("/api/trace/topology")
    def get_trace_topology() -> dict[str, Any]:
        """Discovered runtime topology (§13) — derived from observed events only."""
        try:
            return trace_observer.topology()
        except Exception as exc:  # pragma: no cover - isolation guard
            _log_err(exc, "topology failed", endpoint="/api/trace/topology")
            return _err_payload("TRACE_TOPOLOGY_ERROR")

    @app.get("/api/trace/latency")
    def get_trace_latency() -> dict[str, Any]:
        """Per-stage latency p50/p95/p99 (§35) — INSUFFICIENT DATA until enough samples."""
        try:
            return trace_observer.latency_stats()
        except Exception as exc:  # pragma: no cover - isolation guard
            _log_err(exc, "latency failed", endpoint="/api/trace/latency")
            return _err_payload("TRACE_LATENCY_ERROR")

    @app.get("/api/trace/integrity")
    def get_trace_integrity() -> dict[str, Any]:
        """Trace-integrity warnings (§45): duplicates, sequence holes, orphan links."""
        try:
            return trace_observer.integrity_report()
        except Exception as exc:  # pragma: no cover - isolation guard
            _log_err(exc, "integrity failed", endpoint="/api/trace/integrity")
            return _err_payload("TRACE_INTEGRITY_ERROR")

    @app.get("/api/trace/decisions")
    def get_trace_decisions(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Bounded recent-decision feed (§31), newest first."""
        try:
            limit = max(1, min(int(limit), 200))
            offset = max(0, int(offset))
            return trace_observer.decisions_list(limit=limit, offset=offset)
        except Exception as exc:  # pragma: no cover - isolation guard
            _log_err(exc, "decisions failed", endpoint="/api/trace/decisions")
            return _err_payload("TRACE_DECISIONS_ERROR")

    @app.get("/api/trace/events")
    def get_trace_events(
        last_seq: int = 0,
        limit: int = 1000,
        trace_id: str | None = None,
        stage: str | None = None,
        status: str | None = None,
        mode: str | None = None,
        position_id: str | None = None,
    ) -> dict[str, Any]:
        """Batched event tail with explicit resume point + gap flag (§77).

        Optional AND-filters (§57 clickable counters / §36 filtering): every
        one is a verbatim runtime word (``stage``, ``status``, ``mode``) or id
        (``trace_id``, ``position_id``). With no filter the response is
        byte-identical to the legacy tail — filters only narrow.
        """
        try:
            last_seq = max(0, int(last_seq))
            limit = max(1, min(int(limit), 5000))
            with trace_observer._lock:
                # NOTE: the lock is the observer's own RLock (re-entrant: the
                # read views below take it too, but this path builds the page
                # from the ring under one acquisition so a concurrent emit
                # cannot split the filtered page).
                base = [e for e in trace_observer._events if e["sequence"] > last_seq][-limit:]
                events = _apply_event_filters(
                    base,
                    trace_id=trace_id,
                    stage=stage,
                    status=status,
                    mode=mode,
                    position_id=position_id,
                )
                gap = False
                if trace_observer._events and last_seq > 0:
                    oldest = trace_observer._events[0]["sequence"]
                    if last_seq < oldest - 1:
                        gap = True  # ring evicted events the client asked for
            return {
                "events": events,
                "gap": gap,
                "last_seq": trace_observer._seq,
                "filters": _filter_echo(trace_id, stage, status, mode, position_id),
            }
        except Exception as exc:  # pragma: no cover - isolation guard
            _log_err(exc, "events failed", endpoint="/api/trace/events")
            return _err_payload("TRACE_EVENTS_ERROR")

    @app.get("/api/trace/bundle/{key}")
    def get_trace_bundle(key: str) -> dict[str, Any]:
        """Full forensic bundle for one trace_id or decision_id (§32/§80).

        ``found: false`` is the honest answer when nothing was retained —
        the caller renders PROVENANCE GAP, never a fabricated bundle.
        """
        try:
            return trace_observer.trace_bundle(key)
        except Exception as exc:  # pragma: no cover - isolation guard
            _log_err(exc, "bundle failed", endpoint="/api/trace/bundle")
            return _err_payload("TRACE_BUNDLE_ERROR")

    # --------------------------------------------------------- WHY / NEXT (§37/§38)
    @app.get("/api/trace/why/{event_id}")
    def get_trace_why(event_id: str) -> Response:
        """WHY did this event happen, and WHERE did it go next (§37/§38/§65).

        Built ONLY from the stored record: the event's own reason/state/detail
        evidence, the next observed event of the same trace (or TERMINATED with
        the terminal status, or NOT OBSERVED when nothing further was retained)
        and the provenance mark of that linkage. Stored events only — an
        unknown id is an honest 404; nothing is synthesized.
        """
        try:
            ev = _stored_event(event_id)
            if ev is None:
                return JSONResponse(
                    _err_payload(
                        "RESOURCE_NOT_FOUND",
                        message="No stored event with this id (never emitted or already evicted).",
                        event_id=str(event_id),
                    ),
                    status_code=404,
                )
            payload = _why_payload(ev)
            payload["generated_at"] = _now_iso()
            return JSONResponse(payload, status_code=200)
        except Exception as exc:  # pragma: no cover - isolation guard
            _log_err(exc, "why failed", endpoint="/api/trace/why", resource=str(event_id))
            return JSONResponse(_err_payload("TRACE_WHY_ERROR"), status_code=500)

    # ------------------------------------------------------------- live SSE
    @app.get("/api/trace/stream")
    async def sse_trace_stream(request: Request) -> Response:
        """Live decision-trace stream (§76).

        Named events: ``hello`` (session identity + schema version + resume
        point), ``batch`` (ordered events), ``heartbeat``, ``error``. The
        observer session is tied to this connection's lifetime: on
        disconnect the subscriber is released and the observer auto-stops
        after its no-subscriber grace period — closing the UI returns the
        engine to the low-overhead path without any trading-behavior change.
        """
        try:
            last_seq = max(0, int(request.query_params.get("last_seq", "0")))
        except (TypeError, ValueError):
            last_seq = 0

        from nexus_scalp.web.server import canonical_json

        async def event_generator():
            sub = None
            # Own accumulator: seeded from the outer resume point, then
            # owned locally (a read+write of `last_seq` here would shadow it
            # into an unbound local before the first assignment).
            seq = last_seq
            try:
                # Activate the observer for this consumer (idempotent).
                session = trace_observer.start_session()
                sub = trace_observer.subscribe()
                hello = {
                    "event": "hello",
                    "observer_id": session.get("observer_id"),
                    "session_id": session.get("session_id"),
                    "started_at": session.get("started_at"),
                    "trace_schema_version": TRACE_SCHEMA_VERSION,
                    "last_seq": seq,
                    "resumed": seq > 0,
                    "sent_at": _now_iso(),
                }
                yield f"event: hello\ndata: {canonical_json(hello)}\n\n"

                ticks = 0
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        # Backfill any retained events the client has not seen,
                        # then switch to the subscriber's own bounded buffer.
                        batch: list[dict[str, Any]] = []
                        resumed = trace_observer.events_since(seq, limit=_MAX_BATCH)
                        if resumed["events"]:
                            batch.extend(resumed["events"])
                            seq = batch[-1]["sequence"]
                            if resumed.get("gap"):
                                # The client asked for a point the ring has
                                # evicted: say so instead of faking continuity.
                                yield (
                                    "event: gap\ndata: "
                                    + canonical_json(
                                        {
                                            "event": "gap",
                                            "last_seq": seq,
                                            "message": "TRACE GAP: evicted before requested sequence",
                                        }
                                    )
                                    + "\n\n"
                                )
                        if sub is not None:
                            drained = sub.drain()
                            if drained:
                                batch.extend(drained)
                                seq = max(seq, drained[-1]["sequence"])
                        if batch:
                            ticks = (ticks + 1) % _HEARTBEAT_EVERY
                            payload = {
                                "event": "batch",
                                "count": len(batch),
                                "last_seq": seq,
                                "dropped_visual_events": (
                                    sub.dropped_visual_events if sub is not None else 0
                                ),
                                "coalesced_events": (
                                    sub.coalesced_events if sub is not None else 0
                                ),
                                "events": batch,
                                "sent_at": _now_iso(),
                            }
                            yield f"event: batch\ndata: {canonical_json(payload)}\n\n"
                        else:
                            ticks += 1
                            if ticks % _HEARTBEAT_EVERY == 0:
                                hb = {
                                    "event": "heartbeat",
                                    "last_seq": seq,
                                    "observer_status": trace_observer.status,
                                    "sent_at": _now_iso(),
                                }
                                yield f"event: heartbeat\ndata: {canonical_json(hb)}\n\n"
                    except Exception as exc:
                        log_web_error(
                            logger,
                            "/api/trace/stream",
                            new_request_id(),
                            exc,
                            context={"msg": "trace SSE tick warning"},
                        )
                        err = {
                            "event": "error",
                            "code": "TRACE_STREAM_TICK_ERROR",
                            "message": "trace stream tick failed; connection retained",
                            "sent_at": _now_iso(),
                        }
                        yield f"event: error\ndata: {canonical_json(err)}\n\n"
                    await asyncio.sleep(_TICK_S)
            finally:
                if sub is not None:
                    trace_observer.unsubscribe(sub.subscriber_id)

        try:
            return StreamingResponse(event_generator(), media_type="text/event-stream")
        except Exception as exc:  # pragma: no cover - isolation guard
            _log_err(exc, "trace stream failed", endpoint="/api/trace/stream")
            return JSONResponse(_err_payload("TRACE_STREAM_ERROR"), status_code=500)
