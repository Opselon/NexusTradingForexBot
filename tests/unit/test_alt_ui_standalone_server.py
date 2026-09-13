"""STANDALONE-ALT-UI (BACKEND-1 lane): routing, traversal, SSE-flush and
fail-closed behavior of ``scripts/serve_alt_ui.py`` — fully offline against a
stub upstream (``http.server``) and a temp dist dir. No engine, no network.

Run (repo root):
  ./.venv/Scripts/python.exe -m pytest -p no:cacheprovider ^
      tests/unit/test_alt_ui_standalone_server.py
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import serve_alt_ui as alt  # noqa: E402

TOKEN = "s3cr3t-web-auth-token-abc123"
BEARER = "s3cr3t-bearer-value-xyz789"


# ---------------------------------------------------------------------------
# stub upstream
# ---------------------------------------------------------------------------
class _StubHandler(BaseHTTPRequestHandler):
    """Minimal engine stand-in: JSON echo, SSE stream, 401 route, chunked JSON.

    Per-instance state lives on ``self.server`` (``requests`` log,
    ``disconnect_seen`` event) so each test env is hermetic.
    """

    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- helpers
    def _json(self, status: int, payload: dict, extra_headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # ------------------------------------------------------------- routes
    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "method": "GET",
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
        )
        if path == "/api/ticks/stream":
            self._sse(query)
            return
        if path == "/api/private":
            self._json(
                401,
                {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "no token"}},
                {"WWW-Authenticate": "Bearer"},
            )
            return
        if path == "/api/chunked-json":
            body = json.dumps({"chunked": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            half = len(body) // 2
            for part in (body[:half], body[half:]):
                self.wfile.write(b"%x\r\n" % len(part) + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        if path == "/api/status":
            self._json(
                200,
                {
                    "state_version": 7,
                    "auth": self.headers.get("authorization"),
                    "x_nse_token": self.headers.get("x-nse-token"),
                    "cookie": self.headers.get("cookie"),
                    "request_id": self.headers.get("x-request-id"),
                    "query": query,
                },
            )
            return
        if path in ("/health", "/healthz"):
            self._json(200, {"status": "ok", "path": path})
            return
        self._json(404, {"ok": False, "error": {"code": "RESOURCE_NOT_FOUND"}})

    def do_HEAD(self) -> None:
        self.server.requests.append(  # type: ignore[attr-defined]
            {"method": "HEAD", "path": self.path, "headers": {}}
        )
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length) if length else b""
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "method": "POST",
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": raw.decode("utf-8", "replace"),
            }
        )
        self._json(200, {"echo_ct": self.headers.get("content-type"), "got": raw.decode()})

    def _sse(self, query: str) -> None:
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        n_frames = int(params.get("frames", "3"))
        gap = float(params.get("gap", "0.30"))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self._stream_broken = False

        def _write(data: bytes) -> None:
            # Once ANY write fails (the downstream proxy hung up), every
            # further write fails too — but recv() would just sit in its
            # timeout, so short-circuit the producer loop instead.
            if self._stream_broken:
                raise ConnectionError("stream already marked broken")
            try:
                self.wfile.write(data)
                self.wfile.flush()
            except OSError as exc:
                self._stream_broken = True
                raise ConnectionError(f"downstream gone: {exc}") from exc

        try:
            for i in range(n_frames):
                frame = f'event: tick\ndata: {{"state_version": {i}}}\n\n'.encode()
                _write(b"%x\r\n" % len(frame) + frame + b"\r\n")
                time.sleep(gap)
            _write(b"0\r\n\r\n")
        except (OSError, ValueError, ConnectionError):
            # the proxy hung up on us (client disconnect chain) — detecting
            # THIS is exactly what one test asserts.
            self.server.disconnect_seen.set()  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # keep pytest output clean
        pass


class _QuietStubServer(ThreadingHTTPServer):
    """The disconnect test makes the stub's SSE write fail on purpose; the
    stdlib prints that traceback to stderr (ugly + alarming in test logs).
    The stub records the event via ``disconnect_seen`` instead."""

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if not isinstance(exc, (ConnectionError, OSError)):
            super().handle_error(request, client_address)


# ---------------------------------------------------------------------------
# environment fixture
# ---------------------------------------------------------------------------
class _Env:
    def __init__(self, base: str, stub: ThreadingHTTPServer, host: ThreadingHTTPServer):
        self.base = base
        self.stub = stub
        self.host = host

    # ----------------------------------------------------------- plumbing
    def http(
        self, path: str, headers: dict | None = None, method: str = "GET", data: bytes | None = None
    ):
        req = Request(self.base + path, headers=headers or {}, method=method, data=data)
        try:
            resp = urlopen(req, timeout=15)
        except HTTPError as e:
            return e
        return resp

    def fetch(
        self, path: str, headers: dict | None = None, method: str = "GET", data: bytes | None = None
    ):
        """-> (status, {lower: header}, body bytes)"""
        resp = self.http(path, headers, method, data)
        with resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()

    def raw(self, request_bytes: bytes, read_until_close: bool = False, timeout: float = 15.0):
        """Send a literal request, return all bytes received (headers raw —
        needed to prove per-chunk arrival timing and header stripping)."""
        hostport = self.base.split("://", 1)[1]
        host, port = hostport.rsplit(":", 1)
        sock = socket.create_connection((host, int(port)), timeout=timeout)
        try:
            sock.sendall(request_bytes)
            chunks: list[bytes] = []
            if read_until_close:
                while True:
                    try:
                        part = sock.recv(65536)
                    except OSError:
                        break
                    if not part:
                        break
                    chunks.append(part)
            else:
                chunks.append(sock.recv(65536))
            return b"".join(chunks)
        finally:
            sock.close()

    @property
    def requests(self) -> list[dict]:
        return self.stub.requests  # type: ignore[attr-defined]


def _make_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>NSE Alt</title><div id=root></div>")
    (dist / "assets" / "index-Dgg7nCdr.js").write_text("console.log('nse');")
    (dist / "assets" / "index-BQKTd2Zu.css").write_text("body{color:red}")
    (dist / "assets" / "plain.css").write_text("a{}")
    (dist / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    # decoy: a file UNDER dist named like an API route — proves the proxy
    # never answers API requests from disk.
    (dist / "api").mkdir()
    (dist / "api" / "status").write_text('{"FROM_DISK":true}')
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("TOP-SECRET-OUTSIDE-DIST")
    return dist


def _start(tmp_path: Path) -> _Env:
    dist = _make_dist(tmp_path)
    stub = _QuietStubServer(("127.0.0.1", 0), _StubHandler)
    stub.daemon_threads = True  # type: ignore[attr-defined]
    stub.requests = []  # type: ignore[attr-defined]
    stub.disconnect_seen = threading.Event()  # type: ignore[attr-defined]
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    backend = alt.BackendTarget("127.0.0.1", stub.server_address[1])
    host = alt.build_server(dist, backend, listen=("127.0.0.1", 0))
    threading.Thread(target=host.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{host.server_address[1]}"
    return _Env(base, stub, host)


@pytest.fixture()
def env(tmp_path: Path):
    e = _start(tmp_path)
    yield e
    e.host.shutdown()
    e.host.server_close()
    e.stub.shutdown()
    e.stub.server_close()


# ---------------------------------------------------------------------------
# static routing
# ---------------------------------------------------------------------------
class TestStaticRouting:
    def test_index_served_at_root_and_alt(self, env: _Env) -> None:
        for path in ("/", "/alt", "/alt/", "/index.html"):
            status, headers, body = env.fetch(path)
            assert status == 200, path
            assert b"NSE Alt" in body
            assert headers["content-type"].startswith("text/html")
            assert headers["cache-control"] == "no-store"
            assert headers["x-request-id"].startswith("req_")

    def test_asset_mime_and_immutable_caching(self, env: _Env) -> None:
        status, headers, body = env.fetch("/assets/index-Dgg7nCdr.js")
        assert status == 200
        assert headers["content-type"].startswith("text/javascript")
        assert headers["cache-control"] == "max-age=31536000, immutable"
        assert b"nse" in body
        _s, css, _b = env.fetch("/assets/index-BQKTd2Zu.css")
        assert css["content-type"].startswith("text/css")
        # non-hashed file: must NOT get the immutable pin
        _s, plain, _b = env.fetch("/assets/plain.css")
        assert plain["cache-control"] == "no-store"
        _s, svg, _b = env.fetch("/logo.svg")
        assert svg["content-type"] == "image/svg+xml"
        _s, ico, _b = env.fetch("/favicon.ico")
        assert ico["content-type"] == "image/x-icon"

    def test_spa_fallback_on_deep_link(self, env: _Env) -> None:
        # the Positions page deep link: unknown path + Accept: text/html
        status, headers, body = env.fetch("/positions", headers={"Accept": "text/html"})
        assert status == 200
        assert headers["content-type"].startswith("text/html")
        assert b"NSE Alt" in body
        # same with the vite base prefix
        status, _h, body = env.fetch("/alt/positions", headers={"Accept": "text/html"})
        assert status == 200 and b"NSE Alt" in body

    def test_unknown_path_without_html_accept_is_404(self, env: _Env) -> None:
        status, headers, body = env.fetch("/nope/nothing", headers={"Accept": "application/json"})
        assert status == 404
        assert "text/html" not in headers.get("content-type", "")
        payload = json.loads(body)
        assert payload["error"]["code"] == "RESOURCE_NOT_FOUND"
        assert payload["error"]["request_id"]

    def test_no_directory_listing(self, env: _Env) -> None:
        status, _h, body = env.fetch("/assets", headers={"Accept": "application/json"})
        assert status == 404
        assert b"index-Dgg7nCdr.js" not in body
        status2, _h2, body2 = env.fetch("/nonexistent", headers={"Accept": "application/json"})
        assert status2 == 404 and b"TOP-SECRET" not in body2

    def test_head_static(self, env: _Env) -> None:
        status, headers, body = env.fetch("/assets/plain.css", method="HEAD")
        assert status == 200
        assert body == b""
        assert headers["content-length"] == "3"


# ---------------------------------------------------------------------------
# path traversal
# ---------------------------------------------------------------------------
#: Path-traversal corpus: literal, single- and double-encoded, backslash,
#: mixed, and traversal smuggled through the proxied /api prefix.
TRAVERSAL_VECTORS = [
    "/..",
    "/../outside-secret.txt",
    "/..%2f..%2foutside-secret.txt",
    "/%2e%2e/%2e%2e/outside-secret.txt",
    "/%252e%252e/outside-secret.txt",  # double-encoded
    "/assets/..%2f..%2foutside-secret.txt",
    "/assets/..\\..\\outside-secret.txt",
    "/assets/./../../outside-secret.txt",
    "/api/../../outside-secret.txt",  # traversal through the proxy path too
]


class TestTraversal:
    @pytest.mark.parametrize("vector", TRAVERSAL_VECTORS)
    def test_traversal_never_leaks_dist(self, env: _Env, vector: str) -> None:
        status, _headers, body = env.fetch(vector, headers={"Accept": "text/html"})
        assert status in (400, 404), vector
        assert b"TOP-SECRET-OUTSIDE-DIST" not in body
        # and the proxy must NOT have been asked to forward it
        assert all("outside-secret" not in r["path"] for r in env.requests)

    def test_api_traversal_is_not_proxied(self, env: _Env) -> None:
        status, _h, body = env.fetch("/api/..%2fmanagement")
        assert status in (400, 404)
        assert all(
            "..%2f" not in r["path"] and ".." not in urlparse(r["path"]).path for r in env.requests
        )

    def test_resolve_under_dist_pure_unit(self, tmp_path: Path) -> None:
        dist = _make_dist(tmp_path)
        handler_cls = type("T", (alt.AltUIRequestHandler,), {})
        handler_cls.dist_root = dist.resolve()
        self_ = handler_cls.__new__(handler_cls)
        assert self_._resolve_under_dist("index.html") == (dist / "index.html").resolve()
        assert self_._resolve_under_dist("../outside-secret.txt") is None
        assert self_._resolve_under_dist("assets/../../outside-secret.txt") is None
        assert self_._resolve_under_dist("assets") is None  # never a directory
        assert self_._resolve_under_dist("missing.js") is None

    def test_path_clean_guard_pure_unit(self) -> None:
        clean = ["/", "/alt", "/assets/a.js", "/positions", "/api/status", "/a%20b"]
        dirty = ["/..", "/a/..b/..", "/%2e%2e/x", "/%252e%252e/x", "/a\\b", "/x\x00y", ""]
        for p in clean:
            assert alt.AltUIRequestHandler._path_is_clean(p), p
        for p in dirty:
            assert not alt.AltUIRequestHandler._path_is_clean(p), p


# ---------------------------------------------------------------------------
# reverse proxy
# ---------------------------------------------------------------------------
class TestProxy:
    def test_get_token_header_passthrough(self, env: _Env) -> None:
        status, headers, body = env.fetch(
            "/api/status",
            headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"},
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["auth"] == f"Bearer {TOKEN}"
        assert headers["content-type"].startswith("application/json")
        # content-length corrected to the real relayed body length
        assert int(headers["content-length"]) == len(body)
        assert "transfer-encoding" not in headers

    def test_cookie_and_x_nse_token_passthrough(self, env: _Env) -> None:
        status, _h, body = env.fetch(
            "/api/status",
            headers={"Cookie": f"nse_web_auth={TOKEN}", "X-NSE-Token": TOKEN},
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["cookie"] == f"nse_web_auth={TOKEN}"
        assert payload["x_nse_token"] == TOKEN

    def test_non_allowlisted_headers_dropped(self, env: _Env) -> None:
        env.fetch("/api/status", headers={"User-Agent": "probe-ua", "X-Evil": "drop-me"})
        seen = env.requests[-1]["headers"]
        assert seen.get("x-evil") is None
        assert seen.get("user-agent") is None
        assert seen.get("host")  # corrected Host of the backend
        assert seen.get("x-request-id", "").startswith("req_")

    def test_query_string_forwarded(self, env: _Env) -> None:
        status, _h, body = env.fetch("/api/status?since=12&live=1")
        assert status == 200
        assert json.loads(body)["query"] == "since=12&live=1"

    def test_request_id_echo_and_forward(self, env: _Env) -> None:
        status, headers, body = env.fetch(
            "/api/status", headers={"X-Request-ID": "req_browser_supplied"}
        )
        assert status == 200
        assert headers["x-request-id"] == "req_browser_supplied"
        assert json.loads(body)["request_id"] == "req_browser_supplied"

    def test_post_body_forwarded_with_content_type(self, env: _Env) -> None:
        payload = json.dumps({"active": True}).encode()
        status, _h, body = env.fetch(
            "/api/engine/toggle",
            headers={"Content-Type": "application/json"},
            method="POST",
            data=payload,
        )
        assert status == 200
        assert json.loads(body)["got"] == payload.decode()
        rec = env.requests[-1]
        assert rec["headers"].get("content-length") == str(len(payload))

    def test_upstream_401_relays_verbatim(self, env: _Env) -> None:
        resp = env.http("/api/private")
        assert resp.code == 401  # type: ignore[union-attr]
        assert resp.headers.get("WWW-Authenticate") == "Bearer"  # type: ignore[union-attr]
        data = json.loads(resp.read())  # type: ignore[union-attr]
        assert data["error"]["code"] == "UNAUTHORIZED"

    def test_chunked_upstream_reeframed_with_content_length(self, env: _Env) -> None:
        status, headers, body = env.fetch("/api/chunked-json")
        assert status == 200
        assert json.loads(body) == {"chunked": True}
        assert headers.get("transfer-encoding") is None
        assert int(headers["content-length"]) == len(body)

    def test_api_never_served_from_disk_or_cache(self, env: _Env) -> None:
        # dist/api/status EXISTS on disk, but /api/status must always go
        # upstream — even for a text/html Accept (no SPA fallback on API).
        status, _h, body = env.fetch("/api/status", headers={"Accept": "text/html"})
        assert status == 200
        assert b"FROM_DISK" not in body
        assert b"NSE Alt" not in body

    def test_oversized_body_rejected_without_reading(self, env: _Env) -> None:
        req = (
            b"POST /api/engine/toggle HTTP/1.1\r\n"
            + f"Host: {env.base.split('://')[1]}\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + b"Content-Length: 5000000\r\n\r\n"
        )
        # read to EOF: the host answers 413 WITHOUT consuming the 5 MB body
        # and closes the connection (that is the anti-DoS point).
        raw = env.raw(req, read_until_close=True)
        head = raw.split(b"\r\n", 1)[0]
        assert b"413" in head
        assert b"PAYLOAD_TOO_LARGE" in raw

    def test_chunked_upload_rejected(self, env: _Env) -> None:
        raw = env.raw(
            b"POST /api/engine/toggle HTTP/1.1\r\n"
            b"Host: x\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
        )
        assert b" 400 " in raw.split(b"\r\n", 1)[0]


# ---------------------------------------------------------------------------
# SSE passthrough
# ---------------------------------------------------------------------------
class TestSSEPassthrough:
    def _stream(self, env: _Env, frames: int, gap: float, close_after: int | None = None):
        """Read the proxied stream at raw-socket speed.

        Returns (arrival_times_per_recv, assembled_bytes). ``close_after``
        hangs up (closes the socket) after that many recv()s — used to prove
        the upstream connection is torn down with the client.
        """
        hostport = env.base.split("://", 1)[1]
        host, port = hostport.rsplit(":", 1)
        sock = socket.create_connection((host, int(port)), timeout=20)
        arrivals: list[float] = []
        buf = b""
        try:
            req = (
                f"GET /api/ticks/stream?frames={frames}&gap={gap:.2f} HTTP/1.1\r\n"
                f"Host: {hostport}\r\n"
                f"Accept: text/event-stream\r\n\r\n"
            )
            sock.sendall(req.encode("ascii"))
            t0 = time.monotonic()
            while True:
                if close_after is not None and len(arrivals) >= close_after:
                    break
                try:
                    part = sock.recv(65536)
                except OSError:
                    break
                if not part:
                    break  # upstream stream finished -> proxy closed the wire
                arrivals.append(time.monotonic() - t0)
                buf += part
        finally:
            sock.close()
        return arrivals, buf

    def test_client_disconnect_closes_upstream(self, env: _Env) -> None:
        # 60 frames x 0.25s = 15s of runway; hang up after two recv()s and the
        # stub must see its upstream connection die well before then (the
        # proxy relays, then tears the upstream down with the client).
        arrivals, _buf = self._stream(env, frames=60, gap=0.25, close_after=2)
        assert arrivals, "expected at least one proxied frame"
        assert env.stub.disconnect_seen.wait(10.0), (  # type: ignore[attr-defined]
            "upstream connection must be closed after client disconnect"
        )

    def test_headers_relayed_hop_by_hop_stripped(self, env: _Env) -> None:
        _arrivals, buf = self._stream(env, frames=1, gap=0.05)
        head = buf.split(b"\r\n\r\n", 1)[0].lower()
        assert b"200 ok" in head.split(b"\r\n", 1)[0]
        assert b"content-type: text/event-stream" in head
        assert b"transfer-encoding" not in head  # stripped + re-framed
        assert b"connection: close" in head
        assert b"x-request-id: req_" in head
        assert b"x-accel-buffering: no" in head

    def test_frames_arrive_unbuffered_per_chunk(self, env: _Env) -> None:
        """The proof of TRUE passthrough: the upstream spaces frames 0.3s
        apart. A buffering relay delivers the whole body in ONE recv at the
        end; an unbuffered relay produces one recv per frame with the same
        cadence. We assert both the recv count and the time spread."""
        frames, gap = 4, 0.30
        arrivals, buf = self._stream(env, frames=frames, gap=gap)
        assert buf.count(b"event: tick") == frames
        assert b'"state_version": 0' in buf and b'"state_version": 3' in buf
        # chunk boundaries survived: one recv per upstream frame (the header
        # block may merge with frame 0 — nothing beyond that is aggregated):
        assert len(arrivals) >= frames, arrivals
        # spread across the stream matches the upstream cadence, NOT a
        # single end-of-stream dump: first->last >= 2 frame gaps.
        spread = arrivals[-1] - arrivals[0]
        assert spread >= 2 * gap, (arrivals, spread)
        # and the first bytes were delivered long before the stream ended
        assert arrivals[0] < gap, arrivals


# ---------------------------------------------------------------------------
# fail-closed when the backend is down
# ---------------------------------------------------------------------------
class TestFailClosed:
    def test_502_envelope_backend_down(self, tmp_path: Path) -> None:
        # a port nobody listens on (bind-then-close gives a refused port)
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        dist = _make_dist(tmp_path)
        server = alt.build_server(dist, alt.BackendTarget("127.0.0.1", dead_port), ("127.0.0.1", 0))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = _Env(f"http://127.0.0.1:{server.server_address[1]}", None, server)  # type: ignore[arg-type]
        try:
            for path in ("/api/status", "/health", "/healthz"):
                resp = env.http(path)
                assert resp.status == 502, path
                body = resp.read()
                payload = json.loads(body)
                assert payload["error"]["code"] == "BACKEND_UNREACHABLE"
                assert payload["error"]["request_id"].startswith("req_")
                assert payload["available"] is False and payload["success"] is False
                assert resp.headers.get("content-type", "").startswith("application/json")
                assert resp.headers.get("cache-control") == "no-store"
                assert int(resp.headers["content-length"]) == len(body)
                # fail-closed: NOT the SPA fallback, NOT the dist decoy file
                assert b"FROM_DISK" not in body and b"NSE Alt" not in body
        finally:
            server.shutdown()
            server.server_close()

    def test_html_accept_still_fails_closed(self, tmp_path: Path) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        dist = _make_dist(tmp_path)
        server = alt.build_server(dist, alt.BackendTarget("127.0.0.1", dead_port), ("127.0.0.1", 0))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = _Env(f"http://127.0.0.1:{server.server_address[1]}", None, server)  # type: ignore[arg-type]
        try:
            resp = env.http("/api/status", headers={"Accept": "text/html"})
            assert resp.status == 502
            assert b"NSE Alt" not in resp.read()
        finally:
            server.shutdown()
            server.server_close()


# ---------------------------------------------------------------------------
# logging redaction
# ---------------------------------------------------------------------------
class TestLogRedaction:
    def test_credentials_never_logged(self, env: _Env, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="nse.alt_ui"):
            # header credential
            env.fetch("/api/status", headers={"Authorization": f"Bearer {BEARER}"})
            # cookie credential
            env.fetch("/api/status", headers={"Cookie": f"nse_web_auth={TOKEN}"})
            # query-param credential (SSE transport) — forwarded upstream, redacted in logs
            env.fetch("/api/status?token=***")
        text = caplog.text
        assert BEARER not in text
        assert TOKEN not in text
        assert "query-param-secret" not in text
        assert "[REDACTED]" in text
        # one access line per proxied request, carrying the correlation id
        assert text.count("GET /api/status") >= 3
        assert "request_id=req_" in text

    def test_redactors_pure_unit(self) -> None:
        assert alt.redact_url("/api/status?token=***") == "/api/status?token=[REDACTED]"
        assert alt.redact_url("/x&a=1&token=***&b=2") == "/x&a=1&token=[REDACTED]&b=2"
        assert alt.redact_url("/api/status?since=1") == "/api/status?since=1"
        assert "super-secret" not in alt.redact_header_value("Authorization", "Bearer super-secret")
        assert "super-secret" not in alt.redact_header_value("cookie", "session=super-secret")
        assert alt.redact_header_value("accept", "text/html") == "text/html"


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------
class TestCli:
    def test_parse_backend_url(self) -> None:
        b = alt.parse_backend_url("http://127.0.0.1:8080")
        assert (b.host, b.port, b.origin) == ("127.0.0.1", 8080, "http://127.0.0.1:8080")
        with pytest.raises(ValueError):
            alt.parse_backend_url("https://engine.internal")
        with pytest.raises(ValueError):
            alt.parse_backend_url("not a url")

    def test_main_defaults_loopback_and_remote_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        captured: dict = {}

        # BUG-267: the backend default now consults the launcher's recorded
        # actual bind (.env NSE_WEB_ACTUAL_PORT). Pin it to the historical
        # 8080 so this test keeps asserting the DEFAULT, not this machine's
        # last boot; the recorded-port behavior has its own tests below.
        from nexus_scalp.web import auth_boot

        monkeypatch.delenv(auth_boot.ENV_ACTUAL_PORT, raising=False)
        monkeypatch.delenv("NSE_WEB_PORT", raising=False)
        monkeypatch.delenv("NSE_API_ORIGIN", raising=False)
        monkeypatch.setattr(auth_boot, "_dotenv_path", lambda: None)

        class _Fake:
            def __init__(self, addr):
                self.server_address = addr
                self.RequestHandlerClass = type(
                    "H", (alt.AltUIRequestHandler,), {"dist_root": tmp_path}
                )

            def serve_forever(self):
                captured["ran"] = True

            def shutdown(self):
                pass

            def server_close(self):
                pass

        def fake_build(dist, backend, listen=("127.0.0.1", 8088)):
            captured["listen"] = listen
            captured["backend"] = backend
            return _Fake(listen)

        monkeypatch.setattr(alt, "build_server", fake_build)
        rc = alt.main(["--dist", str(tmp_path)])
        assert rc == 0 and captured["ran"]
        assert captured["listen"] == ("127.0.0.1", 8088)
        assert captured["backend"].origin == "http://127.0.0.1:8080"
        assert "NON-LOCALHOST" not in capsys.readouterr().err

        rc = alt.main(["--dist", str(tmp_path), "--allow-remote", "--port", "9999"])
        assert rc == 0
        assert captured["listen"] == ("0.0.0.0", 9999)
        err = capsys.readouterr().err
        assert "NON-LOCALHOST ADDRESS (0.0.0.0)" in err
        assert "auth model" in err.lower() or "enforces NOTHING" in err

    def test_main_missing_dist_is_actionable(self, tmp_path: Path) -> None:
        rc = alt.main(["--dist", str(tmp_path / "nope")])
        assert rc == 2

    def test_backend_default_uses_recorded_actual_port(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BUG-267: after the launcher auto-incremented (8080 busy -> 8081),
        the standalone host must proxy to the RECORDED bind, not :8080."""
        from nexus_scalp.web import auth_boot

        env_file = tmp_path / ".env"
        env_file.write_text("NSE_WEB_ACTUAL_PORT=8081\n", encoding="utf-8")
        monkeypatch.delenv(auth_boot.ENV_ACTUAL_PORT, raising=False)
        monkeypatch.delenv("NSE_WEB_PORT", raising=False)
        monkeypatch.delenv("NSE_API_ORIGIN", raising=False)
        monkeypatch.setattr(auth_boot, "_dotenv_path", lambda: env_file)
        assert alt._default_backend_origin() == "http://127.0.0.1:8081"

    def test_backend_default_falls_back_without_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Import failure (script outside the repo venv) must never crash
        the host — historical :8080 default is preserved."""
        import builtins

        real_import = builtins.__import__

        def boom(name, *a, **k):
            if name.startswith("nexus_scalp"):
                raise ImportError("no engine package")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", boom)
        assert alt._default_backend_origin() == "http://127.0.0.1:8080"


if __name__ == "__main__":  # standalone probe (no pytest needed)
    sys.exit(pytest.main([__file__, "-v", "-p", "no:cacheprovider"]))
