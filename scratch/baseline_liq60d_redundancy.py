"""TASK-6 forensic audit: redundancy + conditional info of liquidity 10D vs Base 50D.

On the real M5 stream (same 29,946 rows as the baseline), compute the full
60D vector per row and measure:
  1. Pearson |r| between each liquidity feature and each Base 50D feature.
  2. The max-|r| liquidity-vs-Base pair per liquidity feature.
  3. pairwise Pearson r among the 10 liquidity features (redundancy within family).
Also compute conditional stats on the sweep-state value for eqh strength.

This is baseline EVIDENCE for the optimization report — no tuning here.
"""

import sys

import numpy as np
import polars as pl

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")

from nexus_scalp.domain.models import TickData
from nexus_scalp.features import liquidity_engine as le
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Vectorized Pearson correlation (scipy-free; the venv has no scipy)."""
    a = a - a.mean()
    b = b - b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    if den == 0:
        return 0.0
    return float((a * b).sum() / den)


RAW = r"data/raw/XAUUSD_M5.parquet"
N_ROWS = 12000
MIN_BARS = 55

df = pl.read_parquet(RAW).sort("time")
rows = df.to_dicts()
times: list = []
for r in rows:
    t = r.get("time_utc") or r.get("time")
    ts = (
        t
        if isinstance(t, __import__("datetime").datetime)
        else __import__("datetime").datetime.fromtimestamp(float(t), tz=__import__("datetime").UTC)
    )
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=__import__("datetime").UTC)
    times.append(ts)

n = min(N_ROWS, df.height)
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
    for j in range(n)
]

engine = ScalpFeatureEngine(symbol="XAUUSD")
SPREAD = 0.20
vectors60: list[list[float]] = []
for i in range(MIN_BARS - 1, n):
    window = bars[i - MIN_BARS + 1 : i + 1]
    ts = times[i]
    b = rows[i]
    tick = TickData(
        symbol="XAUUSD",
        timestamp=ts,
        bid=float(b["close"]),
        ask=float(b["close"]) + SPREAD,
        volume=0,
    )
    fv = engine.compute_from_bars(window, tick)
    liq = le.compute_liquidity_features(
        window, decision_at=ts, mid_price=float(b["close"]), atr=fv.atr_m1
    )
    vectors60.append(fv.to_tensor_input() + liq.as_vector())

A = np.asarray(vectors60, dtype=np.float64)
liq_names = list(le.LIQUIDITY_FEATURE_NAMES)
base_names = [f"base_{k}" for k in range(50)]

print(f"rows={A.shape[0]} cols={A.shape[1]}")

# liquidity vs base: max abs pearson
print("\n=== liquidity vs Base50D (max |r|) ===")
top_pairs = {}
for k in range(10):
    col = A[:, 50 + k]
    best_r, best_j = 0.0, -1
    for j in range(50):
        c = A[:, j]
        r = _pearson(col, c)
        if abs(r) > abs(best_r):
            best_r, best_j = r, j
    top_pairs[k] = (best_j, best_r)
    print(f"{liq_names[k]:<30} max|r|={best_r:+.3f} with base_{best_j}")

# within-liquidity pairwise
print("\n=== liquidity pairwise Pearson ===")
for a in range(10):
    row = []
    for b in range(10):
        if a == b:
            row.append("  -- ")
        else:
            row.append(f"{_pearson(A[:, 50 + a], A[:, 50 + b]):+.2f}")
    print(f"{liq_names[a]:<28}" + " ".join(row))

# conditional: eqh strength by sweep state
print("\n=== eqh_strength | sweep_state ===")
for sv in sorted(set(A[:, 58].tolist())):
    mask = A[:, 58] == sv
    col = A[mask, 52]
    print(f"sweep_state={sv:+.0f} n={mask.sum():>7d} eqh mean={col.mean():.3f} std={col.std():.3f}")

# dependency: htf vs internal/external
print("\n=== htf vs others ===")
for other in (54, 55, 56, 57):
    print(
        f"htf({liq_names[4]}) ~ {liq_names[other - 50]}: r={_pearson(A[:, 54], A[:, other]):+.3f}"
    )

np.save(r"scratch/liq60d_vectors60_baseline.npy", A)
print("\nsaved scratch/liq60d_vectors60_baseline.npy")
