"""Time per-row cost at increasing indices to expose the quadratic term."""
import time
from datetime import UTC, datetime

import polars as pl

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.schema_v2 import SPREAD_USD

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(3000)
raw = df.sort("time")
times = []
for row in raw.iter_rows(named=True):
    t = row.get("time_utc") or row.get("time")
    ts = t if isinstance(t, datetime) else None
    if ts is None:
        continue
    ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    times.append(ts)

engine = ScalpFeatureEngine(symbol="XAUUSD")
n = raw.height
all_bars = []
for j in range(n):
    bj = raw.row(j, named=True)
    all_bars.append(
        BarData(symbol="XAUUSD", timeframe="M1", timestamp=times[j],
                open=float(bj["open"]), high=float(bj["high"]), low=float(bj["low"]),
                close=float(bj["close"]), tick_volume=int(bj.get("tick_volume", 0) or 0),
                is_complete=True)
    )

# measure per-row time at selected indices
probes = [100, 500, 1000, 1500, 2000, 2500, 2900]
results = {}
for i in probes:
    ts = times[i]
    b = raw.row(i, named=True)
    tick = TickData(symbol="XAUUSD", timestamp=ts, bid=float(b["close"]),
                    ask=float(b["close"]) + SPREAD_USD, volume=int(b.get("tick_volume", 0) or 0))
    window = all_bars[max(0, i - 54):i + 1]
    t0 = time.time()
    fv = engine.compute_from_bars(window, tick)
    t_eng = time.time() - t0
    t0 = time.time()
    liquid = compute_liquidity_features(window, decision_at=ts, mid_price=float(b["close"]), atr=fv.atr_m1)
    t_liq = time.time() - t0
    results[i] = {"engine_ms": round(t_eng * 1000, 1), "liq_ms": round(t_liq * 1000, 1), "win": len(window)}
    print(f"i={i}: engine={t_eng*1000:.1f}ms liq={t_liq*1000:.1f}ms window={len(window)}")

print(results)