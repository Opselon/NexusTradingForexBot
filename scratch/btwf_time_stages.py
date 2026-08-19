"""Time compute_70d_frame stage-by-stage on a tiny slice to find the hotspot."""
import time

import polars as pl

from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(2000)

t0 = time.time()
f = compute_70d_frame(df, news_frame=None)
print(f"compute_70d_frame 2000 rows: {time.time()-t0:.1f}s rows={f.height}")

# Now time internal stages separately: replicate the inner loop with timing
from datetime import UTC, datetime

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.schema_v2 import SPREAD_USD

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

t_engine = 0.0
t_liq = 0.0
t_misc = 0.0
count = 0
for i in range(n):
    if i + 1 < 55:
        continue
    ts = times[i]
    b = raw.row(i, named=True)
    tick = TickData(symbol="XAUUSD", timestamp=ts, bid=float(b["close"]),
                    ask=float(b["close"]) + SPREAD_USD, volume=int(b.get("tick_volume", 0) or 0))
    window = all_bars[max(0, i - 54):i + 1]
    t0 = time.time()
    fv = engine.compute_from_bars(window, tick)
    t_engine += time.time() - t0
    t0 = time.time()
    liquid = compute_liquidity_features(window, decision_at=ts, mid_price=float(b["close"]), atr=fv.atr_m1)
    t_liq += time.time() - t0
    count += 1
    if count >= 100:
        break
print(f"first 100 rows: 50D engine {t_engine:.2f}s  liquidity {t_liq:.2f}s  per-row { (t_engine+t_liq)/100*1000:.1f} ms")