"""
Unit Tests - Bar Aggregator
===========================
Verifies tick aggregation into OHLC bars and period boundary detection.
"""

from datetime import UTC, datetime

from nexus_scalp.domain.models import TickData
from nexus_scalp.market_data.bar_aggregator import BarAggregator


def test_bar_aggregator_single_bar_accumulation() -> None:
    """Verifies that ticks within the same minute aggregate into a single forming bar."""
    agg = BarAggregator(symbol="EURUSD", timeframe_minutes=1)

    t1 = datetime(2024, 1, 15, 10, 0, 5, tzinfo=UTC)
    t2 = datetime(2024, 1, 15, 10, 0, 25, tzinfo=UTC)

    tick1 = TickData(symbol="EURUSD", timestamp=t1, bid=1.0850, ask=1.0852)
    tick2 = TickData(symbol="EURUSD", timestamp=t2, bid=1.0855, ask=1.0857)

    res1 = agg.process_tick(tick1)
    res2 = agg.process_tick(tick2)

    assert res1 is None
    assert res2 is None


def test_bar_aggregator_bar_completion_on_boundary() -> None:
    """Verifies that a tick crossing a minute boundary triggers bar completion."""
    agg = BarAggregator(symbol="EURUSD", timeframe_minutes=1)

    t1 = datetime(2024, 1, 15, 10, 0, 5, tzinfo=UTC)
    t2 = datetime(2024, 1, 15, 10, 1, 2, tzinfo=UTC)

    tick1 = TickData(symbol="EURUSD", timestamp=t1, bid=1.0850, ask=1.0852)
    tick2 = TickData(symbol="EURUSD", timestamp=t2, bid=1.0860, ask=1.0862)

    agg.process_tick(tick1)
    completed_bar = agg.process_tick(tick2)

    assert completed_bar is not None
    assert completed_bar.is_complete
    assert completed_bar.timeframe == "M1"
    assert completed_bar.open == 1.0851
