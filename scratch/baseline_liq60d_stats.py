"""TASK-6 bootstrap: liquidity feature baseline on REAL M5 data.

For each of the 10 liquidity dimensions (index 50..59 in scalp_liquidity_v1,
i.e. the as_vector() order), compute on the real data/raw/XAUUSD_M5.parquet
stream:
  min max mean median std p01 p05 p25 p50 p75 p95 p99
  zero_rate missing_rate(nonfinite) saturation_rate(|v|==3.0)
  unique_count
Plus per-call latency (p50/p95/max) and overall frame time.
"""

import sys
import time
from datetime import UTC, datetime

import polars as pl

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")

from nexus_scalp.domain.models import TickData
from nexus_scalp.features import liquidity_engine as le
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData

RAW = r"data/raw/XAUUSD_M5.parquet"
N_ROWS = 30000  # ~3.5 months of M5
MIN_BARS = 55
N_RUN = 2000  # rows used for the latency measurement

df = pl.read_parquet(RAW).sort("time")
print("raw rows:", df.height, "cols:", df.columns)

rows = df.to_dicts()
times: list[datetime] = []
for r in rows:
    t = r.get("time_utc") or r.get("time")
    ts = t if isinstance(t, datetime) else datetime.fromtimestamp(float(t), tz=UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    times.append(ts)

n = min(N_ROWS, df.height)
bars: list[BarData] = []
for j in range(n):
    b = rows[j]
    bars.append(
        BarData(
            symbol="XAUUSD",
            timeframe="M5",
            timestamp=times[j],
            open=float(b["open"]),
            high=float(b["high"]),
            low=float(b["low"]),
            close=float(b["close"]),
            tick_volume=int(b.get("tick_volume", 0) or 0),
            is_complete=True,
        )
    )

engine = ScalpFeatureEngine(symbol="XAUUSD")
SPREAD = 0.20

# collect vectors
vecs: list[list[float]] = []
lat: list[float] = []
t_compute = time.perf_counter()
for i in range(MIN_BARS - 1, n):
    window = bars[i - MIN_BARS + 1 : i + 1]
    ts = times[i]
    b = rows[i]
    tick = TickData(symbol="XAUUSD", timestamp=ts, bid=float(b["close"]), ask=float(b["close"]) + SPREAD, volume=0)
    fv = engine.compute_from_bars(window, tick)
    t0 = time.perf_counter()
    liquid = le.compute_liquidity_features(
        window,
        decision_at=ts,
        mid_price=float(b["close"]),
        atr=fv.atr_m1,
    )
    lat.append((time.perf_counter() - t0) * 1000.0)
    vecs.append(liquid.as_vector())
elapsed = time.perf_counter() - t_compute
print(f"computed {len(vecs)} rows in {elapsed:.1f}s ({elapsed/max(1,len(vecs))*1000:.2f} ms/row full-engine)")

import numpy as np

A = np.asarray(vecs, dtype=np.float64)
print("vector count:", A.shape, "nonfinite total:", int((~np.isfinite(A)).sum()))
names = le.LIQUIDITY_FEATURE_NAMES
hdr = f"{'feature':<28}{'min':>8}{'max':>8}{'mean':>8}{'median':>8}{'std':>8}{'p01':>8}{'p05':>8}{'p95':>8}{'p99':>8}{'zero%':>7}{'miss%':>7}{'sat%':>7}{'uniq':>7}"
print(hdr)
stats = {}
for k in range(10):
    col = A[:, k]
    fin = col[np.isfinite(col)]
    zr = float((fin == 0).mean()) * 100.0
    mr = float((~np.isfinite(col)).mean()) * 100.0
    sat = float((np.abs(fin) >= 3.0 - 1e-12).mean()) * 100.0
    uniq = len(np.unique(fin))
    p = np.percentile(fin, [1, 5, 50, 95, 99]) if len(fin) else [0] * 5
    stats[names[k]] = dict(
        n=len(fin), min=float(fin.min()), max=float(fin.max()), mean=float(fin.mean()),
        median=float(np.median(fin)),
        std=float(fin.std()), p01=float(p[0]), p05=float(p[1]), p50=float(p[2]),
        p95=float(p[3]), p99=float(p[4]), zero_rate=zr, missing_rate=mr,
        saturation_rate=sat, unique_count=uniq,
    )
    st = stats[names[k]]
    print(f"{names[k]:<28}{st['min']:>8.3f}{st['max']:>8.3f}"
          f"{st['mean']:>8.3f}{st['median']:>8.3f}{st['std']:>8.3f}"
          f"{st['p01']:>8.3f}{st['p05']:>8.3f}{st['p95']:>8.3f}"
          f"{st['p99']:>8.3f}{zr:>7.2f}{mr:>7.2f}{sat:>7.2f}{uniq:>7d}")

la = np.asarray(lat)
print(f"\nlatency (liquidity only) ms: p50={np.percentile(la, 50):.3f} p95={np.percentile(la, 95):.3f} max={la.max():.3f}")

# persist stats for the report
import json

out = {
    "source": RAW,
    "rows_computed": len(vecs),
    "temporal": [times[MIN_BARS - 1].isoformat(), times[n - 1].isoformat()],
    "latency_ms": {"p50": float(np.percentile(la, 50)), "p95": float(np.percentile(la, 95)), "max": float(la.max())},
    "per_feature": {k: {kk: (float(vv) if isinstance(vv, (int, float)) else vv) for kk, vv in stats[k].items()} for k in stats},
}
with open(r"scratch/liq60d_baseline_stats.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2)
print("\nwrote scratch/liq60d_baseline_stats.json")