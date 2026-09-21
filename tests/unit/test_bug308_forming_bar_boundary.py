"""BUG-308 (2026-09-21): broker boundary marked the still-forming current bar
as complete, so BarAggregator.reseed() anchored the monotonic tick clock a
full minute into the future and the out-of-order guard dropped every live
tick of the current minute.

Live symptom: last_accepted=2026-09-21T18:53:00+00:00 while tick_ts=
18:52:52 — every tick dropped from a single reseed, only NO_TRADE possible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.domain.models import TickData
from nexus_scalp.indicators.resample import is_current_bar_forming
from nexus_scalp.market_data.bar_aggregator import BarAggregator, BarData

SYMBOL = "XAUUSD"
# A wall clock strictly inside a minute, so tests never straddle a boundary.
NOW = datetime(2026, 9, 21, 18, 52, 30, tzinfo=UTC)


def _bar(ts: datetime, close: float, complete: bool = True) -> BarData:
    return BarData(
        symbol=SYMBOL,
        timeframe="M1",
        timestamp=ts,
        open=close - 1.0,
        high=close + 0.5,
        low=close - 1.5,
        close=close,
        tick_volume=10,
        is_complete=complete,
    )


def _m1_window(now: datetime, n: int = 6) -> list[BarData]:
    """Broker-shaped window ending on the still-forming current minute."""
    bars = [_bar(now.replace(second=0, microsecond=0) - timedelta(minutes=m), 2600.0 + m) for m in range(n - 1, 0, -1)]
    bars.append(_bar(now.replace(second=0, microsecond=0), 2600.0, complete=False))
    return bars


# ---------------------------------------------------------------------------
# Boundary classifier
# ---------------------------------------------------------------------------


def test_classifier_marks_current_bar_forming() -> None:
    minute = NOW.replace(second=0, microsecond=0)
    assert is_current_bar_forming(minute, "M1", now=NOW) is True


def test_classifier_marks_previous_bar_complete() -> None:
    previous = NOW.replace(second=0, microsecond=0) - timedelta(minutes=1)
    assert is_current_bar_forming(previous, "M1", now=NOW) is False


def test_classifier_minute_edge_seals_bar() -> None:
    """A tick exactly on the next boundary means the bar is sealed."""
    minute = NOW.replace(second=0, microsecond=0)
    sealed = minute + timedelta(minutes=1)
    assert is_current_bar_forming(minute, "M1", now=sealed) is False


def test_classifier_respects_timeframe_width() -> None:
    """An H4 bar stays forming for its whole 240-minute span.

    H4 buckets land on 00/04/08/12/16/20 UTC, so a bar opened at 16:00 is
    forming until the clock crosses 20:00."""
    h4_open = datetime(2026, 9, 21, 16, 0, tzinfo=UTC)
    assert is_current_bar_forming(h4_open, "H4", now=h4_open + timedelta(hours=3)) is True
    assert is_current_bar_forming(h4_open, "H4", now=h4_open + timedelta(hours=4)) is False


def test_classifier_unmapped_timeframe_is_complete() -> None:
    """Unmapped TFs (W1/MN1) fail OPEN to complete, never drop history."""
    assert is_current_bar_forming(NOW, "W1", now=NOW) is False
    assert is_current_bar_forming(NOW, "MN1", now=NOW) is False


def test_classifier_defaults_to_real_clock() -> None:
    real_minute = datetime.now(UTC).replace(second=0, microsecond=0)
    # The current real minute is forming by definition.
    assert is_current_bar_forming(real_minute, "M1") is True


# ---------------------------------------------------------------------------
# reseed() no longer anchors into the future
# ---------------------------------------------------------------------------


def test_reseed_anchor_stays_in_the_past() -> None:
    agg = BarAggregator(SYMBOL, timeframe_minutes=1)
    agg.reseed(_m1_window(NOW))
    anchor = agg._last_accepted_ts
    assert anchor is not None
    # The anchor may sit at the last completed bar's open (18:52:00) but must
    # never be the +1m future boundary (18:53:00) while the clock is in 18:52.
    assert anchor <= NOW, f"anchor {anchor.isoformat()} is in the future"


def test_reseed_with_sealed_current_bar_clamps_future_anchor() -> None:
    """The OLD defect shape: broker handed the forming minute in as complete.

    reseed() compares the anchor against the REAL wall clock, so the clamp
    only fires when the broker's last bar is genuinely near real-time (a
    fixture-time anchor in the past can never be 'future'). What must hold
    unconditionally is the invariant the clamp protects: the anchor never
    points past the last completed bar's own minute."""
    agg = BarAggregator(SYMBOL, timeframe_minutes=1)
    bars = [_bar(NOW.replace(second=0, microsecond=0) - timedelta(minutes=m), 2600.0 + m) for m in range(5, 0, -1)]
    bars.append(_bar(NOW.replace(second=0, microsecond=0), 2600.0, complete=True))
    agg.reseed(bars)
    last_completed = agg.get_completed_bars()[-1].timestamp
    # The acceptance floor is at most one timeframe past the last completed
    # bar — never beyond it.
    assert agg._last_accepted_ts <= last_completed + timedelta(minutes=1)


