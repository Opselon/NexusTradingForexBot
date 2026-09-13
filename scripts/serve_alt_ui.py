#!/usr/bin/env python3
"""STANDALONE-ALT-UI (lane BACKEND-1): a dependency-free alt-console host.

Why this exists
---------------
The alternative React console (``frontend/``) is normally mounted by the
FastAPI engine process at ``/alt`` (see ``src/nexus_scalp/web/server.py``,
ALT-UI mount) and proxied in development by Vite (``frontend/vite.config.ts``).
Both couple the console to the engine process/port. This script decouples
them: it runs on its OWN port (default 8088), serves the built
``frontend/dist`` as a pure static SPA, and reverse-proxies every API route
(``/api`` ``/health`` ``/healthz``) to the authoritative engine backend
(default ``http://127.0.0.1:8080``). The backend stays the single source of
truth — this host NEVER answers an API request from cache or disk (fail-closed:
if the backend is down the caller gets a 502 envelope, never stale HTML).

Stdlib only (``http.server`` + ``threading`` + ``http.client``): no Node
runtime in production (DEC-0002) and no new Python dependencies — it runs with
the repo venv python as-is.

Auth model (READ BEFORE USING ``--allow-remote``)
-------------------------------------------------
This host enforces NOTHING: WEB-AUTH-P0 lives in the backend. The proxy
forwards ONLY an allowlisted header set — ``content-type``, ``accept``,
``x-nse-token``, ``cookie``, ``authorization`` — plus ``X-Request-ID``
correlation and a corrected ``Host``. So the caller's Bearer token /
``X-NSE-Token`` header / HttpOnly ``nse_web_auth`` cookie ride through
untouched and the backend's 401/200 decisions are relayed verbatim. The
static SPA bundle itself is public by design (the backend allowlists
``/assets/`` the same way — ``src/nexus_scalp/web/auth.py``). Binding the
default 127.0.0.1 keeps the trust boundary identical to the engine's own;
``--allow-remote`` deliberately prints a loud warning because it exposes an
unauthenticated, TLS-less path to the control plane.

Realtime
--------
``/api/ticks/stream`` (and ANY upstream ``text/event-stream`` response) is
relayed TRUE passthrough: read up to 64 KiB of already-decoded bytes
(``HTTPResponse.read1``), write, flush, repeat — no line buffering, no
aggregation, no compression applied. A client that goes away closes the
upstream connection immediately.

Run (repo root):
    ./.venv/Scripts/python.exe scripts/serve_alt_ui.py
    ./.venv/Scripts/python.exe scripts/serve_alt_ui.py --port 8088 \
        --backend http://127.0.0.1:8080 --dist frontend/dist
Test:
    ./.venv/Scripts/python.exe -m pytest -p no:cacheprovider \
        tests/unit/test_alt_ui_standalone_server.py
Docs: scripts/README-alt-ui.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import sys
import uuid
from dataclasses import dataclass
from http.client import HTTPConnection, IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

LOGGER = logging.getLogger("nse.alt_ui")
#: Neutral default handler so importing this module never crashes a logging
#: config; ``main()`` installs the real StreamHandler.
LOGGER.addHandler(logging.NullHandler())

#: Path prefixes ALWAYS proxied to the backend — never served locally, never
#: SPA-fallback'd, never cached (mirrors frontend/vite.config.ts proxy keys).
#: BUG-266: /app.js + /api_client.js ride the proxy too — they are the
#: backend's bootstrap-Cookie carriers (WEB-UI-BOOTSTRAP Set-Cookie on the
#: public asset). Serving them from disk here would strand a tokenless
#: browser with no cookie jar and re-create the "every /api call 401s" trap
#: behind a proxy that answers 200 for the bundle.
API_PREFIXES: tuple[str, ...] = ("/api", "/health", "/healthz", "/app.js", "/api_client.js")

#: The realtime route (detection is content-type driven; this is a path
#: override so an empty/streaming-starting upstream is still relayed raw).
SSE_PATHS: frozenset[str] = frozenset({"/api/ticks/stream"})

#: Request headers forwarded upstream. Everything else is dropped, including
#: every hop-by-hop header (this host never forwards more than it needs to).
FORWARD_REQUEST_HEADERS: frozenset[str] = frozenset(
    {"content-type", "accept", "x-nse-token", "cookie", "authorization"}
)

#: Correlation header: accepted from the client, always sent upstream, always
#: echoed on responses (same plumbing/shape as web/errors.request_id_from_request).
REQUEST_ID_HEADER = "X-Request-ID"

#: Hop-by-hop response headers that are never re-emitted (RFC 9110 §7.6.1):
#: this host re-frames every response itself (content-length, or close-delimited
#: for SSE).
HOP_BY_HOP_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

#: Upload guard for proxied request bodies (engine payloads are tiny JSON).
MAX_REQUEST_BODY_BYTES = 1 * 1024 * 1024  # 1 MiB
#: Max bytes per raw relay read on the SSE path (bounds per-chunk latency).
SSE_READ_SIZE = 64 * 1024
#: Buffered (non-SSE) upstream read timeout unless NSE_ALT_UI_PROXY_TIMEOUT.
DEFAULT_PROXY_TIMEOUT = 300.0

#: Vite content-hash suffix (e.g. index-Dgg7nCdr.js) -> immutable caching.
_ASSET_HASH_RE = re.compile(r"-[A-Za-z0-9_]{8,}\.[A-Za-z0-9]+$")

CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
}

#: The built bundle uses vite ``base: "/alt/"``; on this standalone host the
#: same dist is mounted at root, so a leading ``/alt`` path segment is
#: transparently stripped (``/alt/assets/x.js`` -> ``assets/x.js``).
APP_BASE_PREFIX = "alt"

_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def new_request_id() -> str:
    """Correlation id, same shape as nexus_scalp.web.errors.new_request_id."""
    return "req_" + uuid.uuid4().hex[:10]


def redact_url(url: str) -> str:
    """Strip credential-bearing query params from a URL for logging.

    ``?token=`` is an accepted WEB-AUTH-P0 transport (EventSource cannot set
    headers), so it must never reach a log line intact.
    """
    return re.sub(
        r"(?i)([?&](?:token|access_token)=)[^&#\s\"']*",
        r"\1[REDACTED]",
        url,
    )


def redact_header_value(name: str, value: str) -> str:
    """Never log credentials: emit shape only for sensitive headers."""
    n = name.strip().lower()
    if n in ("authorization", "cookie", "x-nse-token", "proxy-authorization"):
        return f"[REDACTED:{n} len={len(value)}]"
    return value


def _is_sse_content_type(value: str | None) -> bool:
    return "text/event-stream" in (value or "").lower()


@dataclass(frozen=True)
class BackendTarget:
    """Parsed reverse-proxy upstream (http only — TLS is out of scope for
    this host; see scripts/README-alt-ui.md limitations)."""

    host: str
    port: int

    @property
    def netloc(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def origin(self) -> str:
        return f"http://{self.netloc}"


def parse_backend_url(url: str) -> BackendTarget:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"backend must be an http(s):// URL, got: {url!r}")
    if parsed.scheme == "https":
        raise ValueError(
            f"TLS to the backend is out of scope for this stdlib host "
            f"(keep it localhost): got {url!r}"
        )
    host = parsed.hostname
    if not host:
        raise ValueError(f"backend URL has no host: {url!r}")
    port = parsed.port or 80
    return BackendTarget(host=host, port=int(port))


# ---------------------------------------------------------------------------
# request handler
# ---------------------------------------------------------------------------
class AltUIRequestHandler(BaseHTTPRequestHandler):
    """Static dist server + API reverse proxy for one (dist, backend) pair.

    Instantiate through :func:`build_server` — it binds the configuration into
    a handler subclass. Never reuse the raw class without setting
    ``dist_root`` / ``backend``.
    """

    protocol_version = "HTTP/1.1"
    server_version = "nse-alt-ui"
    sys_version = ""

    # bound by build_server()
    dist_root: Path
    backend: BackendTarget
    api_prefixes: tuple[str, ...] = API_PREFIXES

    _request_id: str = "-"

    # ------------------------------------------------------------- dispatch
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:
        self._dispatch("OPTIONS")

    def _dispatch(self, method: str) -> None:
        raw_path = self.path or "/"
        raw_only, _, query = raw_path.partition("?")
        self._request_id = (self.headers.get("x-request-id") or "").strip()[:64] or new_request_id()

        # Traversal guard applies to EVERY route (proxy included): a path
        # containing '..' (literal or encoded) or backslashes is a scan/bypass
        # attempt, not a legitimate request on this surface. Answer 404 (the
        # fail-closed envelope) so scanners learn nothing about why, and log
        # the rejection loudly with a redacted path.
        if not self._path_is_clean(raw_only):
            LOGGER.warning(
                "rejected suspicious path method=%s path=%s request_id=%s",
                method,
                redact_url(raw_path)[:160],
                self._request_id,
            )
            self._send_error_envelope(404, "RESOURCE_NOT_FOUND", "not found")
            return

        if self._is_api_path(raw_only):
            self._proxy(method, raw_only, query)
        else:
            self._serve_static(method, raw_only)

    @classmethod
    def _is_api_path(cls, raw_only: str) -> bool:
        # Case-sensitive on purpose: /API/status is not a backend route.
        return any(raw_only == p or raw_only.startswith(p + "/") for p in cls.api_prefixes)

    @staticmethod
    def _path_is_clean(raw_only: str) -> bool:
        """Reject encoded + literal traversal before any filesystem/proxy work.

        Decodes up to two rounds so double-encoded (%252e%252e) payloads are
        caught too. Rejects NUL and backslashes. This mirrors (and does not
        contradict) the backend's own traversal refusal in auth.is_public_path.
        """
        if not raw_only:
            return False
        decoded = raw_only
        for _ in range(2):
            nxt = unquote(decoded)
            if nxt == decoded:
                break
            decoded = nxt
        if ".." in raw_only or ".." in decoded:
            return False
        if "\\" in raw_only or "\\" in decoded or "\x00" in decoded:
            return False
        return True

    # -------------------------------------------------------- static (dist)
    def _serve_static(self, method: str, raw_only: str) -> None:
        if method not in ("GET", "HEAD"):
            self._send_error_envelope(405, "BAD_REQUEST", "method not allowed for static paths")
            return
        rel = unquote(raw_only).lstrip("/")
        # /alt, /alt/ and /alt/... address the same bundle root the vite build
        # assumes (base "/alt/").
        if rel == APP_BASE_PREFIX or rel.startswith(APP_BASE_PREFIX + "/"):
            rel = rel[len(APP_BASE_PREFIX) :].lstrip("/")
        if rel in ("", "index.html"):
            self._send_static_file(self.dist_root / "index.html", head_only=method == "HEAD")
            return
        candidate = self._resolve_under_dist(rel)
        if candidate is None:
            # SPA fallback: an unknown non-API path that accepts HTML is a
            # client-side route (e.g. /positions deep link) -> index.html.
            if "text/html" in (self.headers.get("accept") or "").lower():
                self._send_static_file(self.dist_root / "index.html", head_only=method == "HEAD")
                return
            self._send_error_envelope(404, "RESOURCE_NOT_FOUND", "not found")
            return
        self._send_static_file(candidate, head_only=method == "HEAD")

    def _resolve_under_dist(self, rel: str) -> Path | None:
        """Resolve ``rel`` strictly INSIDE dist_root.

        Refuses: escapes (realpath + commonpath — covers ../, encoded ../,
        symlink/junction escapes, drive changes on Windows), directories
        (NO directory listing, ever), and anything that is not a regular file.
        """
        root = self.dist_root.resolve()
        try:
            resolved = (root / rel).resolve()
        except OSError:  # pragma: no cover - exotic filesystem error
            return None
        try:
            if os.path.commonpath([str(root), str(resolved)]) != str(root):
                return None
        except ValueError:  # different drives / UNC edge on Windows
            return None
        if resolved == root or not resolved.is_file():
            return None
        return resolved

    def _send_static_file(self, path: Path, *, head_only: bool) -> None:
        try:
            rel = str(path.relative_to(self.dist_root))
        except ValueError:  # pragma: no cover - defensive: never serve outside
            self._send_error_envelope(404, "RESOURCE_NOT_FOUND", "not found")
            return
        resolved = self._resolve_under_dist(rel)
        if resolved is None:  # pragma: no cover - defensive double guard
            self._send_error_envelope(404, "RESOURCE_NOT_FOUND", "not found")
            return
        try:
            body = resolved.read_bytes()
        except OSError:
            self._send_error_envelope(404, "RESOURCE_NOT_FOUND", "not found")
            return
        ctype = CONTENT_TYPES.get(resolved.suffix.lower(), "application/octet-stream")
        is_index = resolved.name == "index.html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if is_index:
            self.send_header("Cache-Control", "no-store")
        elif self._is_hashed_asset(resolved):
            # content-hashed filenames under /assets/ are immutable forever
            self.send_header("Cache-Control", "max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(REQUEST_ID_HEADER, self._request_id)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _is_hashed_asset(self, path: Path) -> bool:
        """dist/assets/<name>-<contenthash>.<ext> -> safe to pin forever."""
        try:
            under_assets = "assets" in path.relative_to(self.dist_root.resolve()).parts
        except ValueError:
            under_assets = False
        return under_assets and bool(_ASSET_HASH_RE.search(path.name))

    # ------------------------------------------------------------- proxying
    def _proxy(self, method: str, raw_path: str, query: str) -> None:
        request_id = self._request_id
        target_path = raw_path + (("?" + query) if query else "")
        body = self._read_request_body()
        if body is None:  # oversized / un-framable -> error response sent
            return
        conn: HTTPConnection | None = None
        stream = False
        started = False
        try:
            conn = HTTPConnection(
                self.backend.host, self.backend.port, timeout=self._proxy_timeout()
            )
            conn.putrequest(method, target_path, skip_host=True, skip_accept_encoding=True)
            forwarded_names = set()
            for name in self.headers:
                lname = name.lower()
                if lname in FORWARD_REQUEST_HEADERS:
                    conn.putheader(name, self.headers[name])
                    forwarded_names.add(lname)
            if body and "content-type" not in forwarded_names:
                conn.putheader("Content-Type", "application/json")
            if body:
                conn.putheader("Content-Length", str(len(body)))
            conn.putheader(REQUEST_ID_HEADER, request_id)
            conn.putheader("Host", self.backend.netloc)
            conn.endheaders()  # (stdlib name — not end_headers)
            if body:
                conn.send(body)
            resp = conn.getresponse()
            started = True
            stream = _is_sse_content_type(resp.getheader("content-type")) or (raw_path in SSE_PATHS)
            self._relay(resp, stream=stream)
        except (OSError, IncompleteRead) as exc:
            if stream:
                # Mid-stream upstream death: the client sees the event stream
                # close and the UI's EventSource reconnects. Nothing to frame.
                LOGGER.info(
                    "proxy stream ended method=%s path=%s request_id=%s err=%s",
                    method,
                    redact_url(target_path),
                    request_id,
                    type(exc).__name__,
                )
                self.close_connection = True
            elif started:
                # Headers already reached the client: all we can do is drop
                # the connection (client sees a truncated response).
                LOGGER.error(
                    "proxy relay broke method=%s path=%s request_id=%s err=%s",
                    method,
                    redact_url(target_path),
                    request_id,
                    type(exc).__name__,
                )
                self.close_connection = True
            else:
                self._fail_closed(method, target_path, exc)
        except Exception as exc:  # pragma: no cover - defensive, fail closed
            if not started:
                self._fail_closed(method, target_path, exc)
            else:  # pragma: no cover
                LOGGER.exception("proxy post-response failure request_id=%s", request_id)
                self.close_connection = True
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass

    @staticmethod
    def _proxy_timeout() -> float:
        env = os.environ.get("NSE_ALT_UI_PROXY_TIMEOUT", "").strip()
        try:
            return float(env) if env else DEFAULT_PROXY_TIMEOUT
        except ValueError:
            return DEFAULT_PROXY_TIMEOUT

    def _read_request_body(self) -> bytes | None:
        """Content-Length-bounded body read.

        Returns None when an error response was already emitted (413 over the
        guard / 400 un-framable). Never streams an unbounded upload.
        """
        if self.command not in ("POST", "PUT", "PATCH", "DELETE"):
            return b""
        if "chunked" in (self.headers.get("transfer-encoding") or "").lower():
            self.close_connection = True  # unread body: kill the keep-alive
            self._send_error_envelope(
                400, "BAD_REQUEST", "chunked request bodies are not supported"
            )
            return None
        raw_len = self.headers.get("content-length")
        try:
            length = int(raw_len) if raw_len is not None else 0
        except ValueError:
            self.close_connection = True
            self._send_error_envelope(400, "BAD_REQUEST", "invalid content-length")
            return None
        if length > MAX_REQUEST_BODY_BYTES:
            # Do NOT read the body (that is the DoS point); answer + close.
            self.close_connection = True
            self._send_error_envelope(
                413,
                "PAYLOAD_TOO_LARGE",
                f"request body exceeds the {MAX_REQUEST_BODY_BYTES}-byte proxy guard",
            )
            return None
        if length <= 0:
            return b""
        try:
            return self.rfile.read(length)
        except OSError:  # pragma: no cover - client vanished mid-upload
            self.close_connection = True
            return None

    def _relay(self, resp, *, stream: bool) -> None:  # resp: HTTPResponse
        """Relay the upstream response, re-framed for HTTP/1.1."""
        headers: list[tuple[str, str]] = [
            (name, value)
            for name, value in resp.getheaders()
            if name.lower() not in HOP_BY_HOP_RESPONSE_HEADERS
        ]
        names_lower = {n.lower() for n, _ in headers}

        if stream:
            self.send_response(resp.status)
            for name, value in headers:
                if name.lower() in ("content-length", "date", "server"):
                    continue  # this host re-frames; body ends at connection close
                self.send_header(name, value)
            if "cache-control" not in names_lower:
                self.send_header("Cache-Control", "no-cache")
            if "x-accel-buffering" not in names_lower:
                self.send_header("X-Accel-Buffering", "no")
            self.send_header(REQUEST_ID_HEADER, self._request_id)
            # close-delimited body (we stripped transfer-encoding)
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            self._relay_sse(resp)
            return

        body = resp.read()  # buffered: content-length is corrected exactly
        self.send_response(resp.status)
        for name, value in headers:
            if name.lower() in ("content-length", "date", "server"):
                continue  # re-emitted/corrected by this host
            self.send_header(name, value)
        if self.command == "HEAD":
            # No body to measure: keep the upstream's declared length.
            upstream_len = resp.getheader("content-length")
            if upstream_len is not None:
                self.send_header("Content-Length", upstream_len)
        elif resp.status not in (204, 304):
            # 204/304 must not carry content-length (RFC 9110 §15.3.2/§15.4.5)
            self.send_header("Content-Length", str(len(body)))
        if REQUEST_ID_HEADER.lower() not in names_lower:
            self.send_header(REQUEST_ID_HEADER, self._request_id)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _relay_sse(self, resp) -> None:  # resp: HTTPResponse
        """TRUE SSE passthrough: one raw read (<= 64 KiB) -> one write -> flush.

        Whatever byte framing the backend used (event frames, comment
        keepalives) reaches the browser the moment it hits this wire. A dead
        client socket stops the relay, which closes the upstream connection
        (``finally`` in :meth:`_proxy`).
        """
        sock = getattr(self.connection, "sock", None)
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:  # pragma: no cover
                pass
        try:
            while True:
                try:
                    chunk = resp.read1(SSE_READ_SIZE)
                except TimeoutError:
                    # Idle upstream. A real engine emits SSE keepalives every
                    # few seconds, so a comment ping here is redundant
                    # traffic — but it is what lets us NOTICE a dead client
                    # while the upstream is silent (disconnect detection must
                    # not wait for the next data frame, which could be the
                    # next trade tick that never comes on a halted engine).
                    try:
                        self.wfile.write(b":\n\n")
                        self.wfile.flush()
                        continue
                    except OSError:
                        LOGGER.info(
                            "sse client disconnected on keepalive request_id=%s",
                            self._request_id,
                        )
                        self.close_connection = True
                        return
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError:
            LOGGER.info("sse client disconnected request_id=%s", self._request_id)
            self.close_connection = True
        except IncompleteRead as exc:  # upstream died mid-body
            if exc.partial:
                try:
                    self.wfile.write(exc.partial)
                    self.wfile.flush()
                except OSError:  # pragma: no cover
                    pass
            self.close_connection = True

    def _fail_closed(self, method: str, path: str, exc: BaseException) -> None:
        """Backend unreachable -> 502 v1-style envelope. NEVER a local
        fallback, NEVER a cached answer, NEVER an exception leak."""
        LOGGER.error(
            "BACKEND_UNREACHABLE method=%s path=%s request_id=%s backend=%s err=%s: %.200s",
            method,
            redact_url(path),
            self._request_id,
            self.backend.origin,
            type(exc).__name__,
            exc,
        )
        self._send_error_envelope(
            502,
            "BACKEND_UNREACHABLE",
            f"engine backend unreachable at {self.backend.origin}",
        )

    # ------------------------------------------------------------ plumbing
    def _send_error_envelope(self, status: int, code: str, message: str) -> None:
        """Public-safe error body, same envelope style as web/errors.
        safe_error_payload ({"error": {code, message, request_id}} + legacy
        available/success booleans); internals go to logs only."""
        rid = self._request_id
        payload = {
            "ok": False,
            "available": False,
            "success": False,
            "error": {"code": code, "message": message, "request_id": rid},
        }
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(REQUEST_ID_HEADER, rid)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:  # pragma: no cover - client already gone
            self.close_connection = True

    def log_request(self, code: str | int = "-", size: str | int = "-") -> None:
        """One access line per response — WITHOUT any credential material.

        ``self.path`` can carry ``?token=`` (accepted SSE transport) so it is
        redacted; header VALUES (Authorization / Cookie / X-NSE-Token) are
        never logged at all (redact_header_value exists for debug dumps).
        """
        LOGGER.info(
            "%s %s -> %s (%s) request_id=%s",
            self.command,
            redact_url(self.path or "-"),
            code,
            size,
            getattr(self, "_request_id", "-"),
        )

    def log_error(self, fmt: str, *args) -> None:
        LOGGER.warning(fmt % args)

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover
        LOGGER.debug(redact_url(fmt % args))


# ---------------------------------------------------------------------------
# server construction
# ---------------------------------------------------------------------------
class AltUIServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that keeps TCP teardown races off stderr.

    A browser (or a test client) closing a keep-alive/SSE connection between
    requests is NORMAL and surfaces as ConnectionResetError/Abort inside the
    handler thread; the stdlib prints a full traceback for it. Real errors are
    still reported.
    """

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            LOGGER.debug("connection aborted by %s: %s", client_address, exc)
            return
        super().handle_error(request, client_address)


