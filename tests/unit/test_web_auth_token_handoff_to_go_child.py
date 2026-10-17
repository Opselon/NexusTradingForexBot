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
