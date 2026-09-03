"""TASK-6 §41: performance comparison v1 vs v1.1 (real M5, same rows).

Measures per-call latency (p50/p95/max) of the liquidity computation for
both engines on the identical stream, plus the 70D-ish end-to-end row cost.
"""

import json
import sys

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")

import time
from datetime import UTC, datetime

import numpy as np
import polars as pl

from nexus_scalp.domain.models import TickData
from nexus_scalp.features import liquidity_engine as le
from nexus_scalp.features.liquidity_engine_opt import (
    LiquidityParams,
    compute_liquidity_features_v1_1,
)
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").sort("time")
rows = df.to_dicts()
times = []
for r in rows:
    t = r.get("time_utc") or r.get("time")
    ts = t if isinstance(t, datetime) else datetime.fromtimestamp(float(t), tz=UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    times.append(ts)

N = 2500
bars = [
    BarData(
        symbol="XAUUSD",
        timeframe="M5",
        timestamp=times[j],
        open=float(rows[j]["open"]),
        high=float(rows[j]["high"]),
        low=float(rows[j]["low"]),
        close=float(rows[j]["close"]),
        tick_volume=int(rows[j].get("tick_volume", 0) or 0),
        is_complete=True,
    )
    for j in range(N)
]
engine = ScalpFeatureEngine(symbol="XAUUSD")


def measure(fn, label):
    lat = []
    for i in range(55, N):
        win = bars[i - 55 + 1 : i + 1]
        b = rows[i]
        fv = engine.compute_from_bars(
            win,
            TickData(
                symbol="XAUUSD", timestamp=times[i], bid=b["close"], ask=b["close"] + 0.2, volume=0
            ),
        )
        t0 = time.perf_counter()
        fn(win, decision_at=times[i], mid_price=b["close"], atr=fv.atr_m1)
        lat.append((time.perf_counter() - t0) * 1000.0)
    la = np.asarray(lat)
    print(
        f"{label:<10} p50={np.percentile(la, 50):.3f} ms  p95={np.percentile(la, 95):.3f} ms  max={la.max():.3f} ms  (n={len(la)})"
    )
    return float(np.percentile(la, 50)), float(np.percentile(la, 95)), float(la.max())


print("== per-call latency (liquidity block only) ==")
r1 = measure(le.compute_liquidity_features, "v1")
r2 = measure(
    lambda w, **kw: compute_liquidity_features_v1_1(w, **kw, params=LiquidityParams()), "v1.1"
)
print(f"\nlatency ratio v1.1/v1: p50 {r2[0] / r1[0]:.2f}x")


with open("scratch/liq_perf_comparison.json", "w", encoding="utf-8") as fh:
    json.dump(
        {
            "v1": {"p50": r1[0], "p95": r1[1], "max": r1[2]},
            "v1_1": {"p50": r2[0], "p95": r2[1], "max": r2[2]},
        },
        fh,
        indent=2,
    )
print("wrote scratch/liq_perf_comparison.json")
