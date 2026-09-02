"""TASK-6 forensic probe: pin exact behaviors that optimization must consider.

1. equal_high_low_strengths: does 'last_price' track true price? (No — the
   docstring says mid price via atr but the code uses newest cluster value.)
2. sweep_state distribution: is it really only 4 values on real data? Why is
   0 (NO_RELEVANT_LIQUIDITY) never emitted?
3. breakout-vs-sweep: what does the detector do when price RUNS through a BSL
   and keeps going (a true breakout)? It must NOT mark sweep.
4. confluence: what is the zone/dedup behavior on real data? 11 unique values
   suggests severe quantization of the score.
"""

import sys
import time as _t
from collections import Counter

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")

from datetime import UTC, datetime

import numpy as np
import polars as pl

from nexus_scalp.domain.models import TickData
from nexus_scalp.features import liquidity_engine as le
from nexus_scalp.features.liquidity_engine import (
    detect_confirmed_swings,
    equal_high_low_strengths,
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

N = 12000
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

# 1. EQH last_price tracking: collect pools + eqh while price moves
print("=== EQH/EQL strength last_price probe (select rows) ===")

for i in (100, 500, 1000, 2000, 5000, 9000, 11900):
    win = bars[i - 55 : i + 1]
    s = le.liquidity_atr(
        np.array([b.high for b in win]),
        np.array([b.low for b in win]),
        np.array([b.close for b in win]),
    )
    sh, sl = detect_confirmed_swings(win)
    price = win[-1].close
    e = equal_high_low_strengths(sh, sl, s)
    print(f"i={i:6d} price={price:8.2f} atr={s:6.3f} eqh={e[0]:.3f} eql={e[1]:.3f}")

# 2. sweep_state value census on real data
print("\n=== sweep_state census (rows 100..12000) ===")

cnt = Counter()

t0 = _t.perf_counter()
for i in range(55, N):
    win = bars[i - 55 : i + 1]
    ts = times[i]
    b = rows[i]
    tick = TickData(
        symbol="XAUUSD", timestamp=ts, bid=float(b["close"]), ask=float(b["close"]) + 0.2, volume=0
    )
    fv = engine.compute_from_bars(win, tick)
    liq = le.compute_liquidity_features(
        win, decision_at=ts, mid_price=float(b["close"]), atr=fv.atr_m1
    )
    cnt[liq.liquidity_sweep_state] += 1
print("sweep state census:", dict(sorted(cnt.items())))
print(f"probe time {_t.perf_counter() - t0:.1f}s")

# 3. confluence value census + zone characteristics
print("\n=== confluence census ===")
cnt2 = Counter()
for i in range(55, N):
    win = bars[i - 55 : i + 1]
    ts = times[i]
    b = rows[i]
    fv = engine.compute_from_bars(
        win,
        TickData(
            symbol="XAUUSD",
            timestamp=ts,
            bid=float(b["close"]),
            ask=float(b["close"]) + 0.2,
            volume=0,
        ),
    )
    liq = le.compute_liquidity_features(
        win, decision_at=ts, mid_price=float(b["close"]), atr=fv.atr_m1
    )
    cnt2[round(liq.liquidity_confluence, 4)] += 1
print("confluence distinct:", len(cnt2), "top:", [(k, v) for k, v in cnt2.most_common(8)])
