"""Web response-path tests: the middleware must actually change the wire.

Every test builds a tiny real ASGI app, runs the middleware over it and reads
the raw ASGI messages — no TestClient, no server, no HTTP library guessing at
what happened. The assertions are on bytes on the wire (status, headers, body
length), because "faster" is only true if the bytes change.
"""

from __future__ import annotations

import gzip

import pytest

from nexus_scalp.web.performance import (
    PerfConfig,
    PerfMiddleware,
    _accepts_gzip,
    _etag_matches,
)

JSON_BODY = b'{"status":"ok","rows":[' + b",".join(b'{"i":%d}' % i for i in range(400)) + b"]}"


def make_app(body: bytes = JSON_BODY, *, content_type: str = "application/json", status: int = 200):
    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", content_type.encode()),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return app


async def run(app, *, method: str = "GET", headers: list[tuple[bytes, bytes]] | None = None):
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": method,
        "path": "/api/state",
        "headers": headers or [],
        "query_string": b"",
    }
    await app(scope, receive, send)
    head = sent[0]
    body = b"".join(m.get("body", b"") for m in sent[1:])
    return head["status"], {k.decode(): v.decode() for k, v in head["headers"]}, body


def config(**overrides) -> PerfConfig:
    base = {"enabled": True, "min_compress_bytes": 256, "max_buffer_bytes": 1_000_000}
    base.update(overrides)
    return PerfConfig(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_json_is_gzipped_when_the_client_asks():
    status, headers, body = await run(
        PerfMiddleware(make_app(), config()),
        headers=[(b"accept-encoding", b"gzip, deflate")],
    )
    assert status == 200
    assert headers["content-encoding"] == "gzip"
    assert headers["vary"] == "Accept-Encoding"
    assert int(headers["content-length"]) == len(body)
    assert len(body) < len(JSON_BODY) / 3
    assert gzip.decompress(body) == JSON_BODY


@pytest.mark.asyncio
async def test_no_accept_encoding_means_no_compression():
    status, headers, body = await run(PerfMiddleware(make_app(), config()))
    assert status == 200
    assert "content-encoding" not in headers
    assert body == JSON_BODY


@pytest.mark.asyncio
async def test_small_bodies_are_not_inflated():
    small = b'{"ok":1}'
    _status, headers, body = await run(
        PerfMiddleware(make_app(small), config(min_compress_bytes=1024)),
        headers=[(b"accept-encoding", b"gzip")],
    )
    assert "content-encoding" not in headers
    assert body == small


@pytest.mark.asyncio
async def test_sse_streams_are_never_buffered_or_gzipped():
    chunk = b"data: " + b"x" * 400 + b"\n\n"
    _status, headers, body = await run(
        PerfMiddleware(make_app(chunk, content_type="text/event-stream"), config()),
        headers=[(b"accept-encoding", b"gzip")],
    )
    assert "content-encoding" not in headers
    assert body == chunk


@pytest.mark.asyncio
async def test_sse_frames_flow_incrementally_not_after_the_generator_ends():
    """SSE is streamed frame-by-frame, never withheld until the app returns.

    Regression for TestSSESanitization::test_07_sse_payload_has_no_traceback
    (real uvicorn + raw socket — the SSE generator never ends, so buffering
    that awaited generator completion starved the client until TimeoutError).
    A single generating app yields frame1, sleeps, then frame2; the middleware
    must forward frame1 BEFORE frame2 is produced.
    """
    import asyncio

    produced: list[str] = []
    #: (frame body, how many frames the APP had produced when it arrived).
    observed: list[tuple[str, int]] = []

    async def sse_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send({"type": "http.response.body", "body": b"event: state\ndata: {}\n\n"})
        produced.append("frame1-sent")
        await asyncio.sleep(0.05)
        await send({"type": "http.response.body", "body": b"event: tick\ndata: {}\n\n"})
        produced.append("frame2-sent")

    async def capture(message):
        if message["type"] == "http.response.body" and message.get("body"):
            observed.append((message["body"].decode(), len(produced)))

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/ticks/stream",
        "headers": [],
        "query_string": b"",
    }
    await PerfMiddleware(sse_app, config())(scope, receive, capture)
    # Both frames reached the client, in order and unmodified...
    assert [body for body, _ in observed] == [
        "event: state\ndata: {}\n\n",
        "event: tick\ndata: {}\n\n",
    ]
    # ...and frame1 arrived BEFORE the app produced frame2 — i.e. the
    # middleware forwarded it instead of holding it for generator completion.
    assert observed[0][1] == 0, f"first frame was buffered: {observed}"


