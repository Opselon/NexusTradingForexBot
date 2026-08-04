"""
Unit Tests - Scalp Feature Calculations
=======================================
Verifies feature vector computations and vector dimensions.
"""

from datetime import UTC, datetime

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine


def test_scalp_feature_engine_cold_start() -> None:
    """Verifies that cold start with fewer than 14 bars yields neutral feature vector."""
    engine = ScalpFeatureEngine(symbol="EURUSD")
    now = datetime.now(UTC)
    tick = TickData(symbol="EURUSD", timestamp=now, bid=1.0850, ask=1.0852)

    vec = engine.compute_from_bars(completed_bars=[], current_tick=tick)

    assert vec.symbol == "EURUSD"
    assert vec.log_return_m1 == 0.0
    assert len(vec.to_tensor_input()) == 50


def test_dynamic_z_score_calculation() -> None:
    """Verifies that z-score is calculated dynamically on incoming tick and completed bars."""
    from nexus_scalp.market_data.bar_aggregator import BarData
    import numpy as np

    engine = ScalpFeatureEngine(symbol="XAUUSD")
    now = datetime.now(UTC)

    # Generate 60 completed bars with fluctuating close prices
    bars = []
    for i in range(60):
        close_price = 2000.0 + float(i) * 0.1
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=now,
                open=close_price - 0.5,
                high=close_price + 0.8,
                low=close_price - 0.7,
                close=close_price,
                tick_volume=100,
                is_complete=True,
            )
        )

    # A live tick with price significantly higher than the rolling average
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=2015.0, ask=2015.2)

    vec = engine.compute_from_bars(completed_bars=bars, current_tick=tick)

    assert vec.cross_asset_z_score > 0.0
    # Also verify it is in the 50D tensor representation
    tensor_in = vec.to_tensor_input()
    assert len(tensor_in) == 50
    # cross_asset_z_score is at index 37 in FEATURE_NAMES (0-indexed)
    assert tensor_in[37] > 0.0
