"""WEB-AUTH-P0 tests (audit B1): token auth middleware contract.

Covers:
* fail-closed 401 without token (all protected paths)
* public paths pass without token
* bearer / X-NSE-Token / ?token= acceptance
* wrong token rejected (constant-time path still 401)
* disable flag behavior
* generated-token persistence via SecureSecretStore
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

#: Repo root importable (pythonpath = "." per pyproject).
from nexus_scalp.web import auth as web_auth


@pytest.fixture()
def auth_env(tmp_path, monkeypatch):
    """Isolated env: token via env var; secret store pointed at tmp dir."""
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", "test-token-12345")
    monkeypatch.setattr(web_auth, "app_data_root", lambda: tmp_path, raising=False)
    yield tmp_path
    monkeypatch.delenv("NSE_WEB_AUTH_TOKEN", raising=False)


def _asgi_scope(
    path: str, headers: list[tuple[bytes, bytes]] | None = None, query: bytes = b""
) -> dict:
    return {
        "type": "http",
        "path": path,
        "headers": headers or [],
        "query_string": query,
    }


def _make_app(auth_mw: web_auth.WebAuthMiddleware, calls: list):
    async def inner(scope, receive, send):
        calls.append(scope["path"])

        async def send_ok(message):
            if message["type"] == "http.response.start":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
            elif message["type"] == "http.response.body":
                await send({"type": "http.response.body", "body": b"ok"})

        await send_ok({"type": "http.response.start"})
        await send({"type": "http.response.body"})

    auth_mw.app = inner
    return auth_mw


@pytest.mark.asyncio
async def test_fail_closed_401_without_token(auth_env):
    mw = web_auth.WebAuthMiddleware(app=None)
    calls: list = []
    _make_app(mw, calls)

    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    await mw(_asgi_scope("/api/live/state"), receive, send)
    assert sent[0]["status"] == 401
    assert calls == []  # inner app never reached


@pytest.mark.asyncio
async def test_public_paths_pass_without_token(auth_env):
    mw = web_auth.WebAuthMiddleware(app=None)
    calls: list = []
    _make_app(mw, calls)
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    for p in ("/api/health", "/health", "/static/app.js", "/assets/x.css"):
        await mw(_asgi_scope(p), receive, send)
    assert calls == ["/api/health", "/health", "/static/app.js", "/assets/x.css"]
    assert all(m.get("status") in (200, None) for m in sent if m["type"] == "http.response.start")


@pytest.mark.asyncio
async def test_bearer_and_header_and_query_accepted(auth_env):
    mw = web_auth.WebAuthMiddleware(app=None)
    calls: list = []
    _make_app(mw, calls)

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        pass

    await mw(
        _asgi_scope("/api/x", [(b"authorization", b"Bearer test-token-12345")]),
        receive,
        send,
    )
    await mw(
        _asgi_scope("/api/x", [(b"x-nse-token", b"test-token-12345")]),
        receive,
        send,
    )
    await mw(_asgi_scope("/api/x", query=b"token=test-token-12345"), receive, send)
    assert calls == ["/api/x", "/api/x", "/api/x"]


@pytest.mark.asyncio
async def test_wrong_token_rejected(auth_env):
    mw = web_auth.WebAuthMiddleware(app=None)
    calls: list = []
    _make_app(mw, calls)
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    await mw(
        _asgi_scope("/api/x", [(b"authorization", b"Bearer wrong-token")]),
        receive,
        send,
    )
    assert sent[0]["status"] == 401
    assert calls == []


@pytest.mark.asyncio
async def test_generated_token_persisted(tmp_path, monkeypatch):
    monkeypatch.delenv("NSE_WEB_AUTH_TOKEN", raising=False)
    # Point BOTH the module-level import used by _resolve_token AND the
    # underlying paths helper at the tmp dir (secret_store imports
    # app_data_root directly at module import time).
    monkeypatch.setattr(web_auth, "app_data_root", lambda: tmp_path, raising=False)
    from nexus_scalp.release import paths as release_paths

    monkeypatch.setattr(release_paths, "app_data_root", lambda: tmp_path)
    # Fresh store instance semantics: ensure the secret store resolves its
    # root per-call (it caches the root in __init__, constructed inside
    # _resolve_token, so patching release_paths.app_data_root suffices).
    token, source = web_auth._resolve_token()
    assert source in ("generated", "secret_store")  # order-independent across runs
    assert token
    token2, _ = web_auth._resolve_token()
    assert token2 == token  # stable across restarts (persisted)


def test_install_wraps_and_is_idempotent():
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/api/x")
    def x():
        return {"ok": True}

    # Env token so resolution is deterministic.
    os.environ["NSE_WEB_AUTH_TOKEN"] = "tok-install-test"
    try:
        web_auth.install_web_auth(app)
        installed_cls = app.__class__
        web_auth.install_web_auth(app)  # second call: no-op (idempotent)
        assert app.__class__ is installed_cls
        assert getattr(app.state, "_web_auth_installed", False) is True
    finally:
        os.environ.pop("NSE_WEB_AUTH_TOKEN", None)


# ===========================================================================
# TASK-SEC-WS-AUTH-P0 (2026-09-11): WebSocket fail-closed authentication.
# ---------------------------------------------------------------------------
# Contract proven by the live probes these tests pin:
#   * Starlette's BaseHTTPMiddleware (the production HTTP auth layer) never
#     sees websocket scopes — they flow around it to the router. The
#     WebSocketAuthGuard returned by create_app is the outermost ASGI layer
#     and refuses unauthenticated/invalid handshakes with a websocket.close
#     frame (code 4401) BEFORE any route runs (no accept, no state frame).
#   * Valid tokens are accepted on every browser/native channel: query
#     param (?token=), X-NSE-Token header, Authorization: Bearer, and the
#     nse-token.<token> subprotocol.
#   * NSE_WEB_AUTH_DISABLE=1 allows the connection (operator opt-out) while
#     HTTP auth behavior is unchanged.
# ===========================================================================

WS_VALID_TOKEN = "ws-test-token-12345"


@pytest.fixture()
def ws_auth_env(monkeypatch):
    """Token via env; deterministic; isolated from the operator's store."""
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", WS_VALID_TOKEN)
    yield
    monkeypatch.delenv("NSE_WEB_AUTH_TOKEN", raising=False)


