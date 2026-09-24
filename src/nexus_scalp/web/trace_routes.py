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
from nexus_scalp.observability.trace_contract import TRACE_SCHEMA_VERSION
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
    def get_trace_events(last_seq: int = 0, limit: int = 1000) -> dict[str, Any]:
        """Batched event tail with explicit resume point + gap flag (§77)."""
        try:
            last_seq = max(0, int(last_seq))
            limit = max(1, min(int(limit), 5000))
            return trace_observer.events_since(last_seq, limit=limit)
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
