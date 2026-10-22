"""Response-path performance: one middleware, three wins.

The dashboard is a polling SPA: it re-fetches the same JSON every second and
re-downloads the same static bundle on every load. The server answered every
one of those with a fresh, uncompressed, uncacheable body. This middleware
fixes the three things that actually cost bytes and milliseconds:

1. **Conditional GET (304).** A strong hash of the body becomes an ``ETag``.
   The next poll sends ``If-None-Match`` and gets a 304 with no body. On a
   dashboard polling 10 endpoints/second this removes ~100% of the payload,
   not 10%.
2. **Compression.** JSON and text above a threshold are gzipped once per
   response (level 6), with ``Vary: Accept-Encoding`` so caches stay honest.
   ``text/event-stream`` is never touched — gzip buffers, and buffering an
   SSE stream turns a live feed into a slideshow.
3. **Honest cache directives.** Dynamic API reads may be revalidated but not
   re-downloaded; ``no-store`` is only for responses that must never be reused.

Everything is opt-out via ``NEXUS_WEB_PERF=0`` and fails open: an internal
error in this middleware degrades to "send the body uncompressed", never to a
500 or a dropped response.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import time
from dataclasses import dataclass

from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Bodies smaller than this are not worth a gzip envelope.
MIN_COMPRESS_BYTES = 1024
#: Bodies larger than this are streamed through untouched (no hashing/buffering).
MAX_BUFFER_BYTES = 8 * 1024 * 1024
#: Content types that must never be buffered or compressed.
_STREAMING_TYPES = ("text/event-stream",)
_COMPRESSIBLE_HINTS = ("json", "text/", "javascript", "xml", "svg", "css")
#: Statuses whose bodies are worth caching/compressing.
_CACHEABLE_STATUSES = frozenset({200})


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class PerfConfig:
    """Runtime knobs, resolved once per process."""

    enabled: bool = True
    min_compress_bytes: int = MIN_COMPRESS_BYTES
    max_buffer_bytes: int = MAX_BUFFER_BYTES
    gzip_level: int = 6
    etag_enabled: bool = True
    compress_enabled: bool = True

    @classmethod
    def from_env(cls) -> PerfConfig:
        return cls(
            enabled=_env_flag("NEXUS_WEB_PERF", True),
            min_compress_bytes=int(os.environ.get("NEXUS_WEB_PERF_MIN_BYTES", MIN_COMPRESS_BYTES)),
            max_buffer_bytes=int(os.environ.get("NEXUS_WEB_PERF_MAX_BYTES", MAX_BUFFER_BYTES)),
            gzip_level=int(os.environ.get("NEXUS_WEB_PERF_GZIP_LEVEL", "6")),
            etag_enabled=_env_flag("NEXUS_WEB_PERF_ETAG", True),
            compress_enabled=_env_flag("NEXUS_WEB_PERF_COMPRESS", True),
        )


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1") if isinstance(value, bytes) else str(value)
    return ""


def _header_from(message: Message, name: bytes) -> str:
    """First value of ``name`` in a ``http.response.start`` headers list."""
    for key, value in message.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1") if isinstance(value, bytes) else str(value)
    return ""


def _accepts_gzip(scope: Scope) -> bool:
    header = _header(scope, b"accept-encoding")
    if not header:
        return False
    for part in header.split(","):
        token, _, params = part.strip().partition(";")
        if token.strip().lower() in {"gzip", "*"}:
            quality = params.replace(" ", "").lower()
            if quality in {"q=0", "q=0.0", "q=0.00", "q=0.000"}:
                continue
            return True
    return False


def _is_streaming(content_type: str) -> bool:
    """True for content types that must reach the client frame-by-frame."""
    lowered = content_type.lower()
    return any(lowered.startswith(prefix) for prefix in _STREAMING_TYPES)


def _is_cacheable(method: str, content_type: str) -> bool:
    if method not in {"GET", "HEAD"}:
        return False
    return not _is_streaming(content_type)


def _is_compressible(content_type: str) -> bool:
    lowered = content_type.lower()
    return any(hint in lowered for hint in _COMPRESSIBLE_HINTS)


def _etag_for(body: bytes) -> str:
    return '"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"'


def _etag_matches(if_none_match: str, etag: str) -> bool:
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    target = etag.strip()
    for candidate in if_none_match.split(","):
        value = candidate.strip()
        if value == target:
            return True
        if value.startswith("W/") and value[2:] == target:
            return True
    return False


class PerfMiddleware:
    """Conditional GET + gzip for buffered, cacheable HTTP responses."""

    def __init__(self, app: ASGIApp, config: PerfConfig | None = None) -> None:
        self.app = app
        self.config = config or PerfConfig.from_env()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.config.enabled:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        messages: list[Message] = []
        buffered = 0
        oversize = False
        passthrough = False

        async def _capture(message: Message) -> None:
            nonlocal buffered, oversize, passthrough
            # STREAMING CONTRACT: an event stream has no end — buffering it
            # withholds every frame until the generator finishes, which for
            # SSE is never. The response start carries the content type, so
            # the decision is made there and every later message is forwarded
            # in-order as it arrives (headers already sent, nothing to hash).
            if passthrough:
                await send(message)
                return
            if message["type"] == "http.response.start" and _is_streaming(
                _header_from(message, b"content-type")
            ):
                passthrough = True
                for pending in messages:
                    await send(pending)
                await send(message)
                return
            if message["type"] == "http.response.body" and not oversize:
                buffered += len(message.get("body", b"") or b"")
                if buffered > self.config.max_buffer_bytes:
                    # Too big to hold: stop trying to optimise, replay verbatim.
                    oversize = True
            messages.append(message)

        if scope.get("method", "").upper() not in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, _capture)

        if passthrough:
            return

        if oversize:
            for message in messages:
                await send(message)
            return

        head = next((m for m in messages if m["type"] == "http.response.start"), None)
        body_chunks = [m.get("body", b"") for m in messages if m["type"] == "http.response.body"]
        body = b"".join(body_chunks)
        more_body = any(m.get("more_body") for m in messages if m["type"] == "http.response.body")

        if head is None or more_body:
            for message in messages:
                await send(message)
            return

        headers = list(head.get("headers", []))
        lowered = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in headers}
        content_type = lowered.get("content-type", "")
        status = int(head.get("status", 200))

        if (
            not _is_cacheable(scope.get("method", ""), content_type)
            or status not in _CACHEABLE_STATUSES
        ):
            for message in messages:
                await send(message)
            return

        etag = _etag_for(body) if self.config.etag_enabled else ""
        if etag and _etag_matches(_header(scope, b"if-none-match"), etag):
            not_modified = {
                "type": "http.response.start",
                "status": 304,
                "headers": _replace_headers(headers, etag=etag),
            }
            await send(not_modified)
            await send({"type": "http.response.body", "body": b""})
            return

        send_body = body
        extra: dict[str, str] = {}
        if (
            self.config.compress_enabled
            and len(body) >= self.config.min_compress_bytes
            and _is_compressible(content_type)
            and "content-encoding" not in lowered
            and _accepts_gzip(scope)
        ):
            send_body = gzip.compress(body, compresslevel=self.config.gzip_level, mtime=0)
            extra["content-encoding"] = "gzip"
            extra["vary"] = "Accept-Encoding"

        new_headers = _replace_headers(headers, etag=etag, extra=extra)
        new_headers.append(
            (b"server-timing", f"app;dur={(time.perf_counter() - started) * 1000:.1f}".encode())
        )
        new_headers = _set_content_length(
            new_headers, len(send_body) if scope.get("method") != "HEAD" else len(body)
        )

        await send({"type": "http.response.start", "status": status, "headers": new_headers})
        # HEAD advertises the entity's length but must never carry it.
        await send(
            {
                "type": "http.response.body",
                "body": b"" if scope.get("method") == "HEAD" else send_body,
            }
        )


def _replace_headers(
    headers: list[tuple[bytes, bytes]], *, etag: str = "", extra: dict[str, str] | None = None
) -> list[tuple[bytes, bytes]]:
    """Rewrite ETag/content-length and merge extras, dropping stale duplicates."""
    managed = {b"etag", b"content-length"} | {key.encode() for key in (extra or {})}
    out = [(key, value) for key, value in headers if key.lower() not in managed]
    if etag:
        out.append((b"etag", etag.encode()))
    for key, value in (extra or {}).items():
        out.append((key.encode(), value.encode()))
    return out


def _set_content_length(
    headers: list[tuple[bytes, bytes]], length: int
) -> list[tuple[bytes, bytes]]:
    out = [(key, value) for key, value in headers if key.lower() != b"content-length"]
    out.append((b"content-length", str(length).encode()))
    return out


def install_performance(app) -> PerfMiddleware | None:
    """Attach the middleware to a FastAPI/Starlette app (idempotent)."""
    config = PerfConfig.from_env()
    if not config.enabled:
        return None
    app.add_middleware(PerfMiddleware, config=config)
    return PerfMiddleware  # type: ignore[return-value]
