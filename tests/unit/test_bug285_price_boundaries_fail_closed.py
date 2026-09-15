"""BUG-285 regression net — corrupted-price boundaries fail closed.

Wave 2026-09-14 (lane 11 L11-1/L11-2, probes VERIFIED at 0c855019):
  TickData(bid=1.0, ask=inf)   -> ACCEPTED, spread_points=inf   (RED-before)
  TickData(bid=inf, ask=inf)   -> ACCEPTED, spread_points=NaN   (RED-before)
  TickData(..., volume=inf)    -> ACCEPTED                      (RED-before)
  get_historical_bars          -> forwarded NaN/inf/inverted-OHLC rows
                                  unfiltered (validate_ohlc_bars was
                                  report-only)                    (RED-before)

Corrupted price is a MUST-FAIL-CLOSED input class per the wave charter. The
infinite-tick pins live in tests/unit/test_domain_models.py (BUG-285 block);
this file pins the BarData finiteness boundary and the broker-history reader.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
from nexus_scalp.adapters.mt5.providers import RateBarSnapshot
from nexus_scalp.market_data.bar_aggregator import BarData

_TS = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _bar_data(**kw) -> BarData:
    base = dict(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=_TS,
        open=4000.0,
        high=4001.0,
        low=3999.0,
        close=4000.5,
        tick_volume=10,
        is_complete=True,
    )
    base.update(kw)
    return BarData(**base)


# ------------------------------------------------------------------ BarData


def test_bardata_rejects_non_finite_prices() -> None:
    for field in ("open", "high", "low", "close"):
        for bad in (float("inf"), float("-inf"), float("nan")):
            # RED-before: every combination constructed silently.
            with pytest.raises(ValidationError):
                _bar_data(**{field: bad})


def test_bardata_finite_geometry_still_constructs() -> None:
    b = _bar_data()
    assert b.close == 4000.5


# ------------------------------------------------------ get_historical_bars


def _rate(ts: datetime, o, h, l, c, v=10) -> RateBarSnapshot:
    return RateBarSnapshot(
        time=int(ts.timestamp()),
        time_utc=ts,
        open=o,
        high=h,
        low=l,
        close=c,
        tick_volume=v,
    )


class _HistoryStub(DirectMT5Adapter):
    """Bypass MT5 connection: only get_rate_history is exercised by the
    mapping loop under test."""

    def __init__(self, rows: list[RateBarSnapshot]) -> None:
        self._rows = rows

    def get_rate_history(self, symbol, timeframe="M1", count=100):
        return list(self._rows)


def test_get_historical_bars_drops_malformed_rows_loudly() -> None:
    base = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    good_a = _rate(base, 4000.0, 4001.0, 3999.0, 4000.5)
    good_b = _rate(base + timedelta(minutes=1), 4000.5, 4002.0, 4000.0, 4001.5)
    rows = [
        good_a,
        _rate(base + timedelta(minutes=2), float("nan"), 4001.0, 3999.0, 4000.0),
        _rate(base + timedelta(minutes=3), 4000.0, float("inf"), 3999.0, 4000.0),
        _rate(base + timedelta(minutes=4), 4000.0, 3990.0, 3999.0, 4000.0),  # high<low
        _rate(base + timedelta(minutes=5), 0.0, 4001.0, -5.0, 4000.0),  # non-positive
        good_b,
    ]
    adapter = _HistoryStub(rows)
    bars = adapter.get_historical_bars("XAUUSD", "M1", count=len(rows))
    # RED-before: 6/6 bars returned (validation was report-only).
    assert [b.timestamp for b in bars] == [good_a.time_utc, good_b.time_utc]
    assert all(b.open > 0 and b.low <= b.high for b in bars)


def test_get_historical_bars_none_rows_still_skipped() -> None:
    rows = [
        RateBarSnapshot(time=None, time_utc=None, open=None, high=None, low=None, close=None),
        _rate(datetime(2026, 9, 14, 11, 0, tzinfo=UTC), 4000.0, 4001.0, 3999.0, 4000.5),
    ]
    adapter = _HistoryStub(rows)
    bars = adapter.get_historical_bars("XAUUSD", "M1", count=2)
    assert len(bars) == 1
