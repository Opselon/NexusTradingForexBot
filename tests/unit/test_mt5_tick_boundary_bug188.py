"""BUG-188 regression: MT5 tick history input-boundary timebase (offline).

Protects the certification-probe defect class WITHOUT a real MT5 terminal:

* UTC request boundaries are shifted +BROKER_SERVER_UTC_OFFSET_MINUTES into
  the broker timebase before copy_ticks_range (symmetric with the OUTPUT
  conversion broker_epoch_to_utc(-offset)) so callers keep UTC semantics.
* epoch-seconds / int / str inputs still normalize (normalize_utc contract).
* inverted/invalid ranges return [] (never fabricated data).
* disconnected/unavailable adapter returns [] (failure != zero-result is
  preserved by the caller-side diagnostics, covered by the live probe).
* the research contract (TickHistorySnapshot fields) matches what
  build_tick_history_snapshot produces from a probed raw row.
* mt5_tick_dataset.acquire_ticks consumes the adapter surface (stub) and
  honors its window semantics end-to-end (range containment after the fix).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from nexus_scalp.adapters.mt5 import mt5_adapter as adapter_mod
from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
from nexus_scalp.adapters.mt5.providers import (
    BROKER_SERVER_UTC_OFFSET_MINUTES,
    broker_epoch_to_utc,
    build_tick_history_snapshot,
)
from nexus_scalp.research.event_source import TickEventSource, validate_event_source
from nexus_scalp.research.mt5_tick_dataset import MT5TickDataset

OFFSET = timedelta(minutes=BROKER_SERVER_UTC_OFFSET_MINUTES)


# ---------------------------------------------------------------------------
# Harness: adapter with mocked module-level mt5 surface (no terminal needed)
# ---------------------------------------------------------------------------


class _RawRow(dict):
    """Mimics the numpy structured scalar the MT5 package returns (mapping)."""


def _raw_tick_row(epoch_server_local: int, bid: float = 3300.0, ask: float = 3300.2) -> _RawRow:
    # copy_ticks_range rows carry SERVER-LOCAL epochs ('time' seconds,
    # 'time_msc' millis) — this is the probed field contract.
    return _RawRow(
        time=epoch_server_local,
        time_msc=epoch_server_local * 1000,
        bid=bid,
        ask=ask,
        last=0.0,
        flags=0,
        volume=0.0,
    )


class _Harness:
    """DirectMT5Adapter with the native mt5 module mocked at module scope."""

    def __init__(self, captured: dict[str, Any] | None = None) -> None:
        self.captured = captured if captured is not None else {}
        self.adapter = DirectMT5Adapter(timeout=1000, retries=1)
        self.adapter._connected = True  # bypass initialize(); unit scope

    def set_range_result(self, rows: list[_RawRow]) -> None:
        captured = self.captured

        class _FakeMT5:
            COPY_TICKS_ALL = 0x4000

            @staticmethod
            def copy_ticks_range(symbol, from_dt, to_dt, flags):
                captured["call"] = {
                    "symbol": symbol,
                    "from_dt": from_dt,
                    "to_dt": to_dt,
                    "flags": flags,
                }
                return rows

        self._patcher = patch.object(adapter_mod, "mt5", _FakeMT5)
        self._patcher.start()

    def stop(self) -> None:
        self._patcher.stop()


@pytest.fixture()
def harness():
    h = _Harness()
    yield h
    h.stop()


# ---------------------------------------------------------------------------
# BUG-188: input boundary shifted into the broker timebase
# ---------------------------------------------------------------------------


def test_input_window_shifted_by_broker_offset(harness: _Harness) -> None:
    rows = [_raw_tick_row(1_000_000)]
    harness.set_range_result(rows)
    start = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    harness.adapter.get_tick_history("XAUUSD", count=100, from_utc=start, to_utc=end)
    call = harness.captured["call"]
    # the call receives the window SHIFTED +180min (broker timebase)
    assert call["from_dt"] == start + OFFSET
    assert call["to_dt"] == end + OFFSET


def test_naive_datetime_treated_as_utc_then_shifted(harness: _Harness) -> None:
    rows = [_raw_tick_row(1_000_000)]
    harness.set_range_result(rows)
    naive = datetime(2026, 9, 1, 18, 0)  # naive -> UTC per normalize_utc
    end = naive + timedelta(minutes=5)
    harness.adapter.get_tick_history("XAUUSD", count=100, from_utc=naive, to_utc=end)
    call = harness.captured["call"]
    assert call["from_dt"] == datetime(2026, 9, 1, 18, 0, tzinfo=UTC) + OFFSET
    assert call["to_dt"].tzinfo is UTC


def test_epoch_seconds_input_window_normalized(harness: _Harness) -> None:
    rows = [_raw_tick_row(1_000_000)]
    harness.set_range_result(rows)
    start = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
    start_epoch = start.timestamp()  # float epoch seconds
    end_epoch = (start + timedelta(minutes=5)).timestamp()
    harness.adapter.get_tick_history("XAUUSD", count=100, from_utc=start_epoch, to_utc=end_epoch)
    call = harness.captured["call"]
    assert call["from_dt"] == start + OFFSET
    assert call["to_dt"] == start + timedelta(minutes=5) + OFFSET


def test_shifted_boundary_cannot_invert(harness: _Harness) -> None:
    # equal boundaries shift equally (to == from is a legal 1-sided window,
    # NOT an inversion); the adapter must call through, not drop the window.
    rows = [_raw_tick_row(1_000_000)]
    harness.set_range_result(rows)
    t = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
    result = harness.adapter.get_tick_history("XAUUSD", count=10, from_utc=t, to_utc=t)
    call = harness.captured["call"]
    assert call["from_dt"] == t + OFFSET and call["to_dt"] == t + OFFSET
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Snapshot conversion: output side stays single-converted (BUG-070)
# ---------------------------------------------------------------------------


def test_snapshot_epoch_conversion_single_shift() -> None:
    # server-local epoch 12:00:00 on a GMT+3 terminal == 09:00:00 real UTC
    server_epoch = int(
        datetime(
            2026, 9, 1, 12, 0, tzinfo=UTC
        ).timestamp()  # interpreted as server-local wall clock
    )
    snap = build_tick_history_snapshot(_raw_tick_row(server_epoch))
    assert snap.time_utc == broker_epoch_to_utc(server_epoch)
    # probed research contract fields all present on the snapshot
    assert snap.bid == 3300.0 and snap.ask == 3300.2
    assert snap.time_msc == server_epoch * 1000
    assert snap.flags == 0
    assert snap.volume == 0.0


# ---------------------------------------------------------------------------
# Failure / empty semantics (offline behavior)
# ---------------------------------------------------------------------------


def test_disconnected_adapter_returns_empty_not_error() -> None:
    adapter = DirectMT5Adapter(timeout=1000, retries=1)
    adapter._connected = False
    assert adapter.get_tick_history("XAUUSD", count=10, from_utc=datetime.now(UTC)) == []


def test_invalid_range_returns_empty(harness: _Harness) -> None:
    harness.set_range_result([_raw_tick_row(1_000_000)])
    end = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
    start = end + timedelta(minutes=5)  # inverted
    assert harness.adapter.get_tick_history("XAUUSD", count=10, from_utc=start, to_utc=end) == []


def test_garbage_timestamp_returns_empty(harness: _Harness) -> None:
    harness.set_range_result([_raw_tick_row(1_000_000)])
    result = harness.adapter.get_tick_history("XAUUSD", count=10, from_utc="not-a-date")
    assert result == []


# ---------------------------------------------------------------------------
# acquire_ticks end-to-end (stub adapter, offline): containment + parity
# ---------------------------------------------------------------------------


class _StubTick:
    def __init__(self, ts: datetime, bid: float, ask: float) -> None:
        self.time_utc = ts
        self.time = int(ts.timestamp() + BROKER_SERVER_UTC_OFFSET_MINUTES * 60)
        self.time_msc = int(self.time * 1000)
        self.bid = bid
        self.ask = ask
        self.last = 0.0
        self.flags = 0
        self.volume = 3.0


class _StubAdapter:
    """Post-fix semantics: UTC request in -> ticks stamped inside the window."""

    def __init__(self, base: datetime, minutes: int) -> None:
        self._base = base
        self._minutes = minutes

    def get_tick_history(self, symbol, count=500, from_utc=None, to_utc=None):
        lo = max(0, int((from_utc - self._base).total_seconds() // 60))
        hi = min(self._minutes, int((to_utc - self._base).total_seconds() // 60) + 1)
        return [
            _StubTick(self._base + timedelta(minutes=m), 3300.0 + m * 0.01, 3300.2 + m * 0.01)
            for m in range(lo, hi)
        ]


def test_acquire_ticks_range_containment_end_to_end(tmp_path) -> None:
    base = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
    ds = MT5TickDataset(cache_root=tmp_path / "cache")
    ds_id = ds.acquire_ticks(
        _StubAdapter(base, 10),
        symbol="XAUUSD",
        start=base + timedelta(minutes=2),
        end=base + timedelta(minutes=6),
        chunk_minutes=2,
    )
    records = ds.load(ds_id)
    assert records, "acquisition returned no ticks"
    lo = base + timedelta(minutes=2)
    hi = base + timedelta(minutes=6)
    for r in records:
        ts = datetime.fromisoformat(r["timestamp"])
        assert lo <= ts <= hi, f"tick outside requested window: {ts}"
    # research event-source contract consumes the cache cleanly
    report = validate_event_source(ds.event_source(ds_id))
    assert report.ok
    assert report.out_of_order == 0


def test_research_event_source_contract_field_parity(tmp_path) -> None:
    """Live TickHistorySnapshot fields == fields the research layer consumes."""
    base = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
    snap = build_tick_history_snapshot(
        _raw_tick_row(int((base + OFFSET).timestamp()), 4365.58, 4366.06)
    )
    src = TickEventSource(
        [
            {
                "timestamp": snap.time_utc,
                "bid": snap.bid,
                "ask": snap.ask,
                "time_msc": snap.time_msc,
                "last": snap.last,
                "flags": snap.flags,
                "volume": snap.volume,
                "symbol": "XAUUSD",
            }
        ]
    )
    events = list(src.events())
    assert len(events) == 1
    ev = events[0]
    assert ev.bid == 4365.58 and ev.ask == 4366.06
    assert ev.time_msc == snap.time_msc and ev.flags == 0
    assert ev.timestamp == snap.time_utc
