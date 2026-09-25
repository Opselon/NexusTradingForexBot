"""BUG-106 PHASE-2 optimization regression tests (2026-09-07).

Pins the two semantics-preserving optimizations of the 70D dataset builder:

1. Cursor-incremental pool evidence (IncrementalLiquidityState.pools_visible_at)
   -- per-pool monotone evidence scans with a cursor; states must be
   IDENTICAL to a full-history rescan for every decision point.
2. Prefix-stable HTF aggregation (_FastHTFState + compute_from_bars htf_lists)
   -- per-row HTF lists rebuilt from one full-history aggregation must be
   identical to canonical aggregate_bars(window) for any contiguous window.

The end-to-end builder (compute_70d_frame_fast) must stay BYTE-IDENTICAL to
the canonical compute_70d_frame on real data (existing parity tests) while
running materially faster (2.9x measured on 3k M1 rows, 2026-09-07).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from nexus_scalp.features.scalp_features import aggregate_bars
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.schema_v2_incremental import (
    IncrementalLiquidityState,
    compute_70d_frame_fast,
)

DATA_PATH = "data/raw/XAUUSD_M1.parquet"
PERIODS = (15, 30, 60, 240)


def _load_bars(n: int) -> tuple[list[BarData], list[datetime]]:
    from nexus_scalp.model_generation.bars_normalize import normalize_bars_frame

    df = pl.read_parquet(DATA_PATH).tail(n)
    df, _ = normalize_bars_frame(df)
    raw = df.sort("time")
    bars: list[BarData] = []
    times: list[datetime] = []
    for row in raw.iter_rows(named=True):
        t = row.get("time_utc") or row.get("time")
        ts = t if isinstance(t, datetime) else None
        if ts is None:
            continue
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        times.append(ts)
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                tick_volume=int(row.get("tick_volume", 0) or 0),
                is_complete=True,
            )
        )
    return bars, times


def _pool_signature(pools: list) -> list:
    return sorted(
        (round(p.price, 9), p.side.value, p.state.value, str(p.last_touched_at)) for p in pools
    )


@pytest.mark.skipif(not Path(DATA_PATH).exists(), reason="real data file absent")
def test_bug106_p2_pool_cursor_matches_full_rescan() -> None:
    """Cursor-incremental pool state == full-history rescan at every decision."""
    bars, times = _load_bars(1500)
    ls = IncrementalLiquidityState(bars)
    for i in range(600, 1450, 37):
        decision = times[i]
        incremental = ls.pools_visible_at(decision, 1.5)
        # Independent full-history rescan: fresh state over the same prefix.
        fresh = IncrementalLiquidityState(bars[: i + 1])
        canonical = fresh.pools_visible_at(decision, 1.5)
        assert _pool_signature(incremental) == _pool_signature(canonical), (
            f"pool state diverged at row {i}"
        )


@pytest.mark.skipif(not Path(DATA_PATH).exists(), reason="real data file absent")
def test_bug106_p2_htf_windows_match_canonical_aggregation() -> None:
    """_FastHTFState.window == aggregate_bars(window) for random contiguous slices."""
    from nexus_scalp.model_generation.schema_v2_incremental import _FastHTFState

    bars, _times = _load_bars(1500)
    state = _FastHTFState(bars)
    rng = random.Random(42)
    for _ in range(60):
        lo = rng.randint(0, 300)
        hi = rng.randint(lo + 100, len(bars) - 1)
        window = bars[lo : hi + 1]
        for period in PERIODS:
            canonical = aggregate_bars(window, period)
            fast = state.window(window, period)
            assert len(canonical) == len(fast), (lo, hi, period)
            for x, y in zip(canonical, fast, strict=True):
                assert (x.timestamp, x.open, x.high, x.low, x.close, x.tick_volume) == (
                    y.timestamp,
                    y.open,
                    y.high,
                    y.low,
                    y.close,
                    y.tick_volume,
                ), (lo, hi, period)


@pytest.mark.skipif(not Path(DATA_PATH).exists(), reason="real data file absent")
def test_bug106_p2_builder_output_unchanged_vs_pre_optimization_reference() -> None:
    """End-to-end builder stays byte-identical to the canonical builder."""
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

    df = pl.read_parquet(DATA_PATH).tail(600)
    canon = compute_70d_frame(df, news_frame=None)
    fast = compute_70d_frame_fast(df, news_frame=None)
    assert canon.height == fast.height
    assert canon["timestamp"].to_list() == fast["timestamp"].to_list()
    fcols = [c for c in canon.columns if c.startswith("feat_")]
    for c in fcols:
        assert canon[c].to_list() == fast[c].to_list(), f"feature column {c} diverged"


@pytest.mark.skipif(not Path(DATA_PATH).exists(), reason="real data file absent")
def test_bug106_p2_compute_from_bars_htf_lists_path_matches_canonical() -> None:
    """compute_from_bars(htf_lists=...) == compute_from_bars() without the fast path."""
    from nexus_scalp.domain.models import TickData
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.model_generation.schema_v2_incremental import _FastHTFState

    bars, times = _load_bars(900)
    state = _FastHTFState(bars)
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    i = 800
    window = bars[: i + 1]
    tick = TickData(
        symbol="XAUUSD",
        timestamp=times[i],
        bid=bars[i].close,
        ask=bars[i].close + 0.20,
        volume=0,
    )
    htf_lists = {p: state.window(window, p) for p in PERIODS}
    fv_fast = engine.compute_from_bars(window, tick, htf_lists=htf_lists)
    fv_canon = engine.compute_from_bars(window, tick)
    assert fv_fast.to_tensor_input() == fv_canon.to_tensor_input()
    assert fv_fast.atr_m1 == fv_canon.atr_m1
