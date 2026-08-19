"""Empirical: are 70D liquidity values with full-history == windowed-55 values?

compute_70d_frame passes all_bars[:i+1] (growing). compute_liquidity_frame /
compute_60d_frame pass window[i-54:i+1]. If the values are identical, the
windowed call is a drop-in optimization; if not, the semantics differ and the
difference must be documented (parity finding).
"""

from datetime import UTC, datetime

import polars as pl

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.schema_v2 import SPREAD_USD

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(3000)
times = [datetime.fromtimestamp(int(row["time"]), tz=UTC) for row in df.iter_rows(named=True)]
all_bars = [
    BarData(symbol="XAUUSD", timeframe="M1", timestamp=times[j],
            open=float(df["open"][j]), high=float(df["high"][j]), low=float(df["low"][j]),
            close=float(df["close"][j]), tick_volume=1, is_complete=True)
    for j in range(df.height)
]
engine = ScalpFeatureEngine(symbol="XAUUSD")

import numpy as np

diff_rows = 0
max_diff = 0.0
n_checked = 0
for i in range(500, 1500):  # skip warm-up, sample a region
    ts = times[i]
    tick = TickData(symbol="XAUUSD", timestamp=ts, bid=float(df["close"][i]),
                    ask=float(df["close"][i]) + SPREAD_USD, volume=1)
    window = all_bars[max(0, i - 54):i + 1]
    fv = engine.compute_from_bars(window, tick)
    liq_full = compute_liquidity_features(all_bars[: i + 1], decision_at=ts,
                                          mid_price=float(df["close"][i]), atr=fv.atr_m1)
    liq_win = compute_liquidity_features(window, decision_at=ts,
                                         mid_price=float(df["close"][i]), atr=fv.atr_m1)
    vf = np.array(liq_full.as_vector())
    vw = np.array(liq_win.as_vector())
    d = np.abs(vf - vw).max()
    if d > 1e-9:
        diff_rows += 1
        max_diff = max(max_diff, d)
        if diff_rows <= 3:
            print(f"i={i} full={vf.round(4)} win={vw.round(4)} diff={d:.4f}")
    n_checked += 1
print(f"checked {n_checked} rows: differing rows={diff_rows} max_diff={max_diff:.6f}")
