"""MSLIE REST API integration tests.

TEST-MS LIE-API-01..NN: /api/mslie/status + /api/mslie/features + mslie
section in /api/debug/state. Uses a lightweight engine stub so the suite
stays fast and does not require torch/model artifacts.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "src")

from nexus_scalp.mslie import MarketStructureEngine


@dataclass
class _Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 100


def _trending_bars(n: int = 160) -> list[_Bar]:
    bars: list[_Bar] = []
    t = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)
    price = 2000.0
    for i in range(n):
        drift = 0.6 if i > 80 else 0.0
        o = price
        c = price + drift + (0.4 if i % 2 else -0.2)
        h = max(o, c) + 0.8
        l = min(o, c) - 0.8
        bars.append(_Bar(t, o, h, l, c, 100 + (i % 7) * 20))
        price = c
        t += timedelta(minutes=1)
    return bars


class _EngineState:
    """Minimal app.state.engine stand-in exposing mslie_engine."""

    def __init__(self) -> None:
        self.mslie_engine = MarketStructureEngine(symbol="XAUUSD", timeframe="M1")
        self.mslie_engine.analyze_market(_trending_bars())


class _AppState:
    def __init__(self) -> None:
        self.engine = _EngineState()


def _make_app() -> Any:
    """Builds a minimal FastAPI app with only the MSLIE routes registered.

    The real server.py wires these routes inside create_app(); here we
    import the same route functions via the module to keep the test fast.
    """
    from nexus_scalp.web import server as server_module

    # Reuse the app factory but with a stub engine state.
    app = server_module.create_app()
    app.state.engine = _EngineState()
    return app


# ---------------------------------------------------------------------------
# The routes live inside create_app() closure — use the real app factory with
# a stubbed engine to avoid heavy LiveEngine construction (torch init).
# ---------------------------------------------------------------------------


WEB_TOKEN = "mslie-test-token"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Authenticated client: create_app installs WEB-AUTH-P0 unconditionally,
    so /api/mslie/* + /api/debug/state 401 without a token (CI run #3330).
    The token is set BEFORE create_app — the middleware resolves its token
    at install time from the live env — and sent as a Bearer header, the
    same pattern tests/unit/test_live_state_contract.py uses."""
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", WEB_TOKEN)
    app = _make_app()
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {WEB_TOKEN}"})
    return c


def test_mslie_status_endpoint(client: TestClient) -> None:
    r = client.get("/api/mslie/status")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["available"] is True
    assert data["status"] == "ONLINE"
    es = data["engine_status"]
    assert es["market_structure_engine"] == "ONLINE"
    assert es["feature_generator"] == "RUNNING"
    assert es["latency_ms"] is not None
    ctx = data["market_context"]
    assert ctx["symbol"] == "XAUUSD"
    assert ctx["structure"] in ("BULLISH", "BEARISH", "RANGING")
    assert 0.0 <= ctx["confidence"] <= 100.0


def test_mslie_features_endpoint(client: TestClient) -> None:
    r = client.get("/api/mslie/features")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    vec = data["vector"]
    assert vec["version"] == "MarketIntelligenceFeatureVectorV1"
    assert "regime" in vec and "liquidity_map" in vec
    assert "smart_money" in vec


def test_mslie_features_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh engine without a computation reports NO_MSLIE_VECTOR."""
    from nexus_scalp.web import server as server_module

    # WEB-AUTH-P0: token before create_app (middleware resolves at install)
    # + Bearer header, as in the client fixture above.
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", WEB_TOKEN)
    app = server_module.create_app()
    state = _AppState()
    state.engine.mslie_engine = MarketStructureEngine()  # never analyzed
    app.state.engine = state.engine
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {WEB_TOKEN}"})
    r = client.get("/api/mslie/features")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["reason"] == "NO_MSLIE_VECTOR"


def test_debug_snapshot_contains_mslie_section(client: TestClient) -> None:
    r = client.get("/api/debug/state")
    assert r.status_code == 200
    data = r.json()
    ms = data.get("mslie")
    assert ms is not None
    assert ms["available"] is True
    assert "engine_status" in ms
    assert ms["engine_status"]["market_structure_engine"] == "ONLINE"
