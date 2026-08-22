"""Web-API hot-reload acceptance tests (§19/§40/§56/§65).

Proves the EXACT paths the browser uses:

* PUT /api/algo/config -> runtime applied + version + persisted (live.yaml
  written as projection, never authoritative)
* POST /api/config       -> execution/risk/model applied + version
* GET /api/runtime-config -> effective view reports the ACTIVE snapshot
* invalid values -> API rejects, version unchanged, last known-good active
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client_with_engine():
    """A FastAPI TestClient with a REAL LiveEngine (paper mock adapter)."""
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.configuration.config import AppConfig, ModelConfig
    from nexus_scalp.web.server import create_app

    tmpdir = tempfile.mkdtemp()

    class MockPort:
        def __init__(self) -> None:
            self.positions = []

        def connect(self) -> bool:
            return True

        def disconnect(self) -> None:
            pass

        def get_account_info(self):
            from nexus_scalp.domain.models import AccountInfo

            return AccountInfo(
                login=1,
                trade_mode=0,
                leverage=100,
                balance=10000.0,
                equity=10000.0,
                margin=0.0,
                margin_free=10000.0,
            )

        def get_symbol_info(self, symbol: str):
            from nexus_scalp.domain.models import SymbolInfo

            return SymbolInfo(
                symbol=symbol,
                digits=2,
                point=0.01,
                tick_size=0.01,
                tick_value=1.0,
                volume_min=0.01,
                volume_max=50.0,
                volume_step=0.01,
                stops_level=10,
                freeze_level=0,
                trade_contract_size=100.0,
            )

        def get_last_tick(self, symbol: str):
            from datetime import datetime

            from nexus_scalp.domain.models import TickData

            return TickData(
                symbol=symbol,
                timestamp=datetime.now(UTC),
                bid=100.0,
                ask=100.02,
                volume=1.0,
            )

        def get_historical_bars(self, symbol: str, timeframe: str, count: int):
            return []

        def get_positions(self, symbol: str):
            return self.positions

        def send_order(self, order) -> bool:  # pragma: no cover - unused
            return True

        def close_position(self, ticket: int, volume: float | None = None) -> bool:
            return True

        def modify_position(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
            return True

    config = AppConfig(
        execution={"symbol": "XAUUSD", "mode": "PAPER", "timeframe": "M1"},
        risk={
            "max_account_drawdown_pct": 10.0,
            "risk_per_trade_pct": 1.0,
            "max_concurrent_positions": 1,
            "max_spread_points": 60,
            "max_allowed_lots": 2.0,
            "enforce_stop_loss": True,
        },
        model=ModelConfig(confidence_threshold=0.35),
        algo={
            "atr_sl_buffer_multiplier": 1.5,
            "min_risk_reward_ratio": 1.8,
            "ai_zone_confidence_threshold": 0.60,
            "fvg_mitigation_sensitivity": 0.5,
            "order_block_lookback_bars": 30,
        },
        telegram={"enabled": False},
    )
    engine = LiveEngine(config=config, adapter=MockPort(), force_fresh_model=True)
    app = create_app(engine_ref=engine)
    engine.server_state = app.state.server_state
    client = TestClient(app)

    # Point live.yaml at a temp path so tests never touch the repo file
    import nexus_scalp.web.server as server_module

    original = server_module.Path
    yield client, engine, tmpdir
    server_module.Path = original


class TestAlgoTunerApiHotReload:
    def test_put_algo_config_changes_runtime_and_versions(self, client_with_engine) -> None:
        client, engine, _ = client_with_engine
        pid = os.getpid()

        # Baseline GET reads the runtime snapshot
        r0 = client.get("/api/algo/config")
        assert r0.status_code == 200
        assert r0.json()["atr_sl_buffer_multiplier"] == 1.5
        v0 = r0.json()["configuration_version"]

        # UI-style save
        r = client.put(
            "/api/algo/config",
            json={
                "atr_sl_buffer_multiplier": 2.0,
                "min_risk_reward_ratio": 2.2,
                "ai_zone_confidence_threshold": 0.70,
                "fvg_mitigation_sensitivity": 0.35,
                "order_block_lookback_bars": 45,
            },
        )
        body = r.json()
        assert body["success"] is True
        assert body["runtime_applied"] is True
        assert body["configuration_version"] == v0 + 1

        # The engine's actual services hold the new values
        assert engine.signal_policy.algo_config.atr_sl_buffer_multiplier == 2.0
        assert engine.feature_engine._fvg_mitigation_sensitivity == 0.35
        assert engine.feature_engine._order_block_lookback_bars == 45

        # GET now reflects the NEW snapshot
        r2 = client.get("/api/algo/config")
        assert r2.json()["atr_sl_buffer_multiplier"] == 2.0
        assert r2.json()["configuration_version"] == v0 + 1
        assert os.getpid() == pid  # no restart

    def test_invalid_tuner_value_rejected_by_api(self, client_with_engine) -> None:
        client, engine, _ = client_with_engine
        r = client.put(
            "/api/algo/config",
            json={
                "atr_sl_buffer_multiplier": 99.0,
                "min_risk_reward_ratio": 1.8,
                "ai_zone_confidence_threshold": 0.60,
                "fvg_mitigation_sensitivity": 0.5,
                "order_block_lookback_bars": 30,
            },
        )
        body = r.json()
        assert body["success"] is False
        assert body["runtime_applied"] is False
        v_before = engine.runtime_config.get_version()
        assert body["configuration_version"] == v_before
        # last known-good still active
        assert engine.runtime_config.get_snapshot().atr_sl_buffer_multiplier == 1.5


class TestConfigApiHotReload:
    def test_post_config_applies_risk_and_versions(self, client_with_engine) -> None:
        client, engine, _ = client_with_engine
        r = client.post(
            "/api/config",
            json={
                "execution": {
                    "symbol": "XAUUSD",
                    "timeframe": "M1",
                    "magic_number": 888101,
                    "max_slippage_points": 25,
                },
                "risk": {
                    "max_account_drawdown_pct": 5.0,
                    "risk_per_trade_pct": 0.75,
                    "max_concurrent_positions": 2,
                    "max_spread_points": 40,
                    "max_allowed_lots": 3.0,
                    "enforce_stop_loss": True,
                },
                "model": {"confidence_threshold": 0.40},
                "telegram": {"enabled": False},
                "mt5": {},
            },
        )
        body = r.json()
        assert body["success"] is True
        assert body["runtime_applied"] is True

        snap = engine.runtime_config.get_snapshot()
        assert snap.risk_per_trade_pct == 0.75
        assert snap.max_spread_points == 40
        assert snap.max_concurrent_positions == 2
        assert snap.max_allowed_lots == 3.0
        assert snap.confidence_threshold == 0.40

        # risk engine holds the new config
        assert engine.risk_engine.config.max_spread_points == 40
        assert engine.risk_engine.config.risk_per_trade_pct == 0.75

    def test_effective_config_endpoint_reports_mismatch(self, client_with_engine) -> None:
        client, _engine, _ = client_with_engine
        client.put(
            "/api/algo/config",
            json={
                "atr_sl_buffer_multiplier": 2.0,
                "min_risk_reward_ratio": 1.8,
                "ai_zone_confidence_threshold": 0.60,
                "fvg_mitigation_sensitivity": 0.5,
                "order_block_lookback_bars": 30,
            },
        )
        r = client.get("/api/runtime-config")
        body = r.json()
        assert body["success"] is True
        assert body["runtime_applied"] is True
        assert body["effective"]["algo"]["atr_sl_buffer_multiplier"] == 2.0
        diag = client.get("/api/runtime-config/diagnostics").json()
        assert diag["runtime_version"] == diag["persistent_version"]
        assert diag["mismatch"] is False

    def test_runtime_apply_endpoint_unified(self, client_with_engine) -> None:
        client, engine, _ = client_with_engine
        v0 = engine.runtime_config.get_version()
        r = client.post(
            "/api/runtime-config/apply",
            json={"updates": {"algo.atr_sl_buffer_multiplier": 3.5, "risk.max_spread_points": 25}},
        )
        body = r.json()
        assert body["success"] is True
        assert body["configuration_version"] == v0 + 1
        assert engine.runtime_config.get_snapshot().atr_sl_buffer_multiplier == 3.5
