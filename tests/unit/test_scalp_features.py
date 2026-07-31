"""
Unit Tests - Scalp Feature Calculations
=======================================
Verifies feature vector computations and vector dimensions.
"""

from datetime import datetime, timezone
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData


def test_scalp_feature_engine_cold_start() -> None:
    """Verifies that cold start with fewer than 14 bars yields neutral feature vector."""
    engine = ScalpFeatureEngine(symbol="EURUSD")
    now = datetime.now(timezone.utc)
    tick = TickData(symbol="EURUSD", timestamp=now, bid=1.0850, ask=1.0852)

    vec = engine.compute_from_bars(completed_bars=[], current_tick=tick)

    assert vec.symbol == "EURUSD"
    assert vec.log_return_m1 == 0.0
    assert len(vec.to_tensor_input()) == 40