def build_server(
    dist_dir: str | os.PathLike[str],
    backend: BackendTarget,
    listen: tuple[str, int] = ("127.0.0.1", 8088),
) -> AltUIServer:
    """Create (do NOT start) the alt-console host; call ``serve_forever()``.

    ``listen`` defaults to loopback on purpose (auth model above). Tests pass
    port 0 and read back ``server_address``.
    """
    root = Path(dist_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(
            f"dist directory not found: {root} — build the UI first "
            f"(cd frontend && npm run build) or pass --dist / NEXUS_ALT_UI_DIR"
        )

    handler_cls = type(
        "ConfiguredAltUIHandler",
        (AltUIRequestHandler,),
        {"dist_root": root, "backend": backend},
    )
    return AltUIServer(listen, handler_cls)


def _default_dist() -> Path:
    return Path(__file__).resolve().parents[1] / "frontend" / "dist"


def _default_backend_origin() -> str:
    """BUG-266: the engine's REAL bound port when the launcher recorded one.

    The web server auto-increments past occupied ports (8080 -> 8081 on
    boxes where NVIDIA Broadcast holds 8080) and auth_boot.publish() writes
    the actual bind into .env (NSE_WEB_ACTUAL_PORT). A standalone host that
    keeps proxying to a literal :8080 gets connection refusals and the
    operator sees 502/UNAUTHORIZED ghosts — read the recorded truth first.
    Engine-import failure (script run outside the repo venv) falls back to
    the historical 8080 default, never crashes.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from nexus_scalp.web.auth_boot import resolved_web_port

        return f"http://127.0.0.1:{resolved_web_port(default=8080)}"
    except Exception:
        return "http://127.0.0.1:8080"


_REMOTE_WARNING = """\
********************************************************************************
* NSE ALT-UI HOST IS BOUND TO A NON-LOCALHOST ADDRESS ({host})
*
* The auth model: this host enforces NOTHING. Static SPA files go to anyone
* who can connect; every /api call is forwarded with the caller's Bearer /
* X-NSE-Token / nse_web_auth cookie to the engine backend UNCHECKED. That is
* safe only where the backend's own bind is safe. There is NO TLS here, so
* tokens cross the network in cleartext. Never combine with LIVE execution
* mode or an untrusted network. The engine's WEB-AUTH-P0 stays the only
* authentication layer in this design.
********************************************************************************"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="serve_alt_ui",
        description="Standalone alt-console host: static frontend/dist plus a "
        "reverse proxy for /api /health /healthz to the NSE engine backend "
        "(stdlib only, SSE passthrough, fail-closed).",
    )
    parser.add_argument("--port", type=int, default=8088, help="listen port (default 8088)")
    parser.add_argument(
        "--host",
        default=None,
        help="bind address (default 127.0.0.1; --allow-remote selects 0.0.0.0)",
    )
    parser.add_argument(
        "--backend",
        default=None,
        help="engine backend origin to proxy /api /health /healthz to "
        "(default http://127.0.0.1:8080; NSE_API_ORIGIN env fallback, same "
        "knob the vite dev proxy uses)",
    )
    parser.add_argument(
        "--dist",
        default=None,
        help="built UI directory (default <repo>/frontend/dist; "
        "NEXUS_ALT_UI_DIR env fallback, same knob the /alt mount uses)",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="bind 0.0.0.0 instead of 127.0.0.1 (OFF by default; prints a "
        "loud auth-model warning — see README-alt-ui.md)",
    )
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    backend_url = args.backend or os.environ.get("NSE_API_ORIGIN") or _default_backend_origin()
    dist_arg = args.dist or os.environ.get("NEXUS_ALT_UI_DIR") or str(_default_dist())
    try:
        backend = parse_backend_url(backend_url)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    host = args.host or ("0.0.0.0" if args.allow_remote else "127.0.0.1")
    try:
        httpd = build_server(dist_arg, backend, listen=(host, args.port))
    except FileNotFoundError as exc:
        LOGGER.error("%s", exc)
        return 2
    except OSError as exc:
        LOGGER.error("cannot bind %s:%s — %s", host, args.port, exc)
        return 2

    bound_host, bound_port = httpd.server_address[0], httpd.server_address[1]
    if bound_host not in _LOCALHOST_HOSTS:
        print(_REMOTE_WARNING.format(host=bound_host), file=sys.stderr, flush=True)
    handler = httpd.RequestHandlerClass
    LOGGER.info("[ALT-UI] standalone console on http://%s:%d", bound_host, bound_port)
    LOGGER.info("[ALT-UI] dist=%s backend=%s", handler.dist_root, backend.origin)  # type: ignore[attr-defined]
    LOGGER.info(
        "[ALT-UI] proxied prefixes=%s; SSE /api/ticks/stream relayed unbuffered; "
        "static SPA fallback at /alt and /",
        " ".join(API_PREFIXES),
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("[ALT-UI] interrupted — shutting down")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


#: Import-time paranoia probe: the guard must refuse the classic vectors. A
#: regression guard against someone "simplifying" _path_is_clean away.
_TRAVEL_CHECKS = (
    "/..%2f..%2fWindows/win.ini",
    "/%2e%2e/%2e%2e/etc/passwd",
    "/%252e%252e/etc/passwd",
    "/a\\..\\b",
    "/../../x",
)
for _vec in _TRAVEL_CHECKS:
    if AltUIRequestHandler._path_is_clean(_vec.partition("?")[0]):
        raise RuntimeError(f"traversal guard FAILED for {_vec}")
del _vec


if __name__ == "__main__":
    sys.exit(main())