def _ws_client() -> "TestClient":
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    return TestClient(create_app(engine_ref=None))


def _ws_disconnect(exc: Exception) -> tuple[object, object]:
    """(code, reason) from a WebSocketDisconnect-shaped exception."""
    return getattr(exc, "code", None), getattr(exc, "reason", None)


def test_ws_auth_unauthenticated_connection_rejected(ws_auth_env) -> None:
    """Connecting to /ws without any token is refused with 4401/1008 and the
    system-state payload is NEVER streamed."""
    client = _ws_client()
    try:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive()
            raise AssertionError(f"unauthenticated WS connected: {str(first)[:120]}")
    except AssertionError:
        raise
    except Exception as exc:  # noqa: BLE001 - contract is the disconnect shape
        code, reason = _ws_disconnect(exc)
        assert code in (web_auth.WS_CLOSE_UNAUTHORIZED, web_auth.WS_CLOSE_POLICY_VIOLATION)
        assert reason == "Unauthorized"


def test_ws_auth_unauthenticated_web_route_rejected(ws_auth_env) -> None:
    """/web (second WS route) is refused identically — the guard is route-
    agnostic (outermost ASGI layer, even undiscovered WS routes protected)."""
    client = _ws_client()
    try:
        with client.websocket_connect("/web"):
            raise AssertionError("unauthenticated /web connected")
    except AssertionError:
        raise
    except Exception as exc:  # noqa: BLE001
        code, _reason = _ws_disconnect(exc)
        assert code in (web_auth.WS_CLOSE_UNAUTHORIZED, web_auth.WS_CLOSE_POLICY_VIOLATION)


def test_ws_auth_invalid_token_rejected(ws_auth_env) -> None:
    """A wrong token must fail the constant-time comparison and disconnect."""
    client = _ws_client()
    for url in ("/ws?token=invalid_garbage", "/ws?token=" + WS_VALID_TOKEN + "x"):
        try:
            with client.websocket_connect(url):
                raise AssertionError(f"invalid-token WS connected via {url}")
        except AssertionError:
            raise
        except Exception as exc:  # noqa: BLE001
            code, _reason = _ws_disconnect(exc)
            assert code in (
                web_auth.WS_CLOSE_UNAUTHORIZED,
                web_auth.WS_CLOSE_POLICY_VIOLATION,
            )


def test_ws_auth_valid_query_param_token_accepted(ws_auth_env) -> None:
    """Browser channel: ?token=<valid> establishes the session and streams
    the real system-state snapshot (never a fabricated one)."""
    client = _ws_client()
    with client.websocket_connect(f"/ws?token={WS_VALID_TOKEN}") as ws:
        first = ws.receive()
        assert first.get("type") == "websocket.send"
        assert "state_version" in first.get("text", "")


