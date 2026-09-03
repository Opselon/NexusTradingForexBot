"""
Forensic 50D — Determinism (x100) + Causality (T-1/T/T+1) probe
================================================================
1. Determinism: same raw bars + tick + config -> identical 50D vector,
   100 consecutive runs (Phase 28).
2. Causality: changing ONLY the future bar (T+1) must not change features
   computed at T (Phase 29). Also T-1 independence (adding history before
   the window start must not change features at T).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

import numpy as np

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src")

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData


def make_bars(
    closes: list[float],
    *,
    ohlc: list[tuple[float, float, float, float]] | None = None,
    volumes: list[int] | None = None,
    start: datetime | None = None,
) -> list[BarData]:
    start = start or datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
    bars = []
    for i, c in enumerate(closes):
        if ohlc is not None and i < len(ohlc):
            o, h, l, cl = ohlc[i]
        else:
            o, h, l, cl = c, c + 0.8, c - 0.7, c
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=start + timedelta(minutes=i),
                open=o,
                high=h,
                low=l,
                close=cl,
                tick_volume=int(volumes[i]) if volumes else 100,
                is_complete=True,
            )
        )
    return bars


def base_fixture() -> tuple[list[BarData], TickData]:
    rng = np.random.default_rng(7)
    closes = [2000.0]
    for _ in range(69):
        closes.append(closes[-1] + float(rng.normal(0, 0.5)))
    bars = make_bars(closes)
    tick = TickData(
        symbol="XAUUSD",
        timestamp=bars[-1].timestamp + timedelta(seconds=30),
        bid=bars[-1].close - 0.1,
        ask=bars[-1].close + 0.1,
        volume=1,
    )
    return bars, tick


def run() -> None:
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    bars, tick = base_fixture()

    # ---- Determinism: 100 runs ----
    vecs = []
    for _ in range(100):
        fv = engine.compute_from_bars(bars, tick)
        vecs.append(tuple(fv.to_tensor_input()))
    first = vecs[0]
    # note: vecs[i] is the i-th RUN, not the i-th feature
    mismatches = []
    for run_idx, v in enumerate(vecs[1:], start=1):
        for i in range(50):
            if v[i] != first[i]:
                mismatches.append((run_idx, i, v[i], first[i]))
    print(f"[DETERMINISM] 100 runs -> first={list(first)[:5]}...")
    print(
        f"[DETERMINISM] mismatching (run, idx, val, first): {mismatches[:10]} count={len(mismatches)}"
    )
    print(f"[DETERMINISM] VERDICT: {'PASS' if not mismatches else 'FAIL'}")

    # ---- Causality: change ONLY the future bar T+1 ----
    bars_t, tick_t = base_fixture()
    fv_t = engine.compute_from_bars(bars_t, tick_t)
    vec_t = fv_t.to_tensor_input()

    bars_future = [b for b in bars_t]
    last = bars_future[-1]
    future = BarData(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=last.timestamp + timedelta(minutes=1),
        open=last.close,
        high=last.close + 9.0,  # huge future spike
        low=last.close - 9.0,
        close=last.close + 5.0,
        tick_volume=9999,
        is_complete=True,
    )
    bars_future.append(future)
    fv_t2 = engine.compute_from_bars(bars_future, tick_t)
    vec_t2 = fv_t2.to_tensor_input()

    causal_diffs = []
    for i in range(50):
        a, b = vec_t[i], vec_t2[i]
        if a != b:
            causal_diffs.append((i, a, b))
    print(f"[CAUSALITY T+1] features changed after appending future bar: {causal_diffs}")
    print(f"[CAUSALITY T+1] VERDICT: {'PASS' if not causal_diffs else 'FAIL'}")

    # ---- Causality: change ONLY a past bar (T-1: replace bar at idx 3) ----
    bars_past = [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=b.timestamp,
            open=b.open,
            high=b.high + 3.0,  # huge change in old history
            low=b.low,
            close=b.close,
            tick_volume=b.tick_volume,
            is_complete=True,
        )
        if i == 3
        else b
        for i, b in enumerate(bars_t)
    ]
    fv_past = engine.compute_from_bars(bars_past, tick_t)
    vec_past = fv_past.to_tensor_input()
    past_diffs = []
    for i in range(50):
        a, b = vec_t[i], vec_past[i]
        if a != b:
            past_diffs.append((i, a, b))
    print(f"[CAUSALITY T-1] features changed after altering bar#3: {past_diffs}")
    print(f"[CAUSALITY T-1] VERDICT: {'PASS' if not past_diffs else 'FAIL'}")

    # ---- Extra: tick AFTER last bar changes nothing that should be causal ----
    # (live tick mid is expected to affect live-displacement/z features by design)

    result = {
        "determinism_100_runs": {"pass": not mismatches, "mismatches": mismatches[:20]},
        "causality_t_plus_1": {"pass": not causal_diffs, "diffs": causal_diffs},
        "causality_t_minus_1": {"pass": not past_diffs, "diffs": past_diffs},
        "vec_t_first5": list(vec_t[:5]),
    }
    with open(
        r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\scratch\forensic_50d_determinism_causality.json",
        "w",
    ) as fh:
        json.dump(result, fh, indent=1)
    print("written scratch/forensic_50d_determinism_causality.json")


if __name__ == "__main__":
    run()
