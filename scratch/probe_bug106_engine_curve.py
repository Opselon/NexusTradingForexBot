"""TASK-05 — BUG-106 focused performance probe: per-row engine cost vs history size.

Isolates the O(n^2) driver: compute_liquidity_features on N-bar slices
(N = 55, 200, 1000, 2000, 4000, 8000, 20000). The full-history builder pays
this cost with N=i+1 (growing) per row; the bounded builder caps at 4000.
Times are per single call — the complexity curve is the evidence.

Output: artifacts/benchmarks/bug106_engine_curve.json
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "artifacts/benchmarks"
OUT.mkdir(parents=True, exist_ok=True)

from nexus_scalp.features.liquidity_engine import compute_liquidity_features  # noqa: E402
from nexus_scalp.market_data.bar_aggregator import BarData  # noqa: E402


def _bars(n: int, seed: int = 5) -> list[BarData]:
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    bars, price = [], 3300.0
    for i in range(n):
        o = price
        c = o + float(rng.normal(0, 0.15))
        h = max(o, c) + abs(float(rng.normal(0, 0.2)))
        l = min(o, c) - abs(float(rng.normal(0, 0.2)))
        bars.append(
            BarData(
                symbol="XAUUSD", timeframe="M5", timestamp=t0 + timedelta(minutes=i),
                open=o, high=h, low=l, close=c, tick_volume=100, is_complete=True,
            )
        )
        price = c
    return bars


def main() -> None:
    sizes = [55, 200, 1000, 2000, 4000, 8000, 20000]
    curve: dict[str, dict] = {}
    for n in sizes:
        bars = _bars(n)
        dt = 0.0
        reps = 1 if n >= 8000 else 3
        for _ in range(reps):
            t0 = time.perf_counter()
            compute_liquidity_features(bars)
            dt += time.perf_counter() - t0
        dt /= reps
        curve[str(n)] = {"seconds_per_call": round(dt, 4), "ms": round(dt * 1000, 2)}
        print(f"history={n:>6} bars | {dt*1000:8.2f} ms/call | {dt:8.4f} s", flush=True)
    report = {
        "probe": "TASK-05 BUG-106 engine complexity curve",
        "note": "per-call cost of compute_liquidity_features vs history size; "
                "full-history builder pays cost with N=i+1 per row (O(n^2)); "
                "bounded builder caps at 4000 bars",
        "live_aggregator_cap_bars": 4000,
        "curve": curve,
    }
    out_path = OUT / "bug106_engine_curve.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"saved: {out_path}")


if __name__ == "__main__":
    main()