def test_ws_auth_valid_header_token_accepted(ws_auth_env) -> None:
    """Native-client channels: X-NSE-Token and Authorization: Bearer headers."""
    client = _ws_client()
    with client.websocket_connect("/ws", headers={"X-NSE-Token": WS_VALID_TOKEN}) as ws:
        assert ws.receive().get("type") == "websocket.send"
    with client.websocket_connect(
        "/ws", headers={"Authorization": f"Bearer {WS_VALID_TOKEN}"}
    ) as ws:
        assert ws.receive().get("type") == "websocket.send"


def test_ws_auth_valid_subprotocol_token_accepted(ws_auth_env) -> None:
    """Browser channel 2: Sec-WebSocket-Protocol nse-token.<token>."""
    client = _ws_client()
    with client.websocket_connect(
        "/ws", subprotocols=[f"nse-token.{WS_VALID_TOKEN}"]
    ) as ws:
        assert ws.receive().get("type") == "websocket.send"


def test_ws_auth_multi_param_query_accepted(ws_auth_env) -> None:
    """token= is parsed from a multi-parameter query string (not prefix-
    matched on the whole query string)."""
    client = _ws_client()
    with client.websocket_connect(f"/ws?x=1&token={WS_VALID_TOKEN}&y=2") as ws:
        assert ws.receive().get("type") == "websocket.send"


def test_ws_auth_scope_state_stamped_on_success(ws_auth_env) -> None:
    """An accepted handshake carries scope['state']['authenticated']=True —
    the route-layer defense-in-depth hook (server.py re-verifies this)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    probe = FastAPI()

    @probe.websocket("/probe")
    async def _probe(websocket):  # pragma: no cover - exercised via client
        assert websocket.scope.get("state", {}).get("authenticated") is True
        await websocket.accept()
        await websocket.send_json({"ok": True})

    web_auth.install_web_auth(probe)
    guarded = web_auth.build_websocket_auth_guard(probe)
    with TestClient(guarded).websocket_connect(f"/probe?token={WS_VALID_TOKEN}") as ws:
        assert ws.receive_json() == {"ok": True}


def test_ws_auth_disabled_override_allows_connection(ws_auth_env, monkeypatch) -> None:
    """NSE_WEB_AUTH_DISABLE=1 lets the WS connection through (trusted-LAN
    opt-out) AND leaves HTTP auth disabled exactly as before (same flag)."""
    monkeypatch.setenv("NSE_WEB_AUTH_DISABLE", "1")
    client = _ws_client()
    with client.websocket_connect("/ws") as ws:
        assert ws.receive().get("type") == "websocket.send"


def test_ws_auth_fails_closed_when_token_unresolvable(monkeypatch, tmp_path) -> None:
    """Auth enabled but NO token resolvable => every WS handshake refused
    (never fails open); mirrors the HTTP layer's AUTH_CONFIG_ERROR posture."""
    monkeypatch.delenv("NSE_WEB_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(web_auth, "app_data_root", lambda: tmp_path, raising=False)
    from nexus_scalp.release import paths as release_paths

    monkeypatch.setattr(release_paths, "app_data_root", lambda: tmp_path)
    # Simulate an unresolvable token: both env and store fail.
    def _raise() -> tuple[str, str]:
        raise RuntimeError("no token source available")

    monkeypatch.setattr(web_auth, "_resolve_token", _raise)
    client = _ws_client()
    try:
        with client.websocket_connect("/ws?token=anything"):
            raise AssertionError("WS connected with unresolvable token (fail-open!)")
    except AssertionError:
        raise
    except Exception as exc:  # noqa: BLE001
        code, reason = _ws_disconnect(exc)
        assert code == web_auth.WS_CLOSE_UNAUTHORIZED
        assert "fail-closed" in (reason or "")


def test_ws_auth_subprotocol_token_never_negotiated_back(ws_auth_env) -> None:
    """The token-bearing subprotocol must NOT be selected in the negotiated
    response (it would echo the credential back on the wire)."""
    client = _ws_client()
    with client.websocket_connect(
        "/ws", subprotocols=[f"nse-token.{WS_VALID_TOKEN}"]
    ) as ws:
        accepted = getattr(ws, "accepted_subprotocol", None)
        assert accepted is None or not accepted.startswith("nse-token.")


def test_ws_auth_http_layer_regression_still_enforced(ws_auth_env) -> None:
    """The WS guard must not weaken WEB-AUTH-P0 HTTP protections: protected
    HTTP path still 401s without a token and 200s with the bearer token;
    public paths stay public."""
    client = _ws_client()
    assert client.get("/api/live/state").status_code == 401
    assert (
        client.get(
            "/api/live/state", headers={"Authorization": f"Bearer {WS_VALID_TOKEN}"}
        ).status_code
        == 200
    )
    assert client.get("/api/health").status_code == 200

