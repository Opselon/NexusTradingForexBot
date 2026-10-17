"""WEB-AUTH-P0 token handoff to the Go child on the `nexus start` path.

The shipped defect: create_app resolves/creates the control-plane token and
remembers it in the process, but only the double-click launcher re-exported it
to NSE_WEB_AUTH_TOKEN. `nexus start` (what the frozen NexusScalpEngine.exe
runs) booted the Go child with NO token, so Go could not resolve one — it
cannot read Python's DPAPI secret store, and the frozen tree has no repo-root
pyproject.toml for auth_boot.publish() to write a .env next to, while Go's own
CWD is _internal/go-api. Every Go request then answered

    {"ok":false,"error":{"code":"AUTH_CONFIG_ERROR",
     "message":"web auth token unresolvable (fail-closed)"}}

while Python answered 401 on the same path, and the React console rendered the
red banner plus an empty dashboard.

These tests pin the handoff: the enforced token must reach the child env, the
helper must never mint a competing value, and an operator-set value wins.
"""

from __future__ import annotations

import os

import pytest

from nexus_scalp.cli import engine_boot
from nexus_scalp.web import auth as web_auth

_TOKEN_ENV = "NSE_WEB_AUTH_TOKEN"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate the token env + the process-remembered token per test."""
    monkeypatch.delenv(_TOKEN_ENV, raising=False)
    monkeypatch.setattr(web_auth, "_LIVE_WEB_AUTH_TOKEN", {"token": None}, raising=False)
    yield
    monkeypatch.delenv(_TOKEN_ENV, raising=False)


def test_exports_the_enforced_token_to_the_child_env(monkeypatch):
    """The token the process enforces must be visible to the Go child."""
    monkeypatch.setattr(web_auth, "_LIVE_WEB_AUTH_TOKEN", {"token": "tok-live-123"}, raising=False)

    engine_boot._export_web_auth_token_to_child()

    assert os.environ.get(_TOKEN_ENV) == "tok-live-123"


def test_never_generates_a_token(monkeypatch):
    """No enforced token -> export NOTHING (generation stays owned elsewhere).

    A helper that minted its own value would enforce a DIFFERENT token than
    Python's and 401 every request — the divergence WEB-AUTH-P0 exists to stop.
    """
    monkeypatch.setattr(web_auth, "_LIVE_WEB_AUTH_TOKEN", {"token": None}, raising=False)

    engine_boot._export_web_auth_token_to_child()

    assert _TOKEN_ENV not in os.environ


def test_operator_set_env_wins(monkeypatch):
    """An operator-provided NSE_WEB_AUTH_TOKEN is authoritative, never rewritten."""
    monkeypatch.setattr(
        web_auth, "_LIVE_WEB_AUTH_TOKEN", {"token": "resolved-internal"}, raising=False
    )
    monkeypatch.setenv(_TOKEN_ENV, "operator-token")

    engine_boot._export_web_auth_token_to_child()

    assert os.environ[_TOKEN_ENV] == "operator-token"


def test_import_failure_is_isolated(monkeypatch):
    """A broken auth import must not take the engine boot down."""
    import builtins

    real_import = builtins.__import__

    def boom(name, *args, **kwargs):
        if name.endswith("web.auth") or name == "nexus_scalp.web.auth":
            raise RuntimeError("simulated auth import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", boom)

    engine_boot._export_web_auth_token_to_child()  # must not raise

    assert _TOKEN_ENV not in os.environ


def test_lazy_middleware_stack_is_built_before_the_token_is_read(monkeypatch):
    """THE SHIPPED-BUG GATE: a lazy middleware stack made the export a no-op.

    Starlette builds the middleware stack on FIRST REQUEST, so immediately
    after create_app() the stack is None and the token the middleware will
    enforce does not exist yet. An export that reads the token at that moment
    silently exports nothing — verified against a real install, where the
    console was fixed but Go still answered AUTH_CONFIG_ERROR 500. The helper
    must materialize the stack before reading.
    """
    built: list[int] = []

    class _LazyApp:
        middleware_stack = None

        def build_middleware_stack(self):
            built.append(1)
            # Building is what resolves the token in the real app.
            web_auth._LIVE_WEB_AUTH_TOKEN["token"] = "tok-from-stack"
            return object()

    app = _LazyApp()

    engine_boot._export_web_auth_token_to_child(app)

    assert built == [1], "the middleware stack was never built, so the token was read too early"
    assert os.environ.get(_TOKEN_ENV) == "tok-from-stack", (
        "the enforced token did not reach the child env: a lazy middleware "
        "stack makes the export a silent no-op (the shipped AUTH_CONFIG_ERROR)"
    )


def test_already_built_stack_is_not_rebuilt(monkeypatch):
    """Building is only a bootstrap aid; an already-built stack is left alone."""
    monkeypatch.setattr(web_auth, "_LIVE_WEB_AUTH_TOKEN", {"token": "tok-live"}, raising=False)

    rebuilds: list[int] = []

    class _BuiltApp:
        middleware_stack = object()

        def build_middleware_stack(self):  # pragma: no cover - must not run
            rebuilds.append(1)
            return object()

    engine_boot._export_web_auth_token_to_child(_BuiltApp())

    assert rebuilds == []
    assert os.environ.get(_TOKEN_ENV) == "tok-live"


def test_go_api_boot_exports_the_token_before_spawning(monkeypatch):
    """GATE ON THE CALL SITE: booting the Go plane must hand over the token.

    The helper passing in isolation does not prove the product exports it; the
    shipped bug was precisely a missing CALL. This drives the real
    _boot_go_api_plane and asserts the token is in the env the child would
    inherit.
    """
    monkeypatch.setattr(web_auth, "_LIVE_WEB_AUTH_TOKEN", {"token": "tok-boot"}, raising=False)

    captured: dict[str, object] = {}

    from nexus_scalp.web import go_api_bootstrap

    def fake_boot_go_api(**kwargs):
        captured["env_token"] = os.environ.get(_TOKEN_ENV)

    monkeypatch.setattr(go_api_bootstrap, "boot_go_api", fake_boot_go_api)

    engine_boot._boot_go_api_plane("127.0.0.1", 8080)

    assert captured.get("env_token") == "tok-boot", (
        "the Go child was spawned without NSE_WEB_AUTH_TOKEN; it cannot read "
        "Python's secret store and fails closed with AUTH_CONFIG_ERROR"
    )
