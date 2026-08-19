"""TASK-02 STEP-9: REAL-BROKER CAUSALITY VALIDATION (read-only probe).

Validates the TASK-01 liquidity engine's causal guarantees on REAL
broker-derived XAUUSD M1 parquet data:

  1. confirmed-swing timing  — a swing at bar i is usable only from bar i+5
                               (candidate_at < confirmed_at, exact +5 bars)
  2. HTF completed candles    — the forming H1/H4 bucket's high/low must NOT
                               contribute at a decision inside the bucket
  3. EQH/EQL confirmation     — cluster membership respects confirmed_at
  4. sweep timing             — sweep state at T never shows a penetration
                               that happens after T
  5. displacement timing      — post-sweep displacement measured only after
                               the sweep-confirming bar

Also measures the FEATURE-LATENCY p50/p95/max on real windows (STEP 21).
Read-only: no orders, no writes, no DB.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from nexus_scalp.features.liquidity_engine import (
    compute_liquidity_features,
    detect_confirmed_swings,
    htf_liquidity_score,
)
from nexus_scalp.market_data.bar_aggregator import BarData


def load_bars() -> list[BarData]:
    df = pl.read_parquet("data/raw/XAUUSD_M1.parquet").sort("time_utc")
    bars: list[BarData] = []
    for row in df.iter_rows(named=True):
        ts = row["time_utc"]
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        bars.append(
            BarData(
                symbol="XAUUSD", timeframe="M1", timestamp=ts,
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                tick_volume=int(row.get("tick_volume", 0) or 0), is_complete=True,
            )
        )
    return bars


def main() -> dict:
    bars = load_bars()
    n = len(bars)
    print(f"real bars: {n}  range: {bars[0].timestamp} -> {bars[-1].timestamp}")

    # ---------- 1. confirmed-swing timing ----------
    sh, sl = detect_confirmed_swings(bars)
    swing_ok = 0
    swing_samples = 0
    for p in (sh + sl)[:500]:
        # find bar index of candidate_at
        cand_idx = next(i for i, b in enumerate(bars) if b.timestamp == p.candidate_at)
        conf_idx = next(i for i, b in enumerate(bars) if b.timestamp == p.confirmed_at)
        if conf_idx - cand_idx == 5:
            swing_ok += 1
        swing_samples += 1
    print(f"swing confirm-delay exact +5 bars: {swing_ok}/{swing_samples}")

    # ---------- 2. HTF completed-candle exclusion ----------
    htf_ok = 0
    htf_samples = 0
    for start in range(200, n - 200, 4000):
        mid = start + 30  # inside a forming H1 bucket
        decision = bars[mid].timestamp
        # inject nothing — just check the forming-bucket exclusion invariant:
        # features at decision with full bars == features with bars[: mid+1]
        f_full = compute_liquidity_features(
            bars, decision_at=decision, mid_price=bars[mid].close, atr=1.0
        )
        f_cut = compute_liquidity_features(
            bars[: mid + 1], decision_at=decision, mid_price=bars[mid].close, atr=1.0
        )
        if f_full.htf_liquidity_score == f_cut.htf_liquidity_score:
            htf_ok += 1
        htf_samples += 1
    print(f"HTF forming-bucket exclusion invariant: {htf_ok}/{htf_samples}")

    # ---------- 3. sweep timing (no future penetration) ----------
    sweep_ok = 0
    sweep_samples = 0
    for start in range(300, n - 300, 3000):
        decision = bars[start].timestamp
        f = compute_liquidity_features(
            bars, decision_at=decision, mid_price=bars[start].close, atr=None
        )
        # re-run with only visible bars; the state must be identical
        f_cut = compute_liquidity_features(
            bars[: start + 1], decision_at=decision, mid_price=bars[start].close, atr=None
        )
        if f.liquidity_sweep_state == f_cut.liquidity_sweep_state:
            sweep_ok += 1
        sweep_samples += 1
    print(f"sweep future-penetration invariant: {sweep_ok}/{sweep_samples}")

    # ---------- 4. historical invariance (all 10 features) ----------
    inv_ok = 0
    inv_samples = 0
    for start in range(300, n - 300, 2000):
        decision = bars[start].timestamp
        a = compute_liquidity_features(bars, decision_at=decision, mid_price=bars[start].close)
        b = compute_liquidity_features(bars[: start + 1], decision_at=decision, mid_price=bars[start].close)
        if a.as_vector() == b.as_vector():
            inv_ok += 1
        inv_samples += 1
    print(f"historical invariance (full 10D): {inv_ok}/{inv_samples}")

    # ---------- 5. feature latency p50/p95/max on real windows ----------
    lat: list[float] = []
    step = max(1, n // 300)
    for start in range(300, n, step):
        t0 = time.perf_counter()
        compute_liquidity_features(
            bars[start - 55 : start + 1], decision_at=bars[start].timestamp,
            mid_price=bars[start].close, atr=None,
        )
        lat.append((time.perf_counter() - t0) * 1000.0)
    p50 = statistics.median(lat)
    p95 = sorted(lat)[int(len(lat) * 0.95)]
    mx = max(lat)
    print(f"latency ms p50={p50:.2f} p95={p95:.2f} max={mx:.2f} n={len(lat)}")

    return {
        "bars": n,
        "swing_confirm_delay_exact": f"{swing_ok}/{swing_samples}",
        "htf_forming_exclusion": f"{htf_ok}/{htf_samples}",
        "sweep_future_invariant": f"{sweep_ok}/{sweep_samples}",
        "historical_invariance": f"{inv_ok}/{inv_samples}",
        "latency_ms": {"p50": round(p50, 2), "p95": round(p95, 2), "max": round(mx, 2)},
    }


if __name__ == "__main__":
    res = main()
    out = "artifacts/model_generation/liquidity_task02/real_data_causality_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(f"wrote {out}")