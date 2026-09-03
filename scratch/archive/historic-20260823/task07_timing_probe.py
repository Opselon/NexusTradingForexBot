"""Timing probe: where does task07_consolidated_analysis spend its time?"""
from __future__ import annotations

import sys
import time
from datetime import UTC
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(REPO / "src"))

import polars as pl  # noqa: E402

from nexus_scalp.features.liquidity_engine import (  # noqa: E402
    compute_liquidity_features,
    liquidity_atr,
)
from nexus_scalp.market_data.bar_aggregator import BarData  # noqa: E402

df = pl.read_parquet(REPO / "data" / "raw" / "XAUUSD_M5.parquet").sort("time")
bars: list[BarData] = []
for row in df.iter_rows(named=True):
    t = row["time_utc"]
    ts = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
    bars.append(BarData(symbol="XAUUSD", timeframe="M5", timestamp=ts,
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        tick_volume=int(row["tick_volume"] or 0), is_complete=True))
print(f"bars={len(bars)}")

for i in [1000, 10000, 50000, 99000]:
    t0 = time.time()
    window = bars[: i + 1]
    t1 = time.time()
    highs = [b.high for b in window]
    t2 = time.time()
    atr = float(liquidity_atr(highs, [b.low for b in window], [b.close for b in window]))
    t3 = time.time()
    feats = compute_liquidity_features(window, decision_at=window[-1].timestamp, atr=atr)
    t4 = time.time()
    print(f"i={i}: slice={t1-t0:.3f}s lists={t2-t1:.3f}s atr={t3-t2:.3f}s engine={t4-t3:.3f}s total={t4-t0:.3f}s")