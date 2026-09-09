"""SEC-AUDIT regression tests (Agent-9 security/integrity audit).

Closes the /api/replay/toggle LIVE-flip hole (F-1):

    POST /api/replay/toggle with active=false unconditionally forced
    ``engine.config.execution.mode = ExecutionMode.LIVE`` — a paper/shadow
    session was hot-flipped to LIVE with NO adapter realignment, NO
    operator confirmation, and NO settings persistence (diverging from
    both the CLI start path and /api/engine/mode).

Contract pinned here:
  * toggling replay OFF must never move the execution mode to LIVE;
  * leaving replay restores the mode captured at entry (never a
    hardcoded LIVE);
  * replay activation is refused on a LIVE engine (synthetic replay
    stream must never be attached to a real-money session);
  * the route stays behind the web-auth token.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.web.server import create_app

AUTH_HEADERS = {"Authorization": "Bearer sec-audit-token-12345"}


class _FakeModeHolder:
    """Mimics engine.config.execution without a full AppConfig."""

    def __init__(self, mode: ExecutionMode) -> None:
        self.mode = mode


class _FakeEngine:
    """Minimal engine surface for the replay-toggle route."""

    def __init__(self, mode: ExecutionMode) -> None:
        self.config = SimpleNamespace(execution=_FakeModeHolder(mode))


def _client(monkeypatch, mode: ExecutionMode) -> tuple[TestClient, _FakeEngine, FastAPI]:
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", "sec-audit-token-12345")
    engine = _FakeEngine(mode)
    app = create_app(engine_ref=engine)  # type: ignore[arg-type]
    return TestClient(app), engine, app


def test_replay_toggle_off_never_flips_to_live(monkeypatch) -> None:
    """The historic defect: replay OFF forced mode=LIVE on a PAPER engine."""
    client, engine, _app = _client(monkeypatch, ExecutionMode.PAPER)

    # Enter replay (PAPER-safe), then leave it.
    r_on = client.post(
        "/api/replay/toggle", json={"active": True, "speed": 1}, headers=AUTH_HEADERS
    )
    assert r_on.status_code == 200, r_on.text
    assert engine.config.execution.mode is not ExecutionMode.LIVE

    r_off = client.post(
        "/api/replay/toggle", json={"active": False, "speed": 1}, headers=AUTH_HEADERS
    )
    assert r_off.status_code == 200, r_off.text
    assert engine.config.execution.mode is not ExecutionMode.LIVE, (
        "replay toggle OFF must never set execution mode to LIVE"
    )


def test_replay_toggle_off_restores_prior_mode(monkeypatch) -> None:
    """Leaving replay restores the mode the engine had BEFORE entering it."""
    client, engine, _app = _client(monkeypatch, ExecutionMode.PAPER)

    r_on = client.post(
        "/api/replay/toggle", json={"active": True, "speed": 1}, headers=AUTH_HEADERS
    )
    assert r_on.status_code == 200
    r_off = client.post(
        "/api/replay/toggle", json={"active": False, "speed": 1}, headers=AUTH_HEADERS
    )
    assert r_off.status_code == 200
    assert engine.config.execution.mode == ExecutionMode.PAPER


def test_replay_toggle_requires_auth(monkeypatch) -> None:
    """State-mutating mode routes stay behind the web-auth token."""
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", "sec-audit-token-12345")
    engine = _FakeEngine(ExecutionMode.PAPER)
    app = create_app(engine_ref=engine)  # type: ignore[arg-type]
    client = TestClient(app)

    r = client.post("/api/replay/toggle", json={"active": True, "speed": 1})
    assert r.status_code == 401
    assert engine.config.execution.mode == ExecutionMode.PAPER


def test_replay_activation_refused_on_live_engine(monkeypatch) -> None:
    """A LIVE session must never accept a synthetic replay stream."""
    client, engine, app = _client(monkeypatch, ExecutionMode.LIVE)

    r = client.post("/api/replay/toggle", json={"active": True, "speed": 1}, headers=AUTH_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("success") is False, body
    assert engine.config.execution.mode == ExecutionMode.LIVE
    assert app.state.is_replaying is False
