"""TASK-04-70D-MODEL-VALIDATION — bounded-window 70D frame build probe.

The TASK-3 `compute_70d_frame` passes `all_bars[:i+1]` (FULL history) to the
liquidity engine for every row i -> O(n^2), ~14.5 min for 20K rows (impractical
for 100K). This probe builds the 70D frame with a BOUNDED causal tail window
(h=3000 M5 bars ≈ 10 days — enough for completed D1 buckets) while keeping the
exact same semantics: all bars <= ts, forming HTF bucket excluded.

Findings are recorded for the parity builder (suggested fix upstream).
"""

from __future__ import annotations

import time

import polars as pl

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData

RAW_M5 = "data/raw/XAUUSD_M5.parquet"
HISTORY_LIMIT = 3000  # bounded causal tail (~10 days M5)


def build_bounded_70d_frame(raw: pl.DataFrame, news_frame: pl.DataFrame | None = None):
    from datetime import UTC, datetime

    from nexus_scalp.features.features70 import (
        FeatureSourceState,
        news_10d_from_context,
    )
    from nexus_scalp.model_generation.news_bridge import news_context_at

    raw = raw.sort("time")
    times: list[datetime] = []
    for row in raw.iter_rows(named=True):
        t = row.get("time_utc") or row.get("time")
        ts = t if isinstance(t, datetime) else None
        if ts is None:
            continue
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        times.append(ts)

    engine = ScalpFeatureEngine(symbol="XAUUSD")
    n = raw.height
    all_bars: list[BarData] = []
    for j in range(n):
        bj = raw.row(j, named=True)
        all_bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M5",
                timestamp=times[j],
                open=float(bj["open"]),
                high=float(bj["high"]),
                low=float(bj["low"]),
                close=float(bj["close"]),
                tick_volume=int(bj.get("tick_volume", 0) or 0),
                is_complete=True,
            )
        )
    news_enabled = news_frame is not None and not news_frame.is_empty()
    rows: list[dict] = []
    t0 = time.perf_counter()
    for i in range(n):
        if i + 1 < 55:
            continue
        ts = times[i]
        b = raw.row(i, named=True)
        tick = TickData(
            symbol="XAUUSD",
            timestamp=ts,
            bid=float(b["close"]),
            ask=float(b["close"]) + 0.5,
            volume=int(b.get("tick_volume", 0) or 0),
        )
        # bounded causal tail: last HISTORY_LIMIT bars <= ts
        window = all_bars[max(0, i - 54) : i + 1]
        hist = all_bars[max(0, i + 1 - HISTORY_LIMIT) : i + 1]
        fv = engine.compute_from_bars(window, tick)
        x50 = fv.to_tensor_input()
        liquid = compute_liquidity_features(
            hist,
            decision_at=ts,
            mid_price=float(b["close"]),
            atr=fv.atr_m1,
        )
        liq10 = list(liquid.as_vector())
        if news_enabled:
            ctx = news_context_at(news_frame, ts)
            news10 = news_10d_from_context(ctx)
            news_status = FeatureSourceState.FEATURE_AVAILABLE.value
        else:
            news10 = [0.0] * 10
            news_status = FeatureSourceState.FEATURE_DISABLED.value
        rec = {
            "timestamp": ts,
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "spread": 0.5,
            "atr_m1": float(fv.atr_m1),
            "tick_volume": int(b.get("tick_volume", 0) or 0),
            "news_status": news_status,
            "liquidity_status": FeatureSourceState.FEATURE_AVAILABLE.value,
        }
        for idx in range(50):
            rec[f"feat_{idx}"] = float(x50[idx])
        for idx in range(10):
            rec[f"feat_{50 + idx}"] = float(news10[idx])
        for idx in range(10):
            rec[f"feat_{60 + idx}"] = float(liq10[idx])
        rows.append(rec)
        if (i + 1) % 2000 == 0:
            print(f"  row {i + 1}/{n} ({time.perf_counter() - t0:.0f}s)", flush=True)
    print(f"rows: {len(rows)} in {time.perf_counter() - t0:.0f}s", flush=True)
    return pl.DataFrame(rows)


if __name__ == "__main__":
    raw = pl.read_parquet(RAW_M5)
    print("raw rows:", raw.height, flush=True)
    # bounded build on a 20K slice for timing first
    frame = build_bounded_70d_frame(raw.head(20000))
    out = "scratch/liq70d_frame_bounded_20k.parquet"
    frame.write_parquet(out)
    print("saved:", out, "rows:", frame.height, "cols:", len(frame.columns), flush=True)
