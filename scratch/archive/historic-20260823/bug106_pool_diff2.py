"""Compare session/daily pools between canonical and fast (BUG-106)."""
import sys
from collections import Counter
from datetime import UTC, datetime

import polars as pl

sys.path.insert(0, "src")
from nexus_scalp.features.liquidity_engine import (
    daily_price_pools,
    session_high_low_pools,
)
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.schema_v2_incremental import (
    IncrementalLiquidityState,
    _session_ranges,
)

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(400)
raw = df.sort("time")
times = []
for row in raw.iter_rows(named=True):
    t = row.get("time_utc") or row.get("time")
    ts = t if isinstance(t, datetime) else None
    if ts is None:
        continue
    ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    times.append(ts)
all_bars = []
for j in range(raw.height):
    bj = raw.row(j, named=True)
    all_bars.append(BarData(symbol="XAUUSD", timeframe="M1", timestamp=times[j],
                            open=float(bj["open"]), high=float(bj["high"]),
                            low=float(bj["low"]), close=float(bj["close"]),
                            tick_volume=int(bj.get("tick_volume", 0) or 0), is_complete=True))

for row_i in (60, 120, 206, 208):
    ts = times[row_i]
    lst = IncrementalLiquidityState(all_bars)
    print(f"--- row {row_i} decision={ts} hour={ts.hour} ---")
    print("  session range:", _session_ranges(ts))
    canon_s = session_high_low_pools(all_bars[: row_i + 1], now=ts)
    fast_s = lst.session_pools_at(ts)
    print("  canon session pools:", len(canon_s), "fast:", len(fast_s))
    for label, ps in (("canon", canon_s), ("fast", fast_s)):
        if ps:
            print(f"   {label} s0: price={ps[0].price} confirmed={ps[0].confirmed_at} cand={ps[0].candidate_at}")
    canon_d = daily_price_pools(all_bars[: row_i + 1], now=ts)
    fast_d = lst.daily_pools_at(ts)
    print("  canon daily pools:", len(canon_d), "fast:", len(fast_d), Counter(p.source.value for p in canon_d), Counter(p.source.value for p in fast_d))