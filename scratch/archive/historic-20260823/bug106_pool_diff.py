"""Compare pool lists between canonical and fast builder at the diff rows."""
import sys

import polars as pl

sys.path.insert(0, "src")
from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.schema_v2_incremental import (
    IncrementalLiquidityState,
)

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(400)

# build bar list identical to the frame builders
raw = df.sort("time")
times = []
for row in raw.iter_rows(named=True):
    t = row.get("time_utc") or row.get("time")
    ts = t if isinstance(t, __import__("datetime").datetime) else None
    if ts is None:
        continue
    ts = ts.replace(tzinfo=__import__("datetime").UTC) if ts.tzinfo is None else ts.astimezone(__import__("datetime").UTC)
    times.append(ts)
all_bars = []
for j in range(raw.height):
    bj = raw.row(j, named=True)
    all_bars.append(BarData(symbol="XAUUSD", timeframe="M1", timestamp=times[j],
                            open=float(bj["open"]), high=float(bj["high"]),
                            low=float(bj["low"]), close=float(bj["close"]),
                            tick_volume=int(bj.get("tick_volume", 0) or 0), is_complete=True))

# diff rows (from earlier run): 120, 206, 208
for row_i in (120, 206, 208, 210):
    ts = times[row_i]
    # canonical
    canon = compute_liquidity_features(all_bars[: row_i + 1], decision_at=ts,
                                       mid_price=float(raw.row(row_i, named=True)["close"]), atr=1.5)
    # fast
    lst = IncrementalLiquidityState(all_bars)
    vis = lst.pools_visible_at(ts, 1.5)
    print(f"row {row_i}: canon pools={len(canon.pools)} fast pools={len(vis)}")
    from collections import Counter
    print("  canon sources:", Counter(p.source.value for p in canon.pools))
    print("  fast  sources:", Counter(p.source.value for p in vis))
    # canonical confluence
    from nexus_scalp.features.liquidity_engine import liquidity_confluence
    print("  canon conf:", liquidity_confluence(canon.pools, decision_at=ts, atr=1.5))
    print("  fast  conf:", liquidity_confluence(vis, decision_at=ts, atr=1.5))