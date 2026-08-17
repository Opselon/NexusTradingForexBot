"""
Unit Tests - /api/mt5/status endpoint + runtime-mode safety (Phase 14)
=======================================================================
Covers: account snapshot, live tick, chart history source, runtime mode
(PAPER live / LIVE_CONFIGURED-MT5_DISCONNECTED / TRADE_BLOCKED), MT5
disconnect behavior, stale tick, state version, error payload safety.
Uses the PaperMT5Adapter (no real broker needed) and a forced-disconnect
adapter to simulate MT5 outage.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState
from nexus_scalp.adapters.mt5.providers import AccountSnapshot, BrokerTickSnapshot
from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.models import AccountInfo, TickData
from nexus_scalp.web.server import create_app


def _make_config(tmp_path: Path, mode: str = "PAPER") -> AppConfig:
    return AppConfig.model_validate(
        {
            "execution": {"symbol": "XAUUSD", "mode": mode, "magic_number": 888201},
            "model": {
                "model_artifact_path": str(tmp_path / "model.pt"),
                "feature_schema_version": "v1.0",
                "confidence_threshold": 0.20,
            },
            "risk": {
                "risk_per_trade_pct": 2.0,
                "max_account_drawdown_pct": 10.0,
                "max_concurrent_positions": 5,
                "max_spread_points": 50,
                "max_allowed_lots": 10.0,
                "max_margin_usage_pct": 50.0,
            },
            "telegram": {"enabled": False, "bot_token": "x", "admin_id": "y"},
        }
    )


class _FakeShadowAdapter(PaperMT5Adapter):
    """Paper adapter that reports disconnected - simulates MT5 outage."""

    def is_connected(self) -> bool:
        return False

    def connection_state(self) -> MT5ConnectionState:
        state = MT5ConnectionState()
        state.set_state(MT5ConnectionState.DISCONNECTED, "forced test disconnect")
        return state

    def get_last_tick(self, symbol: str) -> TickData:
        raise RuntimeError("MT5 disconnected: no tick data (simulated outage)")

    def get_broker_tick(self, symbol: str) -> BrokerTickSnapshot:
        snap = BrokerTickSnapshot()
        snap.symbol = symbol
        snap.available = False
        snap.source = "UNAVAILABLE"
        return snap

    def get_account_snapshot(self) -> AccountSnapshot:
        snap = AccountSnapshot()
        snap.available = False
        snap.source = "UNAVAILABLE"
        snap.error_state = {"operation": "account_info", "code": None, "message": "disconnected"}
        return snap

    def get_account_info(self) -> AccountInfo:
        # Real MT5 raises on disconnect (assert-connected); match that so the
        # legacy fallback path can never masquerade disconnected state.
        raise RuntimeError("MT5 disconnected: no account data (simulated outage)")


@pytest.fixture()
def paper_engine(tmp_path: Path):
    cfg = _make_config(tmp_path)
    adapter = PaperMT5Adapter(symbol="XAUUSD", initial_balance=10000.0)
    adapter.connect()
    engine = LiveEngine(config=cfg, adapter=adapter, force_fresh_model=True)
    engine._account_snapshot = adapter.get_account_snapshot()
    engine._update_runtime_mode()
    app = create_app(engine_ref=engine)
    return engine, TestClient(app)


@pytest.fixture()
def offline_engine(tmp_path: Path):
    cfg = _make_config(tmp_path, mode="LIVE")
    adapter = _FakeShadowAdapter(symbol="XAUUSD", initial_balance=10000.0)
    engine = LiveEngine(config=cfg, adapter=adapter, force_fresh_model=True)
    engine._account_snapshot = None
    engine._update_runtime_mode()
    app = create_app(engine_ref=engine)
    return engine, TestClient(app)


class TestMt5StatusEndpoint:
    def test_account_snapshot_present(self, paper_engine) -> None:
        _engine, client = paper_engine
        r = client.get("/api/mt5/status")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        acct = body["account"]
        assert acct["available"] is True
        assert acct["source"] == "PAPER_SIMULATION"
        assert acct["balance"] == 10000.0
        assert acct["equity"] == 10000.0
        assert acct["login"] is not None

    def test_live_tick_present(self, paper_engine) -> None:
        _engine, client = paper_engine
        r = client.get("/api/mt5/status")
        body = r.json()
        sym = body["symbol"]
        assert sym["available"] is True
        assert sym["specification"]["name"] == "XAUUSD"
        assert "bid" in sym["current_tick"]
        assert sym["spread_points"] is not None
        assert sym["tick_stale"] is False

    def test_chart_history_paper_source(self, paper_engine) -> None:
        _engine, client = paper_engine
        r = client.get("/api/chart/history")
        body = r.json()
        # Paper adapter implements get_rate_history -> bars available
        assert len(body["bars"]) > 0
        assert body["source"] in ("MT5", "ENGINE_STATE")
        assert body["requested"] >= body["returned"]
        assert body["first_timestamp"] is not None
        assert body["last_timestamp"] is not None

    def test_runtime_mode_paper(self, paper_engine) -> None:
        _engine, client = paper_engine
        r = client.get("/api/status")
        st = r.json()
        assert st["runtime_mode"] == "PAPER"

    def test_live_configured_but_mt5_disconnected(self, offline_engine) -> None:
        """Config says LIVE but MT5 is disconnected -> explicit degraded mode."""
        _engine, client = offline_engine
        r = client.get("/api/status")
        st = r.json()
        assert "LIVE" in st["runtime_mode"]
        assert "DISCONNECTED" in st["runtime_mode"]
        # No fake prices / no fake account data
        assert st["bid"] is None
        assert st["account"]["available"] is False

    def test_mt5_disconnect_surfaces_in_live_state(self, offline_engine) -> None:
        _engine, client = offline_engine
        r = client.get("/api/live/state")
        live = r.json()
        mt5 = live["mt5"]
        assert mt5["available"] is True
        assert mt5["connection"]["state"] == "DISCONNECTED"

    def test_state_version_monotonic(self, paper_engine) -> None:
        _engine, client = paper_engine
        v1 = client.get("/api/status").json()["state_version"]
        v2 = client.get("/api/status").json()["state_version"]
        assert v2 > v1

    def test_no_tracebacks_exposed(self, paper_engine) -> None:
        _engine, client = paper_engine
        for endpoint in ("/api/status", "/api/mt5/status", "/api/live/state"):
            r = client.get(endpoint)
            assert r.status_code == 200
            assert "Traceback" not in r.text
            assert 'File "' not in r.text

    def test_engine_offline_safe_payload(self, tmp_path: Path) -> None:
        app = create_app(engine_ref=None)
        client = TestClient(app)
        r = client.get("/api/mt5/status")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["reason"] == "ENGINE_OFFLINE"


class TestConnectionState:
    def test_paper_connected_state(self, paper_engine) -> None:
        engine, _client = paper_engine
        state = engine.adapter.connection_state()
        assert state.state == MT5ConnectionState.CONNECTED

    def test_forced_disconnect_state(self, offline_engine) -> None:
        engine, _client = offline_engine
        state = engine.adapter.connection_state()
        assert state.state == MT5ConnectionState.DISCONNECTED
