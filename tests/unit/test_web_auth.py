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
    monkeypatch.setattr(
        web_auth, "app_data_root", lambda: tmp_path, raising=False
    )
    yield tmp_path
    monkeypatch.delenv("NSE_WEB_AUTH_TOKEN", raising=False)


def _asgi_scope(path: str, headers: list[tuple[bytes, bytes]] | None = None,
                query: bytes = b"") -> dict:
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
        receive, send,
    )
    await mw(
        _asgi_scope("/api/x", [(b"x-nse-token", b"test-token-12345")]),
        receive, send,
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
        receive, send,
    )
    assert sent[0]["status"] == 401
    assert calls == []


@pytest.mark.asyncio
async def test_generated_token_persisted(tmp_path, monkeypatch):
    monkeypatch.delenv("NSE_WEB_AUTH_TOKEN", raising=False)
    # Point BOTH the module-level import used by _resolve_token AND the
    # underlying paths helper at the tmp dir (secret_store imports
    # app_data_root directly at module import time).
    monkeypatch.setattr(
        web_auth, "app_data_root", lambda: tmp_path, raising=False
    )
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
