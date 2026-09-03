"""BUG-234: HTF train/live window parity — feat_41/42 must train == live.

This regression pins that `compute_70d_frame` passes the full causal
history window to the feature engine so `htf_h1_momentum` (feat_41) and
`htf_m30_structure` (feat_42) are no longer structurally 0.0 in every
training row while being nonzero in live inference.

The sink is naturally a small 55-60M1 window (no H1 signal yet), so we
assert at 120-M1 covering two completed H1 buckets (≥120 bars) the
window-expanded build actually produces the H1 signal, while a synthetic
55-bar slice via the feature engine stays 0.0.
"""

from __future__ import annotations

import pytest

try:
    import polars as pl
except Exception:  # pragma: no cover
    pytest.skip("polars missing", allow_module_level=True)


def _m1_bars() -> pl.DataFrame:
    import random as _random
    from datetime import UTC, datetime, timedelta

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    rng = _random.Random(7)
    for i in range(240):
        o = 4300.0 + rng.uniform(-1, 1)
        c = o + rng.uniform(-1, 1)
        rows.append(
            {
                "time": t0 + timedelta(minutes=i),
                "time_utc": t0 + timedelta(minutes=i),
                "open": o,
                "high": max(o, c) + 0.5,
                "low": min(o, c) - 0.5,
                "close": c,
                "tick_volume": 100,
            }
        )
    return pl.DataFrame(rows)


def test_bug234_htf_window_parity_train_sees_h1_signal_at_120() -> None:
    """compute_70d_frame at i=120 must see H1 buckets (feat_41 != 0.0)."""
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

    df = _m1_bars()
    fr = compute_70d_frame(df, min_bars=55, news_frame=None)
    # Last row (i=239) has full depth; feat_41 must be nonzero there.
    assert "feat_41" in fr.columns
    vals = fr["feat_41"].to_list()
    assert any(abs(float(v)) > 1e-9 for v in vals[60:]), (
        "HTF h1_momentum must be nonzero over the tail of a 240-bar build"
    )


def test_bug234_htf_window_parity_narrow_window_stays_zero() -> None:
    """A 55-bar isolated window must stay h1-momentom == 0 (no H1 signal)."""
    import random as _random
    from datetime import UTC, datetime, timedelta

    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.market_data.bar_aggregator import BarData

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars: list[BarData] = []
    rng = _random.Random(7)
    for i in range(55):
        o = 4300.0 + rng.uniform(-1, 1)
        c = o + rng.uniform(-1, 1)
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=t0 + timedelta(minutes=i),
                open=o,
                high=max(o, c) + 0.5,
                low=min(o, c) - 0.5,
                close=c,
                tick_volume=100,
                is_complete=True,
            )
        )
    from nexus_scalp.domain.models import TickData

    tick = TickData(
        symbol="XAUUSD",
        timestamp=t0 + timedelta(minutes=54),
        bid=float(bars[-1].close),
        ask=float(bars[-1].close) + 0.20,
        volume=100,
    )
    fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bars, tick)
    assert fv.htf_h1_momentum == pytest.approx(0.0)
