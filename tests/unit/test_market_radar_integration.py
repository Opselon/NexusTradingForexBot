from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from nexus_scalp.web.server import create_app
from types import SimpleNamespace
from datetime import datetime, UTC


def test_market_radar_live_state_integration():
    """BUG-138 verification: Market Radar (SetupDetector) is wired into live_engine
    and surfaced through /api/live/state under the canonical 'radar' key."""
    app = create_app()
    
    class _FakeAggregator:
        def get_completed_bars(self):
            return []
        def get_current_forming_bar(self):
            return None

    class _FakeEngine:
        def __init__(self):
            self.config = SimpleNamespace(
                execution=SimpleNamespace(symbol="XAUUSD", mode=SimpleNamespace(value="LIVE")),
                risk=SimpleNamespace(risk_per_trade_pct=0.5, max_account_drawdown_pct=10.0, max_concurrent_positions=3, max_spread_points=50.0)
            )
            self._running = True
            self._last_tick = None
            self._last_regime_state = None
            self._last_fv = None
            self._last_proposal = None
            self._last_probs = None
            self.aggregator = _FakeAggregator()
            self._last_market_radar = {
                "symbol": "XAUUSD",
                "timestamp": "2026-08-26T03:15:00+00:00",
                "bar_timestamp": "2026-08-26T03:15:00+00:00",
                "regime": "HIGH_SPREAD_CHOP",
                "candidate_count": 1,
                "best_setup": {
                    "setup_id": "LIQUIDITY_SWEEP_123",
                    "setup_type": "LIQUIDITY_SWEEP",
                    "quality": 0.85,
                    "factors": {"ob_swept": 0.8},
                    "filters": {"session_ok": True},
                    "compatible_strategies": ["hunter_sweep_v1"],
                    "version": "2.0.0",
                },
                "setups": [{
                    "setup_id": "LIQUIDITY_SWEEP_123",
                    "setup_type": "LIQUIDITY_SWEEP",
                    "quality": 0.85,
                    "factors": {"ob_swept": 0.8},
                    "filters": {"session_ok": True},
                    "compatible_strategies": ["hunter_sweep_v1"],
                    "version": "2.0.0",
                }],
                "state": "SETUP_READY",
                "news_state": "HIGH_IMPACT",
                "decision_reason": "BLOCKED_BY_GUARDIAN_UNSAFE_REGIME",
                "updated_at": "2026-08-26T03:15:00+00:00",
            }

    app.state.engine = _FakeEngine()
    client = TestClient(app)
    
    r = client.get("/api/live/state")
    assert r.status_code == 200
    data = r.json()
    assert "radar" in data
    radar = data["radar"]
    assert radar is not None
    assert radar["symbol"] == "XAUUSD"
    assert radar["state"] == "SETUP_READY"
    assert radar["best_setup"]["setup_type"] == "LIQUIDITY_SWEEP"
    assert radar["candidate_count"] == 1
    print("Market Radar integration test PASSED successfully.")


def test_radar_on_new_bar_no_mslie_engine():
    """BUG-139 regression: LiveEngine._on_new_bar must run Market Radar successfully
    even when mslie_engine is None (without raising NameError on `rec`)."""
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.domain.models import TickData
    from nexus_scalp.market_data.bar_aggregator import BarData
    from nexus_scalp.configuration.config import AppConfig

    class _MockAdapter:
        pass

    config = AppConfig()
    engine = LiveEngine(config=config, adapter=_MockAdapter())
    engine.mslie_engine = None  # force None to test the fallback path

    tick = TickData(symbol="XAUUSD", bid=2400.0, ask=2400.5, timestamp=datetime.now(UTC))
    bar = BarData(symbol="XAUUSD", timeframe="M1", timestamp=datetime.now(UTC), open=2399.0, high=2402.0, low=2398.0, close=2401.0, tick_volume=100, is_complete=True)
    
    class _MockFV:
        atr_m1 = 1.0
        def to_tensor_input(self):
            return [0.0] * 50

    fv = _MockFV()
    
    # Must run without raising NameError or UnboundLocalError
    engine._on_new_bar(tick=tick, fv=fv, last_bar=bar)
    assert engine._last_market_radar is not None
    assert "candidate_count" in engine._last_market_radar
    print("BUG-139 regression test PASSED successfully.")
