"""
Contract tests — /api/chart/history?timeframe= (chart timeframe switching)
==========================================================================

The /alt chart gained a TradingView-style timeframe switcher (2026-09-23).
Contract pinned here:

1. Omitting `timeframe` keeps the legacy behavior: the ENGINE's own
   execution timeframe is requested and echoed (backward compat with the
   legacy Web/ console's `?count=900` resync path).
2. An allowlisted foreign timeframe (`?timeframe=H4`) is passed through to
   the broker rate provider verbatim and echoed in the response — bars are
   stepped at that timeframe, never M1 bars wearing an H4 label.
3. Anything outside the allowlist is a 422 at the boundary (no silent M1
   fallback: a mislabeled bar is a lie to the operator).
4. FOREIGN-timeframe fetches NEVER touch engine state: the BUG-054 aggregator
   reseed and sync_chart_state are gated on serving the engine's own
   timeframe (an H4 reseed would corrupt the M1 feature stream), and the
   ENGINE_STATE fallback never serves engine bars under a foreign label.
5. Engine-timeframe fetches still reseed (the lag-storm guard semantics:
   bigger-than-memory only) — regression-pin of the BUG-054 mirror.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from nexus_scalp.adapters.mt5.providers import RateBarSnapshot
from nexus_scalp.market_data.bar_aggregator import BarAggregator, BarData
from nexus_scalp.web.server import create_app

_STEP_MIN = {
    "M1": 1,
    "M3": 3,
    "M5": 5,
    "M10": 10,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


class TfChartAdapter:
    """Fake rate provider that records the timeframe it was asked for and
    serves bars stepped at THAT timeframe (so tests can prove pass-through)."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def get_historical_bars(self, symbol: str, timeframe: str = "M1", count: int = 100):
        return []

    def get_rate_history(self, symbol: str, timeframe: str = "M1", count: int = 500, from_utc=None):
        self.calls.append(timeframe)
        if self.fail:
            raise RuntimeError("broker down (test)")
        step = timedelta(minutes=_STEP_MIN.get(timeframe, 1))
        base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
        out = []
        for i in range(count):
            r = RateBarSnapshot()
            r.available = True
            r.source = "BROKER_NATIVE"
            r.time_utc = base + step * i
            r.open = 100.0 + i * 0.01
            r.high = 101.0 + i * 0.01
            r.low = 99.0 + i * 0.01
            r.close = 100.5 + i * 0.01
            r.tick_volume = 10
            out.append(r)
        return out


class _TfChartEngine:
    def __init__(self, adapter: TfChartAdapter | None = None) -> None:
        self.config = SimpleNamespace(execution=SimpleNamespace(symbol="XAUUSD", timeframe="M1"))
        self.aggregator = BarAggregator(symbol="XAUUSD", timeframe_minutes=1)
        self.server_state = MagicMock()
        self.adapter = adapter or TfChartAdapter()
        base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
        stale = [
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=base + timedelta(minutes=i),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                tick_volume=10,
                is_complete=True,
            )
            for i in range(50)
        ]
        self.aggregator.reseed(stale)
        self.sync_calls = 0
        # Attributes read by get_system_state() (never faked values — the
        # engine simply has no live state yet, which is the correct contract).
        self._last_fv = None
        self._last_probs = None
        self._last_tick = None
        self._last_regime_state = None
        self._last_proposal = None
        self._running = False
        self._symbol_info = None
        self._last_inference_latency_ms = None
        self._last_experience_decision = None
        self.FEATURE_DIM = 50
        self.FEATURE_SCHEMA_ID = "scalp_v1"

    def sync_chart_state(self) -> None:
        self.sync_calls += 1


FAKE_WEB_AUTH_TOKEN = "chart-tf-test-token"


def _client(monkeypatch, adapter: TfChartAdapter | None = None) -> TestClient:
    """Authenticated TestClient (test_frontend_assets_phase14.auth_client
    pattern): pin NSE_WEB_AUTH_TOKEN BEFORE create_app so env-wins over the
    DPAPI secret store — on this box the live engine persisted a real token,
    and WEB-AUTH-P0 401s headerless probes for reasons unrelated to the
    contract under test. Auth itself is pinned by tests/unit/test_web_auth.py.
    """
    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", FAKE_WEB_AUTH_TOKEN)
    client = TestClient(create_app(engine_ref=_TfChartEngine(adapter)))
    client.headers.update({"Authorization": f"Bearer {FAKE_WEB_AUTH_TOKEN}"})
    return client