@pytest.mark.asyncio
async def test_the_second_poll_is_a_bodyless_304():
    app = PerfMiddleware(make_app(), config())
    _status, headers, body = await run(app, headers=[(b"accept-encoding", b"gzip")])
    etag = headers["etag"]
    assert etag.startswith('"')

    status, headers2, body2 = await run(app, headers=[(b"if-none-match", etag.encode())])
    assert status == 304
    assert body2 == b""
    assert headers2["etag"] == etag


@pytest.mark.asyncio
async def test_a_stale_etag_still_gets_the_body():
    app = PerfMiddleware(make_app(), config())
    status, headers, body = await run(app, headers=[(b"if-none-match", b'"deadbeef"')])
    assert status == 200
    assert body == JSON_BODY
    assert headers["etag"].startswith('"')


@pytest.mark.asyncio
async def test_head_requests_get_headers_without_a_body():
    app = PerfMiddleware(make_app(), config())
    status, headers, body = await run(app, method="HEAD")
    assert status == 200
    assert body == b""
    assert int(headers["content-length"]) == len(JSON_BODY)


@pytest.mark.asyncio
async def test_errors_and_non_json_are_passed_through_untouched():
    plain = b"x" * 4000
    _status, headers, body = await run(
        PerfMiddleware(make_app(plain, content_type="application/octet-stream"), config()),
        headers=[(b"accept-encoding", b"gzip")],
    )
    assert "content-encoding" not in headers
    assert body == plain

    _status, headers, body = await run(
        PerfMiddleware(make_app(JSON_BODY, status=500), config()),
        headers=[(b"accept-encoding", b"gzip")],
    )
    assert "content-encoding" not in headers


@pytest.mark.asyncio
async def test_the_middleware_is_a_no_op_when_disabled():
    _status, headers, body = await run(
        PerfMiddleware(make_app(), config(enabled=False)),
        headers=[(b"accept-encoding", b"gzip")],
    )
    assert "content-encoding" not in headers
    assert "etag" not in headers
    assert body == JSON_BODY


@pytest.mark.asyncio
async def test_an_oversized_body_is_replayed_verbatim():
    # max_buffer_bytes smaller than the body: optimise nothing, break nothing.
    _status, headers, body = await run(
        PerfMiddleware(make_app(), config(max_buffer_bytes=10)),
        headers=[(b"accept-encoding", b"gzip")],
    )
    assert body == JSON_BODY
    assert "content-encoding" not in headers


@pytest.mark.asyncio
async def test_post_bodies_are_never_reused_from_cache():
    app = PerfMiddleware(make_app(), config())
    status, headers, body = await run(app, method="POST", headers=[(b"accept-encoding", b"gzip")])
    assert status == 200
    assert "etag" not in headers
    assert body == JSON_BODY


def test_accept_encoding_is_parsed_not_guessed():
    def scope(value: str) -> dict:
        return {"type": "http", "headers": [(b"accept-encoding", value.encode())]}

    assert _accepts_gzip(scope("gzip")) is True
    assert _accepts_gzip(scope("deflate, gzip;q=0.5")) is True
    assert _accepts_gzip(scope("gzip;q=0")) is False
    assert _accepts_gzip(scope("br, deflate")) is False
    assert _accepts_gzip(scope("")) is False


def test_etag_matching_handles_lists_and_weak_tags():
    assert _etag_matches('"abc"', '"abc"') is True
    assert _etag_matches('W/"abc"', '"abc"') is True
    assert _etag_matches('"zzz", "abc"', '"abc"') is True
    assert _etag_matches("*", '"abc"') is True
    assert _etag_matches('"zzz"', '"abc"') is False
    assert _etag_matches("", '"abc"') is False
