"""Paper REPLAY mode tests (P0 phase 4 — paper data integrity).

Invariants:

* Both modes are EXPLICIT and observable (SYNTHETIC default, REPLAY opt-in).
* REPLAY construction with no available historical source FAILS CLOSED
  (ReplayDataUnavailableError) — never a silent synthetic fallback.
* Replay preserves chronology (strictly non-decreasing historical timestamps)
  and serves the HISTORICAL timestamp (never wall-clock).
* Bar-record pricing: bid=close, ask=close+spread (dataset-builder convention).
* Auto SL/TP execution still processes replayed ticks (execution semantics
  are NOT bypassed).
* Synthetic CI mode remains fully available and unchanged in behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.adapters.paper.replay_source import (
    ReplayDataUnavailableError,
    ReplayTickSource,
)
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import TradeOrder


class _FakeReplaySource:
    """Minimal deterministic replay source for adapter-level tests."""

    def __init__(self, records: list[dict]) -> None:
        self._records = records
        self._cursor = 0
        self.source_mode = "REPLAY"

    def identity(self) -> dict:
        return {
            "market_data_mode": "REPLAY",
            "source": "TEST",
            "record_count": len(self._records),
            "spread_model": "RECORDED_TICK_BID_ASK",
            "slippage_model": "PAPER_ADAPTER_DETERMINISTIC",
        }

    def next_tick(self):
        if self._cursor >= len(self._records):
            return None
        r = self._records[self._cursor]
        self._cursor += 1
        return r

    def history_bars(self, timeframe: str, count: int):
        return [r for r in self._records if "high" in r][:count]


def _rec(ts: datetime, close: float) -> dict:
    return {"timestamp": ts, "bid": close, "ask": round(close + 0.12, 2), "volume": 3.0}


# --------------------------------------------------------------------------
# Mode explicitness
# --------------------------------------------------------------------------


def test_default_mode_is_synthetic_and_observable() -> None:
    a = PaperMT5Adapter(symbol="XAUUSD")
    assert a.market_data_mode == "SYNTHETIC"
    assert a.replay_provenance["market_data_mode"] == "SYNTHETIC"


def test_replay_mode_requires_replay_source() -> None:
    with pytest.raises(ValueError):
        PaperMT5Adapter(symbol="XAUUSD", replay_source=object())  # not a source


def test_replay_mode_identity_recorded() -> None:
    src = _FakeReplaySource([_rec(datetime.now(UTC), 2400.0)])
    a = PaperMT5Adapter(symbol="XAUUSD", replay_source=src)
    assert a.market_data_mode == "REPLAY"
    prov = a.replay_provenance
    assert prov["market_data_mode"] == "REPLAY"
    assert "spread_model" in prov and "slippage_model" in prov
    assert prov["record_count"] == 1


# --------------------------------------------------------------------------
# Fail-closed source loading
# --------------------------------------------------------------------------


def test_missing_dataset_fails_closed(tmp_path) -> None:
    with pytest.raises(ReplayDataUnavailableError):
        ReplayTickSource(symbol="XAUUSD", dataset_id="ds_does_not_exist")


def test_missing_raw_fallback_fails_closed() -> None:
    with pytest.raises(ReplayDataUnavailableError):
        ReplayTickSource(symbol="XAUUSD", allow_raw_fallback=False)


def test_raw_bars_source_loads_chronological_real_data() -> None:
    # BUG-266 note: data/raw is gitignored (operator-exported broker data), so
    # this is an ENVIRONMENT-GATED honesty test — it must assert on real data
    # when present and skip loudly when not, never fake a pass and never fail
    # a machine that simply has no export.
    from pathlib import Path

    from nexus_scalp.adapters.paper.replay_source import RAW_M1_BARS_PATH

    if not Path(RAW_M1_BARS_PATH).exists():
        pytest.skip(f"{RAW_M1_BARS_PATH} not exported on this host (gitignored operator data)")
    src = ReplayTickSource(symbol="XAUUSD", allow_raw_fallback=True)
    prov = src.identity()
    assert prov["market_data_mode"] == "REPLAY"
    assert prov["record_count"] > 0
    t1 = src.next_tick()
    t2 = src.next_tick()
    assert t1 is not None and t2 is not None
    assert t2["timestamp"] > t1["timestamp"], "replay chronology must be ascending"
    assert t2["ask"] > t2["bid"], "ask must exceed bid (direction-aware quote)"


# --------------------------------------------------------------------------
# Replay serving semantics
# --------------------------------------------------------------------------


def test_replay_serves_historical_timestamps_not_wall_clock() -> None:
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    src = _FakeReplaySource([_rec(t0, 2400.0), _rec(t0 + timedelta(minutes=1), 2401.0)])
    a = PaperMT5Adapter(symbol="XAUUSD", replay_source=src)
    tick1 = a.get_last_tick("XAUUSD")
    assert tick1.timestamp == t0  # historical, NOT datetime.now()
    tick2 = a.get_last_tick("XAUUSD")
    assert tick2.timestamp == t0 + timedelta(minutes=1)
    assert tick2.ask > tick2.bid


def test_replay_exhaustion_does_not_fall_back_to_synthetic() -> None:
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    src = _FakeReplaySource([_rec(t0, 2400.0)])
    a = PaperMT5Adapter(symbol="XAUUSD", replay_source=src)
    a.get_last_tick("XAUUSD")  # consumes the only record
    last = a.get_last_tick("XAUUSD")  # exhausted -> frozen on last real tick
    assert last.timestamp == t0
    assert a.market_data_mode == "REPLAY"


def test_replay_ticks_still_execute_sl_tp() -> None:
    """Execution semantics are NOT bypassed: a replayed tick that crosses an
    open position's stop must close it (broker-managed stop realism)."""
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    src = _FakeReplaySource(
        [
            _rec(t0, 2400.0),
            {"timestamp": t0 + timedelta(minutes=1), "bid": 2390.0, "ask": 2390.12, "volume": 3.0},
        ]
    )
    a = PaperMT5Adapter(symbol="XAUUSD", replay_source=src)
    a.get_last_tick("XAUUSD")
    order = TradeOrder(
        order_id="replay-test-1",
        symbol="XAUUSD",
        order_type=OrderType.BUY,
        volume=0.01,
        price=2400.0,
        stop_loss=2395.0,
        take_profit=2410.0,
        magic_number=888101,
    )
    assert a.send_order(order)
    a.get_last_tick("XAUUSD")  # second replayed tick: bid 2390 <= SL 2395
    # Auto SL/TP fired inline during get_last_tick (PAPER_SLTP path) — the
    # position must be gone and the loss realized.
    assert not a.get_positions("XAUUSD"), "SL must have auto-closed in replay"


# --------------------------------------------------------------------------
# Synthetic CI mode still works
# --------------------------------------------------------------------------


def test_synthetic_mode_unchanged_for_ci() -> None:
    a = PaperMT5Adapter(symbol="XAUUSD", initial_balance=5000.0)
    assert a.market_data_mode == "SYNTHETIC"
    a.connect()
    assert a.is_connected()
    t = a.get_last_tick("XAUUSD")
    assert t.bid > 0 and t.ask > t.bid
