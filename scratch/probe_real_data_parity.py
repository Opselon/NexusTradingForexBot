"""Real broker M5 parity probe: dataset builder vs live governor on REAL bars.

Uses a 1000-bar real slice (fast build) + the 4000-bar golden for depth.
Every selected timestamp must show exact 10D parity (brief 35).
"""
import sys, json, time; sys.path.insert(0, '.')
from datetime import datetime
from types import SimpleNamespace
import polars as pl
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
from nexus_scalp.features.liquidity_runtime import LiquidityGovernor
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine

df = pl.read_parquet(r"data/raw/XAUUSD_M5.parquet").sort("time")
# time column is datetime[us]; strip to naive then attach UTC
df = df.with_columns(
    pl.col("time").cast(pl.Datetime("us")).dt.replace_time_zone(None).alias("time_naive")
).with_columns(
    (pl.col("time_naive").cast(pl.Int64) * 1_000).alias("_us")
)
import datetime as _dt

def _utc_from_us(us: int) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(us / 1_000_000, tz=_dt.timezone.utc)
# pick a slice with plenty of history: use 1000 real bars
n = 1000
window_df = df.tail(n)
rows = []
for r in window_df.iter_rows(named=True):
    us = int(r["_us"])
    rows.append({
        "time": _utc_from_us(us), "open": float(r["open"]), "high": float(r["high"]),
        "low": float(r["low"]), "close": float(r["close"]),
        "tick_volume": int(r.get("tick_volume", 0) or 0),
    })
frame_in = pl.DataFrame(rows)
tm = time.perf_counter()
frame = compute_70d_frame(frame_in)
print("real frame built %.1fs rows=%d" % (time.perf_counter() - tm, frame.height))
last = frame.tail(1).row(0, named=True)
ds_liq = [float(last[f"feat_{i}"]) for i in range(60, 70)]

bars = [BarData(symbol="XAUUSD", timeframe="M5", timestamp=r["time"],
                open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                close=float(r["close"]), tick_volume=int(r.get("tick_volume", 0) or 0),
                is_complete=True) for r in rows]
close = bars[-1].close
tick = SimpleNamespace(timestamp=bars[-1].timestamp, bid=close, ask=close + 0.20, volume=100)
fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bars, tick)
gov = LiquidityGovernor(enabled=True)
gov.compute_from_engine(bars=bars, mid_price=float(close), atr=float(fv.atr_m1),
                        decision_at=bars[-1].timestamp)
live_liq = list(gov.last_snapshot.features)
deltas = [abs(a - b) for a, b in zip(ds_liq, live_liq)]
names = ["bsl","ssl","eqh","eql","htf","internal","external","confl","sweep","disp"]
print("real LAST-BAR parity:")
for i, (n_, d, l) in enumerate(zip(names, ds_liq, live_liq)):
    print(f"  {n_:9s} ds={d:8.5f} live={l:8.5f} delta={deltas[i]:+.8f}")
print("MAX DELTA:", max(deltas), "EXACT:", max(deltas) <= 1e-12)
print("last bar ts:", bars[-1].timestamp)

# ---- multi-timestamp sweep (brief 35: 20+ timestamps) ----
# Compare dataset-frame rows vs live governor at 25 evenly spaced row indices.
fails = []
checked = 0
ts_labels = []
for frac in [i / 25 for i in range(1, 26)]:
    row_idx = int(frac * (frame.height - 1))
    row = frame.row(row_idx, named=True)
    ts = row["timestamp"]
    ds_v = [float(row[f"feat_{i}"]) for i in range(60, 70)]
    # live-style: governor on bars[0..row_idx+54] (all history up to that row)
    sub = bars[: row_idx + 55]
    if len(sub) < 55:
        continue
    close = sub[-1].close
    tt = SimpleNamespace(timestamp=sub[-1].timestamp, bid=close, ask=close + 0.20, volume=100)
    fv2 = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(sub, tt)
    g2 = LiquidityGovernor(enabled=True)
    g2.compute_from_engine(bars=sub, mid_price=float(close), atr=float(fv2.atr_m1),
                           decision_at=sub[-1].timestamp)
    lv = list(g2.last_snapshot.features)
    md = max(abs(a - b) for a, b in zip(ds_v, lv, strict=True))
    checked += 1
    ts_labels.append(str(ts)[:19])
    if md > 1e-12:
        fails.append((row_idx, ts, md, ds_v, lv))
print(f"multi-timestamp sweep: {checked} timestamps checked, {len(fails)} mismatches")
if fails:
    for f in fails[:3]:
        print("  FAIL at", f[1], "delta", f[2])
else:
    print("ALL", checked, "TIMESTAMPS EXACT PARITY")
print("sample ts:", ts_labels[0], "...", ts_labels[-1])
