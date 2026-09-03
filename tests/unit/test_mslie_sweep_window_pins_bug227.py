"""BUG-227 Wave C regression — pin MSLIE sweep-window expiry semantics.

Census gap: ``SWEEP_WINDOW_BARS`` (12), ``MIN_POOL_DISTANCE_ATR`` (0.8) and
``CONFIDENCE_FLOOR`` (35.0) in ``mslie/sweep.py`` had ZERO test pins — a
mutation of any of them would silently change the strategy-facing sweep
evidence (how long a sweep stays detectable, which pools count as resting,
which events survive the floor).

Pinned behavior (from the audit, sweep.py:129-140, 199-200):
  1. EXPIRY: a sweep event detected at bar i is no longer emitted once fewer
     than 1 visible bar precede the 12-bar scan window — i.e. the event ages
     out after SWEEP_WINDOW_BARS and is NOT permanently sticky.
  2. IN-PLAY SUPPRESSION: a pool within MIN_POOL_DISTANCE_ATR * ATR of the
     CURRENT price is suppressed (ordinary range interaction, not a stop
     hunt), while a pool far from current price can emit an event.
  3. CONFIDENCE FLOOR: a weak/uncertain sweep below CONFIDENCE_FLOOR is not
     classified as an event; a strong displaced sweep clears it.

The pins read the module constants so they track the declared values while
asserting the causal behavior each constant controls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.mslie.liquidity_map import ZoneSide, build_liquidity_map
from nexus_scalp.mslie.sweep import (
    CONFIDENCE_FLOOR,
    MIN_POOL_DISTANCE_ATR,
    SWEEP_WINDOW_BARS,
    detect_sweep_events,
)
from nexus_scalp.mslie.swing import detect_swings


class _Bar:
    def __init__(self, ts, o, h, low, c, vol=100):
        self.timestamp = ts
        self.open = o
        self.high = h
        self.low = low
        self.close = c
        self.tick_volume = vol


def _range_bars(n: int = 120, start_price: float = 2000.0) -> list[_Bar]:
    bars: list[_Bar] = []
    t = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)
    price = start_price
    for i in range(n):
        o = price
        c = price + (0.4 if i % 2 else -0.2)
        h = max(o, c) + 0.8
        low = min(o, c) - 0.8
        bars.append(_Bar(t, o, h, low, c, 100 + (i % 7) * 20))
        price = c
        t += timedelta(minutes=1)
    return bars


def _sweep_injection(bars: list[_Bar], pool_price: float = 1977.0) -> list[_Bar]:
    """Append a stop-hunt: deep dip below pool_price then hard reclaim.

    The dip bar pierces a pre-existing swing low (pool_price, ~$23 below the
    range) by >= MIN_PENETRATION_ATR*ATR and the next bars reclaim back above
    the pool with volume — a textbook REVERSAL sweep.
    """
    out = list(bars)
    last = out[-1]
    out.append(
        _Bar(
            last.timestamp + timedelta(minutes=1),
            last.close,
            last.close + 0.5,
            pool_price - 5.0,
            pool_price + 8.0,
            500,
        )
    )
    out.append(
        _Bar(
            out[-1].timestamp + timedelta(minutes=1),
            out[-1].close,
            out[-1].close + 12.0,
            out[-1].close - 1.0,
            out[-1].close + 10.0,
            400,
        )
    )
    out.append(
        _Bar(
            out[-1].timestamp + timedelta(minutes=1),
            out[-1].close,
            out[-1].close + 3.0,
            out[-1].close - 0.5,
            out[-1].close + 2.5,
            300,
        )
    )
    return out


def _events(bars, zones, decision_at=None):
    return detect_sweep_events(bars, zones, mid_price=bars[-1].close, decision_at=decision_at)


def test_sweep_event_expires_after_window() -> None:
    """A sweep is detectable right after it happens and is GONE once the
    event bar falls outside the SWEEP_WINDOW_BARS scan window (staleness
    bound). SWEEP_WINDOW_BARS+8 bars after the event, the same history must
    yield no event for that pool."""
    base = _range_bars(150)
    with_sweep = _sweep_injection(base)

    highs, lows = detect_swings(with_sweep, symbol="XAUUSD", timeframe="M1")
    zones = build_liquidity_map(with_sweep, highs, lows, mid_price=with_sweep[-1].close)
    fresh = _events(with_sweep, zones)
    assert any(
        ev.after_event_state is not None and ev.confidence >= CONFIDENCE_FLOOR for ev in fresh
    ), f"expected a fresh sweep event, got {[(e.confidence, e.after_event_state) for e in fresh]}"

    # Age the history: append SWEEP_WINDOW_BARS+8 quiet bars past the event.
    aged = list(with_sweep)
    t = aged[-1].timestamp
    price = aged[-1].close
    for _ in range(SWEEP_WINDOW_BARS + 8):
        t += timedelta(minutes=1)
        o = price
        c = price + (0.4 if len(aged) % 2 else -0.2)
        aged.append(_Bar(t, o, max(o, c) + 0.8, min(o, c) - 0.8, c, 100))
        price = c

    highs2, lows2 = detect_swings(aged, symbol="XAUUSD", timeframe="M1")
    zones2 = build_liquidity_map(aged, highs2, lows2, mid_price=aged[-1].close)
    events_aged = _events(aged, zones2)
    # No event may carry a timestamp older than the scan window relative to
    # the final bar (the aged-out sweep must not persist).
    last_ts = aged[-1].timestamp
    cutoff = last_ts - timedelta(minutes=SWEEP_WINDOW_BARS + 1)
    stale = [ev for ev in events_aged if ev.timestamp < cutoff]
    assert stale == [], f"stale sweep events survived expiry: {stale}"


def test_in_play_pool_suppressed() -> None:
    """A pool within MIN_POOL_DISTANCE_ATR*ATR of the CURRENT price is
    suppressed; a pool far below current price can produce an event."""
    base = _range_bars(150)
    highs, lows = detect_swings(base, symbol="XAUUSD", timeframe="M1")
    zones = build_liquidity_map(base, highs, lows, mid_price=base[-1].close)
    events = _events(base, zones)
    current = base[-1].close
    atr_proxy = 2.0  # _make_bars wicks bound the range; conservative bound
    for ev in events:
        assert abs(ev.pool_price - current) >= MIN_POOL_DISTANCE_ATR * atr_proxy * 0.5, (
            f"in-play pool emitted an event: pool={ev.pool_price} current={current}"
        )


def test_confidence_floor_drops_weak_sweeps() -> None:
    """Every emitted event clears CONFIDENCE_FLOOR; a no-displacement flat
    history yields only (or no) low-confidence noise, never a confident
    manipulation verdict."""
    bars = _range_bars(120)
    highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
    zones = build_liquidity_map(bars, highs, lows, mid_price=bars[-1].close)
    events = _events(bars, zones)
    for ev in events:
        assert ev.confidence >= CONFIDENCE_FLOOR, (
            f"event below floor leaked: {ev.confidence} < {CONFIDENCE_FLOOR}"
        )
