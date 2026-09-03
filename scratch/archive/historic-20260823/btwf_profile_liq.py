"""Profile the liquidity engine per-row cost to find the hotspot."""
import cProfile
import io
import pstats
from datetime import UTC, datetime

import polars as pl

from nexus_scalp.features.liquidity_engine import (
    compute_liquidity_features,
)
from nexus_scalp.market_data.bar_aggregator import BarData

# build one 55-bar window like compute_70d_frame does
df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(56)
bars = []
for row in df.iter_rows(named=True):
    t = row["time"]
    ts = datetime.fromtimestamp(int(t), tz=UTC)
    bars.append(
        BarData(symbol="XAUUSD", timeframe="M1", timestamp=ts,
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                tick_volume=int(row.get("tick_volume", 0) or 0), is_complete=True)
    )

window = bars[-55:]
decision_at = window[-1].timestamp

pr = cProfile.Profile()
pr.enable()
for _ in range(3):
    compute_liquidity_features(window, decision_at=decision_at, mid_price=window[-1].close)
pr.disable()
s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
ps.print_stats(25)
print(s.getvalue())