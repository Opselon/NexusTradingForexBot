"""Perf baseline probe: hot-path feature pipeline + model forward (re-created at HEAD cbb9a06).

Measures with time.perf_counter_ns, repeated runs, p50/p95/max:
1. ScalpFeatureEngine.compute_from_bars per tick at 600/900/4000 bars.
2. Sub-costs: aggregate_bars x4, SMC swing scan, _cold_start guard.
3. Model forward (ScalpNet 70->4) single + batch.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from nexus_scalp.domain.models import TickData  # noqa: E402
from nexus_scalp.features.scalp_features import (  # noqa: E402
    ScalpFeatureEngine,
    aggregate_bars,
)
from nexus_scalp.market_data.bar_aggregator import BarData  # noqa: E402


def make_bars(n: int):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rng = np.random.default_rng(42)
    out: list[BarData] = []
    price = 2000.0
    for i in range(n):
        ts = start + timedelta(minutes=i)
        o = price
        c = price + float(rng.normal(0, 1.5))
        h = max(o, c) + float(abs(rng.normal(0, 0.8)))
        l = min(o, c) - float(abs(rng.normal(0, 0.8)))
        out.append(
            BarData(
                symbol="XAUUSD", timeframe="M1", timestamp=ts,
                open=o, high=h, low=l, close=c,
                tick_volume=int(rng.integers(10, 500)), is_complete=True,
            )
        )
        price = c
    return out


def make_tick(last):
    return TickData(
        symbol="XAUUSD",
        timestamp=last.timestamp + timedelta(seconds=30),
        bid=last.close + 0.02, ask=last.close + 0.05, volume=3,
    )


def bench(fn, repeat=30, warmup=2):
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter_ns()
        fn()
        times.append((time.perf_counter_ns() - t0) / 1e6)
    times.sort()
    return {
        "p50_ms": round(statistics.median(times), 4),
        "p95_ms": round(times[int(len(times) * 0.95) - 1], 4),
        "max_ms": round(times[-1], 4),
        "runs": repeat,
    }


def smc_scan(bars):
    swing_highs = []
    swing_lows = []
    for i in range(5, len(bars) - 5):
        wh = [b.high for b in bars[i - 5 : i + 6]]
        wl = [b.low for b in bars[i - 5 : i + 6]]
        if bars[i].high == max(wh):
            swing_highs.append((i, bars[i].high))
        if bars[i].low == min(wl):
            swing_lows.append((i, bars[i].low))
    return swing_highs, swing_lows


def main() -> None:
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    results: dict = {"head": "cbb9a06", "feature_pipeline": {}, "model": {}}
    for n_bars in (600, 900, 4000):
        bars = make_bars(n_bars)
        tick = make_tick(bars[-1])
        key = f"compute_from_bars_{n_bars}bars"
        results["feature_pipeline"][key] = bench(lambda: engine.compute_from_bars(bars, tick))
        results["feature_pipeline"][f"aggregate_m15_{n_bars}"] = bench(
            lambda: aggregate_bars(bars, 15), repeat=15
        )
        results["feature_pipeline"][f"aggregate_h1_{n_bars}"] = bench(
            lambda: aggregate_bars(bars, 60), repeat=15
        )
        results["feature_pipeline"][f"aggregate_h4_{n_bars}"] = bench(
            lambda: aggregate_bars(bars, 240), repeat=15
        )
        results["feature_pipeline"][f"smc_scan_{n_bars}"] = bench(
            lambda: smc_scan(bars), repeat=15
        )

    try:
        import torch
        from nexus_scalp.models.scalp_net import ScalpNet

        torch.manual_seed(0)
        model = ScalpNet(num_features=70, num_classes=4, hidden_dim=128, num_heads=4)
        model.eval()
        x = torch.randn(1, 70, dtype=torch.float32)
        with torch.inference_mode():
            for _ in range(5):
                model(x)
        results["model"]["scalpnet70_4_single"] = bench(
            lambda: model(x), repeat=60
        )
        xb = torch.randn(32, 70, dtype=torch.float32)
        with torch.inference_mode():
            model(xb)
        results["model"]["scalpnet70_4_batch32"] = bench(
            lambda: model(xb), repeat=40
        )
        results["model"]["torch_threads"] = torch.get_num_threads()
    except Exception as e:  # noqa: BLE001
        results["model"] = {"error": str(e)[:200]}

    out_dir = REPO / "artifacts" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "perf_baseline_cbb9a06.json"
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()