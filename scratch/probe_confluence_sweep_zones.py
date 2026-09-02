"""TASK-6 probe: confluence zone composition on REAL pools + sweep relevance distances.

Why is confluence always 2.5-3.0? Why does sweep never hit 0 on real data?
"""

import sys

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")

from datetime import UTC, datetime

import numpy as np
import polars as pl

from nexus_scalp.domain.models import TickData
from nexus_scalp.features import liquidity_engine as le
from nexus_scalp.features.liquidity_engine import PoolState
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

N = 3000
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

# Probe 30 rows: zone composition of the BEST zone

best_zones = []
dist_to_nearest = []
for i in range(55, 400, 12):  # ~30 samples
    win = bars[i - 55 : i + 1]
    ts = times[i]
    b = rows[i]
    fv = engine.compute_from_bars(
        win, TickData(symbol="XAUUSD", timestamp=ts, bid=b["close"], ask=b["close"] + 0.2, volume=0)
    )
    liq = le.compute_liquidity_features(win, decision_at=ts, mid_price=b["close"], atr=fv.atr_m1)
    price = b["close"]
    usable = liq.pools
    # nearest pool distance in ATR
    if usable:
        d = min(abs(p.price - price) for p in usable) / max(fv.atr_m1, 0.2)
        dist_to_nearest.append(d)
    # best zone: replicate confluence internals
    users = [p for p in usable if p.state != PoolState.CANDIDATE]
    if not users:
        continue
    import math

    sorted_p = sorted(users, key=lambda p: p.price)
    zones = []
    cur = [sorted_p[0]]
    cutoff = max(fv.atr_m1, 0.2) * 0.75
    for p in sorted_p[1:]:
        if p.price - cur[-1].price <= cutoff:
            cur.append(p)
        else:
            zones.append(cur)
            cur = [p]
    zones.append(cur)
    for z in zones:
        distinct = {p.source for p in z}
        tf = sum(p.timeframe_minutes for p in z)
        strength = sum(p.strength for p in z)
        score = (1 + math.log1p(len(distinct))) + (tf / 1440.0) * 0.5 + strength * 0.25
        best_zones.append((score, len(z), len(distinct), round(tf / 1440.0, 2), round(strength, 2)))

print("=== best-zone composition (score, npools, ndistinct, tf_days, strength_sum) ===")
for row in best_zones[:25]:
    print(
        f"  score={row[0]:6.3f} npools={row[1]:2d} ndistinct={row[2]} tf_days={row[3]:5.2f} str={row[4]:.2f}"
    )
dist_to_nearest = np.asarray(dist_to_nearest)
print(
    f"\nnearest-pool distance (ATR): mean={dist_to_nearest.mean():.2f} p50={np.percentile(dist_to_nearest, 50):.2f} p95={np.percentile(dist_to_nearest, 95):.2f} max={dist_to_nearest.max():.2f}"
)
print("fraction beyond 2 ATR:", (dist_to_nearest > 2.0).mean())
print("fraction beyond 6 ATR:", (dist_to_nearest > 6.0).mean())
