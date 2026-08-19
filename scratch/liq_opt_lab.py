"""TASK-6: golden dataset freeze + optimization candidate harness.

This module is the OPTIMIZATION LAB for liquidity-v1 -> candidates. It runs
on a FIXED temporal split of the real M5 stream and produces the A/B diff
table (timestamp, feature_old, feature_new, delta) required by TASK-6 §31.

CRITICAL OOS DISCIPLINE (§6):
  - TRAIN split (first 60% of the frozen window) is used for parameter
    evidence + coarse/narrow search.
  - VALIDATION split (next 20%) for candidate selection.
  - OOS split (last 20%) is NEVER touched during search; it is evaluated
    once after freezing, and the result LOCKS the configuration.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl


def load_bars(path: str = "data/raw/XAUUSD_M5.parquet", n: int = 60000) -> list[dict[str, Any]]:
    df = pl.read_parquet(path).sort("time")
    rows = df.head(n).to_dicts()
    out = []
    for r in rows:
        t = r.get("time_utc") or r.get("time")
        ts = t if isinstance(t, datetime) else datetime.fromtimestamp(float(t), tz=UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        out.append(
            {
                "time": ts,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "tick_volume": int(r.get("tick_volume", 0) or 0),
            }
        )
    return out


def compute_vectors(
    bars: list[dict[str, Any]],
    producer: Callable,
    min_bars: int = 55,
) -> tuple[np.ndarray, list[datetime]]:
    """Runs a liquidity producer over the stream; returns (N,10) array + times.

    producer(bars_window, decision_at, mid_price, atr) -> LiquidityFeatures-like
    with .as_vector().
    """
    import sys

    sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
    from nexus_scalp.domain.models import TickData
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.market_data.bar_aggregator import BarData

    engine = ScalpFeatureEngine(symbol="XAUUSD")
    n = len(bars)
    vecs: list[list[float]] = []
    ts_out: list[datetime] = []
    bar_objs = [
        BarData(
            symbol="XAUUSD", timeframe="M5", timestamp=b["time"],
            open=b["open"], high=b["high"], low=b["low"], close=b["close"],
            tick_volume=b["tick_volume"], is_complete=True,
        )
        for b in bars
    ]
    for i in range(min_bars - 1, n):
        win = bar_objs[i - min_bars + 1 : i + 1]
        b = bars[i]
        tick = TickData(symbol="XAUUSD", timestamp=b["time"], bid=b["close"], ask=b["close"] + 0.2, volume=0)
        fv = engine.compute_from_bars(win, tick)
        liq = producer(win, decision_at=b["time"], mid_price=b["close"], atr=fv.atr_m1)
        vecs.append(list(liq.as_vector()))
        ts_out.append(b["time"])
    return np.asarray(vecs, dtype=np.float64), ts_out


def ab_diff(old: np.ndarray, new: np.ndarray) -> dict[str, Any]:
    """TASK-6 §31: per-dimension old-vs-new comparison."""
    names = [
        "bsl_distance_atr", "ssl_distance_atr", "eqh_strength", "eql_strength",
        "htf_liquidity_score", "internal_liquidity_distance",
        "external_liquidity_distance", "liquidity_confluence",
        "liquidity_sweep_state", "post_sweep_displacement",
    ]
    out = {}
    for k in range(10):
        o = old[:, k]
        n = new[:, k]
        delta = n - o
        out[names[k]] = {
            "changed_pct": float((np.abs(delta) > 1e-12).mean() * 100.0),
            "mean_delta": float(delta.mean()),
            "median_delta": float(np.median(delta)),
            "max_abs_delta": float(np.abs(delta).max()),
            "direction_changes": int((np.sign(n) != np.sign(o)).sum()),
            "old_saturation_pct": float((np.abs(o) >= 2.9999).mean() * 100.0),
            "new_saturation_pct": float((np.abs(n) >= 2.9999).mean() * 100.0),
            "old_unique": len(np.unique(np.round(o, 6))),
            "new_unique": len(np.unique(np.round(n, 6))),
        }
    return out


if __name__ == "__main__":
    bars = load_bars(n=30000)
    print("loaded", len(bars), "bars", bars[0]["time"], "->", bars[-1]["time"])
    # sanity: old producer runtime
    t0 = time.perf_counter()
    from nexus_scalp.features.liquidity_engine import compute_liquidity_features

    vec, ts = compute_vectors(bars[:3000], compute_liquidity_features)
    print(f"3000 rows in {time.perf_counter()-t0:.1f}s, shape {vec.shape}")