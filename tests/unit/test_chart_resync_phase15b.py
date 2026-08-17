"""
Unit Tests - Chart Resync & 900-Bar Downtime Recovery (BUG-054)
===============================================================
Verifies the cold-start / reconnect resynchronization contract:

1. `BarAggregator.reseed()` atomically replaces history with broker bars,
   dedupes by timestamp, aligns the forming bar to the latest broker minute,
   and never leaves duplicate/stale bars behind.
2. `LiveEngine._resync_from_broker()` reseeds the aggregator, rebuilds a
   rolling feature record, pushes ServerState visuals, and re-evaluates
   warmup readiness.
3. `LiveEngine.sync_chart_state()` pushes a 900-bar snapshot + overlays.
4. `/api/chart/history` default window is 900 bars (BUG-054) and the broker
   fetch mirrors bars into the engine aggregator + ServerState.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.adapters.mt5.providers import RateBarSnapshot
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.domain.models import AccountInfo, SymbolInfo, TickData
from nexus_scalp.market_data.bar_aggregator import BarAggregator, BarData
from nexus_scalp.web.server import create_app

# ---------------------------------------------------------------------------
# BarAggregator.reseed()
# ---------------------------------------------------------------------------


def _bar(
    ts: datetime,
    o: float = 100.0,
    h: float = 101.0,
    l: float = 99.0,
    c: float = 100.5,
    v: int = 10,
) -> BarData:
    return BarData(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=ts,
        open=o,
        high=h,
        low=l,
        close=c,
        tick_volume=v,
        is_complete=True,
    )


def test_reseed_replaces_history_and_aligns_forming_bar() -> None:
    agg = BarAggregator(symbol="XAUUSD", timeframe_minutes=1)
    base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)

    # Build a "downtime" history ending at 07:59 (5+ hours of M1 bars).
    bars = [_bar(base + timedelta(minutes=i), c=100.0 + i * 0.01) for i in range(360)]

    last = agg.reseed(bars)

    assert last is not None
    assert last.timestamp == bars[-1].timestamp
    assert len(agg.get_completed_bars()) == 360
    # The forming bar must CONTINUE the last broker minute (same timestamp),
    # so the first live tick of that minute updates it instead of minting a
    # duplicate completed bar.
    forming = agg.get_current_forming_bar()
    assert forming is not None
    assert forming.timestamp == bars[-1].timestamp
    assert forming.is_complete is False
    assert forming.open == last.open
    assert forming.close == last.close


def test_reseed_dedupes_and_sorts() -> None:
    agg = BarAggregator(symbol="XAUUSD", timeframe_minutes=1)
    base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    # Out-of-order + duplicate timestamps must collapse to one bar each.
    dup_ts = base + timedelta(minutes=5)
    bars = [
        _bar(base + timedelta(minutes=10)),
        _bar(dup_ts),
        _bar(base + timedelta(minutes=1)),
        _bar(dup_ts),  # duplicate
        _bar(base + timedelta(minutes=7)),
    ]

    agg.reseed(bars)

    completed = agg.get_completed_bars()
    timestamps = [b.timestamp for b in completed]
    assert timestamps == sorted(timestamps)
    assert len(timestamps) == len(set(timestamps))
    assert len(completed) == 4


def test_reseed_empty_clears_state() -> None:
    agg = BarAggregator(symbol="XAUUSD", timeframe_minutes=1)
    base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    agg.reseed([_bar(base + timedelta(minutes=i)) for i in range(5)])
    assert len(agg.get_completed_bars()) == 5

    agg.reseed([])
    assert agg.get_completed_bars() == []
    assert agg.get_current_forming_bar() is None


def test_reseed_drops_incomplete_bars() -> None:
    agg = BarAggregator(symbol="XAUUSD", timeframe_minutes=1)
    base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    broken = BarData(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=base,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        tick_volume=10,
        is_complete=False,
    )
    agg.reseed([broken, _bar(base + timedelta(minutes=1))])

    completed = agg.get_completed_bars()
    assert len(completed) == 1
    assert completed[0].timestamp == base + timedelta(minutes=1)


def test_reseed_then_live_tick_continues_bar() -> None:
    """The critical downtime scenario: after reseed, the first live tick of
    the same minute must NOT create a duplicate completed bar."""
    agg = BarAggregator(symbol="XAUUSD", timeframe_minutes=1)
    base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    bars = [_bar(base + timedelta(minutes=i), c=100.0 + i * 0.01) for i in range(360)]
    agg.reseed(bars)
    assert len(agg.get_completed_bars()) == 360

    tick = TickData(
        symbol="XAUUSD",
        timestamp=bars[-1].timestamp + timedelta(seconds=30),  # same minute
        bid=100.55,
        ask=100.57,
    )
    completed_bar = agg.process_tick(tick)
    # Same minute -> no boundary crossed -> no new completed bar.
    assert completed_bar is None
    assert len(agg.get_completed_bars()) == 360

    # Next minute -> exactly ONE new completed bar (the seeded minute).
    tick2 = TickData(
        symbol="XAUUSD",
        timestamp=bars[-1].timestamp + timedelta(minutes=1, seconds=5),
        bid=101.0,
        ask=101.02,
    )
    completed_bar2 = agg.process_tick(tick2)
    assert completed_bar2 is not None
    assert completed_bar2.timestamp == bars[-1].timestamp
    assert len(agg.get_completed_bars()) == 361


# ---------------------------------------------------------------------------
# LiveEngine resync paths (real engine + fake adapter)
# ---------------------------------------------------------------------------


class FakeResyncAdapter:
    """Fake MT5 adapter whose M1 history advances to 'now' (downtime gap)."""

    def __init__(self, m1_count: int = 3500) -> None:
        self.m1_count = m1_count
        self.base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
        self.connected = True

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def get_account_info(self) -> AccountInfo:
        return AccountInfo(
            login=123456,
            trade_mode=0,
            leverage=100,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
            currency="USD",
        )

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(
            symbol=symbol,
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            stops_level=10,
            freeze_level=0,
            trade_contract_size=100.0,
        )

    def get_last_tick(self, symbol: str) -> TickData:
        return TickData(
            symbol=symbol,
            timestamp=datetime.now(UTC),
            bid=2000.0,
            ask=2000.20,
            last=2000.10,
            volume=1.0,
        )

    def get_historical_bars(
        self, symbol: str, timeframe: str = "M1", count: int = 100
    ) -> list[BarData]:
        step = timedelta(minutes=1)
        if timeframe == "H1":
            step = timedelta(hours=1)
        elif timeframe == "H4":
            step = timedelta(hours=4)
        return [
            BarData(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=self.base + step * i,
                open=2000.0 + i * 0.1,
                high=2001.0 + i * 0.1,
                low=1999.0 + i * 0.1,
                close=2000.5 + i * 0.1,
                tick_volume=100,
                is_complete=True,
            )
            for i in range(max(0, int(count)))
        ]


@pytest.fixture
def resync_engine(tmp_path):
    cfg = AppConfig()
    cfg.execution.mode = ExecutionMode.PAPER
    cfg.model.model_artifact_path = str(tmp_path / "model.pt")
    engine = LiveEngine(
        config=cfg,
        adapter=FakeResyncAdapter(),
        audit_repo=MagicMock(),
        force_fresh_model=True,
    )
    return engine


@pytest.mark.asyncio
async def test_resync_from_broker_reseeds_and_pushes_visuals(resync_engine):
    engine = resync_engine
    # Stale pre-downtime series (30 bars), then broker history arrives.
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
        for i in range(30)
    ]
    engine.aggregator.reseed(stale)
    assert len(engine.aggregator.get_completed_bars()) == 30

    # Wire a fake ServerState (as the CLI does).
    server_state = MagicMock()
    engine.server_state = server_state

    await engine._resync_from_broker("XAUUSD")

    completed = engine.aggregator.get_completed_bars()
    assert len(completed) > 30  # broker history replaces the stale series
    # Forming bar continues the newest broker minute.
    forming = engine.aggregator.get_current_forming_bar()
    assert forming is not None
    assert forming.timestamp == completed[-1].timestamp
    # A rolling feature record was appended (>=1).
    assert len(engine._rolling_feature_records) >= 1
    # ServerState received a fresh push.
    assert server_state.update_live_visuals.called


@pytest.mark.asyncio
async def test_resync_from_broker_skips_when_no_bars(resync_engine):
    engine = resync_engine
    engine.adapter = MagicMock()
    engine.adapter.get_historical_bars.return_value = []
    engine.server_state = MagicMock()

    await engine._resync_from_broker("XAUUSD")

    assert len(engine.aggregator.get_completed_bars()) == 0
    assert not engine.server_state.update_live_visuals.called


def test_sync_chart_state_pushes_900_bars(resync_engine):
    engine = resync_engine
    server_state = MagicMock()
    engine.server_state = server_state

    base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    bars = [
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
        for i in range(1200)
    ]
    engine.aggregator.reseed(bars)
    engine.sync_chart_state()
    # ServerState got the full 900-bar window (1200 available -> last 900).
    call = server_state.update_live_visuals.call_args
    assert call is not None
    bars_list = call.args[0]
    assert len(bars_list) == 900


# ---------------------------------------------------------------------------
# /api/chart/history contract: 900-bar default + resync mirror
# ---------------------------------------------------------------------------


class ChartEngineAdapter:
    def get_historical_bars(self, symbol: str, timeframe: str = "M1", count: int = 100):
        return []

    def get_rate_history(self, symbol: str, timeframe: str = "M1", count: int = 500, from_utc=None):
        base = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
        out = []
        for i in range(count):
            r = RateBarSnapshot()
            r.available = True
            r.source = "BROKER_NATIVE"
            r.time_utc = base + timedelta(minutes=i)
            r.open = 100.0 + i * 0.01
            r.high = 101.0 + i * 0.01
            r.low = 99.0 + i * 0.01
            r.close = 100.5 + i * 0.01
            r.tick_volume = 10
            out.append(r)
        return out


class _ChartEngine:
    def __init__(self) -> None:
        self.config = SimpleNamespace(execution=SimpleNamespace(symbol="XAUUSD", timeframe="M1"))
        self.aggregator = BarAggregator(symbol="XAUUSD", timeframe_minutes=1)
        self.server_state = MagicMock()
        self.adapter = ChartEngineAdapter()
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
        # Attributes read by get_system_state() (never faked values - the
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


@pytest.fixture
def chart_client():
    return TestClient(create_app(engine_ref=_ChartEngine()))


def test_chart_history_default_window_is_900(chart_client):
    body = chart_client.get("/api/chart/history").json()
    assert body["requested"] == 900
    assert body["returned"] == 900
    assert len(body["bars"]) == 900
    assert body["source"] == "MT5"


def test_chart_history_custom_count_honored(chart_client):
    body = chart_client.get("/api/chart/history?count=300").json()
    assert body["requested"] == 300
    assert len(body["bars"]) == 300


def test_chart_history_resync_mirrors_bars_into_engine(chart_client):
    """After a broker fetch the engine aggregator must be reseeded so the
    snapshot/SSE chart converges (BUG-054)."""
    engine = chart_client.app.state.engine
    assert len(engine.aggregator.get_completed_bars()) == 50  # stale pre-downtime

    body = chart_client.get("/api/chart/history").json()
    assert body["source"] == "MT5"

    completed = engine.aggregator.get_completed_bars()
    assert len(completed) == 900
    assert engine.sync_calls == 1
