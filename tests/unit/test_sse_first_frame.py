"""SSE /api/ticks/stream must emit its first frame immediately.

Two hangs lived in the SSE generator behind _TokenAuthMiddleware
(BaseHTTPMiddleware): the per-iteration request.is_disconnected() probe
never resolves on that wrapper (disconnect messages are not proxied into
the generator's receive channel), and the first full-snapshot build
delayed the first chunk for seconds — clients saw no response headers at
all, EventSource timed out, and every dashboard showed "Connection lost"
while REST polling stayed healthy. The generator now yields an immediate
heartbeat frame first and relies on generator cancellation for
disconnects.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from starlette.requests import Request

from nexus_scalp.web.auth import _resolve_token
from nexus_scalp.web.server import create_app


@pytest.mark.asyncio
async def test_sse_endpoint_yields_heartbeat_first() -> None:
    """Direct generator contract: first chunk is the lightweight heartbeat."""
    app = create_app(engine_ref=None)
    routes = [r for r in app.routes if getattr(r, "path", "") == "/api/ticks/stream"]
    assert len(routes) == 1, "expected /api/ticks/stream route"

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/ticks/stream",
        "headers": [],
    }
    req = Request(scope)
    resp = await routes[0].endpoint(req)
    gen = resp.body_iterator

    started = time.monotonic()
    first_chunk = await anext(gen)
    elapsed = time.monotonic() - started

    assert first_chunk == "event: heartbeat\ndata: {}\n\n"
    assert elapsed < 5.0, f"SSE first chunk took {elapsed:.2f}s — expected immediate heartbeat"

    # Second chunk is a full state snapshot
    second_chunk = await anext(gen)
    assert second_chunk.startswith("event: state\ndata: ")


@pytest.mark.asyncio
async def test_sse_asgi_lifecycle_flushes_headers_immediately() -> None:
    """Full ASGI pipeline (including auth middleware) flushes http.response.start immediately."""
    app = create_app(engine_ref=None)
    token, _ = _resolve_token()
    sent_messages: list[dict] = []

    async def send(message: dict) -> None:
        sent_messages.append(message)

    # Disconnect after 0.5s so the infinite streaming loop terminates cleanly
    async def receive() -> dict:
        await asyncio.sleep(0.5)
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/ticks/stream",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "query_string": b"",
        "server": ("127.0.0.1", 8080),
        "client": ("127.0.0.1", 12345),
    }

    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=5.0)
    except TimeoutError:
        pass

    types = [m.get("type") for m in sent_messages]
    assert "http.response.start" in types, "response headers must start immediately"
    assert "http.response.body" in types, "at least one body chunk must be sent"

    # Verify response headers
    start_msg = next(m for m in sent_messages if m.get("type") == "http.response.start")
    assert start_msg.get("status") == 200
    headers = dict(start_msg.get("headers", []))
    assert b"text/event-stream" in headers.get(b"content-type", b"")
