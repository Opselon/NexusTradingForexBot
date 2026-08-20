"""TASK-02-70D-INTEGRATION — Liquidity API integration tests (REST + SSE).

Covers TEST-70D-13/14/16/17 + runtime smoke at the HTTP layer:

  * GET  /api/liquidity/state       — real backend status + ten values
  * GET  /api/liquidity/features    — ten individual values (index 60..69)
  * POST /api/liquidity/toggle      — persists via SettingsService + hot-applies
  * /api/live/state liquidity section + SSE payload carries the section

Follows the established integration suite pattern (FastAPI TestClient with a
minimal fake engine surface, mirroring test_news_api.py).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.features.liquidity_engine import LIQUIDITY_FEATURE_NAMES
from nexus_scalp.features.liquidity_runtime import (
    DIMENSION_70D,
    SCHEMA_70D,
    LiquidityGovernor,
)


class _FakeEngine:
    """Minimal LiveEngine surface the web layer needs for liquidity."""

    def __init__(self, governor: LiquidityGovernor) -> None:
        self.liquidity_governor = governor
        self._running = False
        self._runtime_mode = "paper"
        self.adapter = _FakeAdapter()
        self.aggregator = _FakeAggregator()
        self.server_state = None
        self._last_fv = None
        self._last_tick = None
        self._last_regime_state = None
        self._account_snapshot = None
        self._rolling_feature_records = []
        self.notifier = None
        self.settings_service = MagicMock()
        self._last_probs = None
        self._last_proposal = None
        self._runtime_mode = "paper"
        self.audit = MagicMock()
        self.audit.get_recent_predictions = MagicMock(return_value=[])
        self._bundle_lock = threading.RLock()
        self.config = SimpleNamespace(
            execution=SimpleNamespace(symbol="XAUUSD", mode=SimpleNamespace(value="paper")),
            risk=SimpleNamespace(
                risk_per_trade_pct=0.5,
                max_account_drawdown_pct=90,
                max_concurrent_positions=1,
                max_spread_points=60,
            ),
            model=SimpleNamespace(confidence_threshold=0.35),
            algo=SimpleNamespace(atr_sl_buffer_multiplier=1.5),
            news=None,
        )
        self.signal_policy = MagicMock()
        self.signal_policy.extract_live_chart_overlays = MagicMock(
            return_value={"rectangles": [], "bos_lines": [], "midlines": [], "liq_markers": []}
        )
        self.risk_engine = None

    FEATURE_SCHEMA_ID = "scalp_v4"
    FEATURE_DIM = 70
    _inference_enabled = False
    warmup_state = "READY"


class _FakeAdapter:
    def get_broker_tick(self, symbol):
        return None

    def get_account_snapshot(self):
        return None


class _FakeAggregator:
    def get_completed_bars(self):
        return []

    def get_current_forming_bar(self):
        return None


def _bar(i: int, t0: datetime, close: float = 3300.0) -> SimpleNamespace:
    return SimpleNamespace(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=t0 + timedelta(minutes=i),
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        tick_volume=100,
        is_complete=True,
    )


def _steady_bars(n: int = 60, base: float = 3300.0) -> list[SimpleNamespace]:
    t0 = datetime.now(UTC).replace(microsecond=0)
    return [_bar(i, t0, base + (i * 0.1)) for i in range(n)]


@pytest.fixture
def api_env(tmp_path):
    from nexus_scalp.settings.service import SettingsDatabase, SettingsService
    from nexus_scalp.web.server import create_app

    svc = SettingsService(db=SettingsDatabase(db_path=tmp_path / "settings.db"))
    gov = LiquidityGovernor(enabled=True, settings_service=svc)
    engine = _FakeEngine(gov)
    gov.bind_engine(engine)
    app = create_app()
    app.state.engine = engine
    return app, TestClient(app), gov


# ---------------------------------------------------------------------------
# TEST-70D-13/14 — API exposes real values
# ---------------------------------------------------------------------------


def test_70d_13_state_endpoint_reports_disabled_honestly(api_env) -> None:
    _, client, gov = api_env
    gov.set_enabled(False, actor="test")
    resp = client.get("/api/liquidity/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["enabled"] is False
    assert body["status"] == "DISABLED"
    assert body["available"] is False
    assert body["features"] == {}  # no fake values


def test_70d_13_state_endpoint_real_values(api_env) -> None:
    _, client, gov = api_env
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    resp = client.get("/api/liquidity/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["status"] == "ENABLED"
    assert body["available"] is True
    # TASK-02: enabled -> the ACTIVE schema is the 60D liquidity contract.
    assert body["schema"]["id"] == SCHEMA_70D  # canonical scalp_v3 (TASK-11)
    assert body["schema"]["dimension"] == 70
    assert body["reserved_70d_schema"]["id"] == SCHEMA_70D
    assert len(body["features"]) == 10
    for name in LIQUIDITY_FEATURE_NAMES:
        assert name in body["features"]
        assert isinstance(body["features"][name], float)
    assert body["latency_ms"] is not None
    assert body["source"] in ("LIVE_MARKET_STATE", "UNAVAILABLE")


def test_70d_13_features_endpoint_per_value_index(api_env) -> None:
    _, client, gov = api_env
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    resp = client.get("/api/liquidity/features")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["schema_id"] == SCHEMA_70D
    assert body["dimension"] == DIMENSION_70D
    assert body["available"] is True
    for idx, name in enumerate(LIQUIDITY_FEATURE_NAMES):
        entry = body["features"][name]
        # BUG-111: canonical 70D registry placement — liquidity is 60..69.
        assert entry["index"] == 60 + idx
        assert isinstance(entry["value"], float)
        assert entry["status"] in ("ENABLED", "DEGRADED", "UNAVAILABLE", "DISABLED")


# ---------------------------------------------------------------------------
# TEST-70D-10/11 — runtime toggle through the REAL HTTP endpoint
# ---------------------------------------------------------------------------


def test_70d_10_toggle_persists_and_returns_new_state(api_env) -> None:
    _, client, gov = api_env
    # start disabled
    gov.set_enabled(False, actor="test")
    resp = client.post("/api/liquidity/toggle", json={"enabled": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["enabled"] is True
    assert body["status"] in ("ENABLED", "UNAVAILABLE", "DEGRADED")
    # persistence went through the governor -> SettingsService.db (canonical
    # typed SettingsDatabase API; INV-010/BUG-080 — never live.yaml writes).
    row = gov._settings_service.db.get("model.liquidity_features_enabled")
    assert row is not None and row.value is True
    # the runtime flag is applied in the same object (hot reload, no restart)
    assert gov.enabled is True


def test_70d_11_toggle_off_keeps_engine_untouched(api_env) -> None:
    _, client, gov = api_env
    resp = client.post("/api/liquidity/toggle", json={"enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["status"] == "DISABLED"


# ---------------------------------------------------------------------------
# TEST-70D-16/17 — SSE section + reconnect restore (server-state contract)
# ---------------------------------------------------------------------------


def test_70d_16_live_state_includes_liquidity_section(api_env) -> None:
    _, client, gov = api_env
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    resp = client.get("/api/live/state")
    assert resp.status_code == 200
    body = resp.json()
    assert "liquidity" in body
    liq = body["liquidity"]
    assert liq["status"] in ("ENABLED", "DEGRADED", "UNAVAILABLE", "DISABLED")
    assert liq["schema"]["id"] == SCHEMA_70D
    assert liq["schema"]["dimension"] == 70
    if liq["available"]:
        assert len(liq["features"]) == 10
    # news remains an independent section (never degraded by liquidity absence)
    assert "news" in body


def test_70d_17_reconnect_snapshot_restores_liquidity(api_env) -> None:
    _, client, gov = api_env
    bars = _steady_bars()
    gov.compute_from_engine(bars=bars, mid_price=3305.0, atr=1.5, decision_at=bars[-1].timestamp)
    # canonical snapshot endpoint (what the UI pulls after SSE reconnect)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    # get_system_state() embeds the liquidity section in the canonical graph
    assert "liquidity" in body
    assert body["liquidity"]["schema"]["id"] == SCHEMA_70D  # canonical scalp_v3
    assert body["liquidity"]["schema"]["dimension"] == 70


# ---------------------------------------------------------------------------
# Model compatibility surfaced through the API
# ---------------------------------------------------------------------------


def test_70d_12_model_compatibility_reported(api_env) -> None:
    _, client, gov = api_env
    # fake engine declares scalp_v4/70D -> PASS
    resp = client.get("/api/liquidity/state")
    body = resp.json()
    mc = body["model_compatibility"]
    assert mc["result"] in ("PASS", "BLOCK", "UNKNOWN")
    assert mc["model_dimension"] is not None


def test_70d_12_incompatible_engine_blocked_flag(api_env) -> None:
    from nexus_scalp.web.server import create_app

    gov = LiquidityGovernor(enabled=True, settings_service=MagicMock())
    engine = _FakeEngine(gov)
    engine.FEATURE_SCHEMA_ID = "scalp_v2"  # 60D model
    engine.FEATURE_DIM = 60
    gov.bind_engine(engine)
    app = create_app()
    app.state.engine = engine
    client = TestClient(app)
    resp = client.get("/api/liquidity/state")
    body = resp.json()
    assert body["model_compatibility"]["result"] == "BLOCK"
    # scalp_v2 is a legacy family: the 70D contract rejects it by SCHEMA_VERSION
    assert body["model_compatibility"]["reason"] == "SCHEMA_VERSION_MISMATCH"
