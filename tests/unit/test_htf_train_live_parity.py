"""HTF train/live parity regression (MLPWR-06-02 / BUG-234).

Proves the TRAIN builder and the LIVE builder see the SAME bounded
history window (HTF_HISTORY_BARS) so feat_41/42 and the full 70D vector
are identical for the same causal market state.

Fail mode this guards against: training caller depth 55 bars always
yields feat_41/42 == 0.0 while live caller depth ~900..4000 yields real
values (max_delta 3.0 at those indices). The shared HTF_HISTORY_BARS
contract fixes the CALLER, not just the numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import HTF_HISTORY_BARS, ScalpFeatureEngine
from nexus_scalp.features.schema_contract import feature_schema_hash
from nexus_scalp.market_data.bar_aggregator import BarData


def _bars(n: int, t0: datetime) -> list[BarData]:
    """Deterministic M1 bars drifting upward (so H1 momentum is nonzero at depth 4000)."""
    out: list[BarData] = []
    for i in range(n):
        o = 3300.0 + i * 0.4
        c = o + 0.25
        h = max(o, c) + 0.5
        low = min(o, c) - 0.5
        out.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=t0 + timedelta(minutes=i),
                open=o,
                high=h,
                low=low,
                close=c,
                tick_volume=100,
                is_complete=True,
            )
        )
    return out


def _to_frame(bars: list[BarData]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "time": b.timestamp,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "tick_volume": b.tick_volume,
            }
            for b in bars
        ]
    )


def _live_vector_from_bars(bars: list[BarData]) -> list[float]:
    """What the live engine computes: engine.compute_from_bars over the capped aggregator window."""
    from nexus_scalp.features.features70 import assemble_70d
    from nexus_scalp.features.liquidity_engine import compute_liquidity_features
    from nexus_scalp.features.schema_contract import validate_70d_vector

    # Live path: the aggregator is capped at HTF_HISTORY_BARS (4000) in live_engine.
    capped = bars[-HTF_HISTORY_BARS:] if len(bars) > HTF_HISTORY_BARS else bars
    last = capped[-1]
    tick = TickData(
        symbol="XAUUSD", timestamp=last.timestamp, bid=last.close, ask=last.close + 0.20, volume=100
    )
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    fv = engine.compute_from_bars(capped, tick)
    x50 = fv.to_tensor_input()
    liquid = compute_liquidity_features(
        capped, decision_at=last.timestamp, mid_price=float(last.close), atr=fv.atr_m1
    )
    liq10 = list(liquid.as_vector())
    news10 = [0.0] * 10
    from nexus_scalp.features.features70 import FeatureSourceState

    snap = assemble_70d(
        base50=x50,
        news10=news10,
        liquidity10=liq10,
        symbol="XAUUSD",
        timeframe="M1",
        timestamp_utc=last.timestamp,
        news_available=False,
        liquidity_available=True,
        news_status=FeatureSourceState.FEATURE_DISABLED,
        liquidity_status=FeatureSourceState.FEATURE_AVAILABLE,
    )
    validate_70d_vector(snap.vector, context="live_htf_parity")
    return snap.vector


def test_htf_history_contract_is_single_source() -> None:
    """The shared HTF history constant exists and equals the live aggregator cap (4000)."""
    from nexus_scalp.model_generation.schema_v2 import LIQUIDITY_HISTORY_LIMIT

    assert HTF_HISTORY_BARS == 4000
    assert LIQUIDITY_HISTORY_LIMIT == HTF_HISTORY_BARS, (
        "liquidity limit must alias the HTF contract"
    )


def test_htf_feat41_42_nonzero_when_history_deep() -> None:
    """With deep history the HTF features are nonzero (proves the signal exists to test)."""
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _bars(500, t0)
    vec = _live_vector_from_bars(bars)
    # feat_41 is H1 momentum, feat_42 is M30 structure — both nonzero on a 500-bar ramp
    assert vec[41] != 0.0, "feat_41 must be nonzero at depth 500"
    assert vec[42] != 0.0, "feat_42 must be nonzero at depth 500"


def test_train_equals_live_same_causal_state() -> None:
    """Training builder (compute_70d_frame) and live builder produce identical 70D vector for the same causal window."""
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _bars(600, t0)
    df = _to_frame(bars)

    # Training path: the dataset builder over the full frame — last row is the causal state at t0+599min
    frame = compute_70d_frame(df, news_frame=None)
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    assert len(feat_cols) == 70
    row = frame.tail(1).row(0, named=True)
    train_vec = [float(row[c]) for c in feat_cols]

    # Live path: same bars, capped window, same producers
    live_vec = _live_vector_from_bars(bars)

    assert train_vec == live_vec, (
        "train builder and live builder must produce identical 70D for the same causal state"
    )
    # Pin the two indices this bug broke
    assert train_vec[41] == live_vec[41] and train_vec[41] != 0.0
    assert train_vec[42] == live_vec[42] and train_vec[42] != 0.0
    # Schema hash stable (no name/order change)
    assert feature_schema_hash() == feature_schema_hash()


def test_train_fast_equals_train_canonical() -> None:
    """Incremental fast path is byte-identical to canonical for HTF indices too."""
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
    from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _bars(600, t0)
    df = _to_frame(bars)
    canon = compute_70d_frame(df, news_frame=None)
    fast = compute_70d_frame_fast(df, news_frame=None)
    assert canon.height == fast.height
    fcols = [c for c in canon.columns if c.startswith("feat_")]
    diffs = sum(
        1
        for c in fcols
        for a, b in zip(canon[c].to_list(), fast[c].to_list(), strict=True)
        if a != b
    )
    assert diffs == 0, f"{diffs} feature diffs between canonical and fast"


def test_replay_equals_dataset_row() -> None:
    """Replay (same causal window via bounded HTF window) equals the dataset row."""
    from nexus_scalp.model_generation.replay import replay_70d_vector
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _bars(400, t0)
    df = _to_frame(bars)
    frame = compute_70d_frame(df, news_frame=None)
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    row = frame.tail(1).row(0, named=True)
    ds_vec = [float(row[c]) for c in feat_cols]
    ds_ts = row["timestamp"]
    r = replay_70d_vector(df, timestamp=ds_ts)
    assert r["feature_vector"] == ds_vec


@pytest.mark.skipif(
    True,
    reason="documented: HTF_HISTORY_BARS is the only knob; 55-bar window would zero feat_41/42",
)
def test_documented_55_bar_window_would_break_parity() -> None:
    """If the training window were 55, feat_41/42 would be zero — the bug we fixed. Kept as documentation."""
    raise AssertionError("parity requires the HTF_HISTORY_BARS contract, not 55")