# ---------------------------------------------------------------------------
# 1/2 — default + pass-through
# ---------------------------------------------------------------------------


def test_default_timeframe_is_engine_timeframe(monkeypatch):
    client = _client(monkeypatch, )
    body = client.get("/api/chart/history?count=10").json()
    assert body["timeframe"] == "M1"
    assert client.app.state.engine.adapter.calls[-1] == "M1"
    assert body["source"] == "MT5"


def test_foreign_timeframe_passed_through_and_echoed(monkeypatch):
    client = _client(monkeypatch, )
    body = client.get("/api/chart/history?count=10&timeframe=H4").json()
    assert body["timeframe"] == "H4"
    assert client.app.state.engine.adapter.calls[-1] == "H4"
    # Bars are stepped at H4, not M1-under-an-H4-label.
    t0 = datetime.fromisoformat(body["bars"][0]["time"])
    t1 = datetime.fromisoformat(body["bars"][1]["time"])
    assert t1 - t0 == timedelta(hours=4)


def test_common_switcher_timeframes_all_accepted(monkeypatch):
    client = _client(monkeypatch, )
    for tf in ("M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1", "W1"):
        r = client.get(f"/api/chart/history?count=5&timeframe={tf}")
        assert r.status_code == 200, tf
        assert r.json()["timeframe"] == tf


# ---------------------------------------------------------------------------
# 3 — boundary validation
# ---------------------------------------------------------------------------


def test_invalid_timeframe_is_rejected_not_silently_downgraded(monkeypatch):
    client = _client(monkeypatch, )
    r = client.get("/api/chart/history?timeframe=BOGUS")
    assert r.status_code == 422
    # No silent M1 request was fired for the invalid value.
    assert client.app.state.engine.adapter.calls == []


# ---------------------------------------------------------------------------
# 4 — foreign timeframe never touches engine state
# ---------------------------------------------------------------------------


def test_foreign_timeframe_never_reseeds_engine_aggregator(monkeypatch):
    client = _client(monkeypatch, )
    engine = client.app.state.engine
    assert len(engine.aggregator.get_completed_bars()) == 50  # stale pre-downtime

    body = client.get("/api/chart/history?count=300&timeframe=H4").json()
    assert body["source"] == "MT5"
    assert body["returned"] == 300

    # The M1 aggregator must be untouched: no reseed, no ServerState push.
    assert len(engine.aggregator.get_completed_bars()) == 50
    assert engine.sync_calls == 0


def test_foreign_timeframe_broker_failure_never_falls_back_to_engine_state(monkeypatch):
    client = _client(monkeypatch, TfChartAdapter(fail=True))
    engine = client.app.state.engine
    body = client.get("/api/chart/history?timeframe=H4").json()
    assert body["source"] == "UNAVAILABLE"
    assert body["bars"] == []
    assert body["returned"] == 0
    assert body["error"] is not None and body["error"].get("code") == "MT5_RATE_HISTORY_FAILED"
    # Engine M1 bars were NOT served under an H4 label.
    assert len(engine.aggregator.get_completed_bars()) == 50


# ---------------------------------------------------------------------------
# 5 — engine timeframe still reseeds (BUG-054 regression pin)
# ---------------------------------------------------------------------------


def test_engine_timeframe_fetch_still_reseeds_aggregator(monkeypatch):
    client = _client(monkeypatch, )
    engine = client.app.state.engine
    assert len(engine.aggregator.get_completed_bars()) == 50

    body = client.get("/api/chart/history").json()  # default window, engine tf
    assert body["timeframe"] == "M1"
    assert body["source"] == "MT5"
    assert len(engine.aggregator.get_completed_bars()) == 900
    assert engine.sync_calls == 1


def test_engine_timeframe_broker_failure_still_falls_back_to_engine_state(monkeypatch):
    client = _client(monkeypatch, TfChartAdapter(fail=True))
    engine = client.app.state.engine
    body = client.get("/api/chart/history?count=20").json()
    assert body["source"] == "ENGINE_STATE"
    assert body["timeframe"] == "M1"
    assert len(engine.aggregator.get_completed_bars()) == 50
    # count window honored: last 20 completed + the forming bar
    assert 20 <= len(body["bars"]) <= 21
    # engine-state bars are M1-spaced, served under the engine's own label
    t0 = datetime.fromisoformat(body["bars"][0]["time"])
    t1 = datetime.fromisoformat(body["bars"][1]["time"])
    assert t1 - t0 == timedelta(minutes=1)