def test_reseed_clamps_a_real_future_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the wall clock inside the seeded minute, the anchor must clamp.

    This is the exact production shape: the last completed bar is the
    current minute and the real clock is inside it, so last+1m is in the
    future and every real tick of that minute would otherwise be dropped."""
    fixed_now = NOW
    monkeypatch.setattr(
        "nexus_scalp.market_data.bar_aggregator.datetime",
        type("FakeDT", (), {"now": staticmethod(lambda tz=None: fixed_now)}),
    )
    agg = BarAggregator(SYMBOL, timeframe_minutes=1)
    bars = [_bar(NOW.replace(second=0, microsecond=0) - timedelta(minutes=m), 2600.0 + m) for m in range(4, 0, -1)]
    bars.append(_bar(NOW.replace(second=0, microsecond=0), 2600.0, complete=True))
    agg.reseed(bars)
    assert agg._last_accepted_ts <= fixed_now
    # The forming bar's geometry is unchanged; only the floor moved.
    assert agg._current_bar_time == NOW.replace(second=0, microsecond=0) + timedelta(minutes=1)
    # And the first real tick of the forming minute is accepted.
    volume_before = agg._volume
    agg.process_tick(TickData(symbol=SYMBOL, timestamp=NOW, bid=2600.1, ask=2600.3))
    assert agg._volume == volume_before + 1


def test_first_live_tick_of_forming_minute_is_accepted() -> None:
    """The production symptom: 18:52:52 dropped against 18:53:00."""
    agg = BarAggregator(SYMBOL, timeframe_minutes=1)
    agg.reseed(_m1_window(NOW))
    tick = TickData(symbol=SYMBOL, timestamp=NOW, bid=2600.1, ask=2600.3)
    volume_before = agg._volume
    result = agg.process_tick(tick)
    # No boundary crossed, so no completed bar — but the tick must be ACCEPTED
    # (it mutated the forming bar instead of being dropped).
    assert agg._volume == volume_before + 1
    assert result is None
    assert agg._close == pytest.approx(2600.2)


def test_first_live_tick_advances_out_of_downtime() -> None:
    """After a gap, the first live tick seals the seeded forming bar.

    A tick at +2m05 crosses the seeded forming minute (18:52), so that
    minute seals once and the tick starts the 18:54 bar."""
    agg = BarAggregator(SYMBOL, timeframe_minutes=1)
    agg.reseed(_m1_window(NOW))
    forming_minute = NOW.replace(second=0, microsecond=0)
    future = forming_minute + timedelta(minutes=2, seconds=5)
    completed = agg.process_tick(TickData(symbol=SYMBOL, timestamp=future, bid=2601.0, ask=2601.2))
    assert completed is not None
    # The seeded forming minute (18:52) is the bar that seals.
    assert completed.timestamp == forming_minute
    assert agg.get_current_forming_bar() is not None
    assert agg.get_current_forming_bar().timestamp == forming_minute + timedelta(minutes=2)


def test_stale_tick_before_reseed_is_still_dropped() -> None:
    """The fix must not weaken the out-of-order guard."""
    agg = BarAggregator(SYMBOL, timeframe_minutes=1)
    agg.reseed(_m1_window(NOW))
    stale = NOW.replace(second=0, microsecond=0) - timedelta(minutes=10)
    volume_before = agg._volume
    assert agg.process_tick(TickData(symbol=SYMBOL, timestamp=stale, bid=2590.0, ask=2590.2)) is None
    assert agg._volume == volume_before


def test_seeded_history_excludes_the_forming_minute() -> None:
    """The forming minute is NOT in the completed series (no duplicate seal)."""
    agg = BarAggregator(SYMBOL, timeframe_minutes=1)
    agg.reseed(_m1_window(NOW))
    stamps = [b.timestamp for b in agg.get_completed_bars()]
    forming = NOW.replace(second=0, microsecond=0)
    assert forming not in stamps
    assert stamps == sorted(stamps)
    assert len(stamps) == len(set(stamps))
