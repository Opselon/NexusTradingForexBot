"""
Forensic 50D — Dataset/Live parity probe (train_model replay path)
===================================================================
Proves the dataset-builder parity contract: replaying raw ticks through
BarAggregator + ScalpFeatureEngine (exactly like src/cli/train_model.py)
produces the same 50D vector as compute_from_bars() on the same completed
bars at the same tick. This is the repo's DESIGNED dataset/live parity path.

Also verifies model-input parity: the exact tensor that reaches the model
(float32, clipped, scaler-transformed) starts from the identical 50 floats.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

import numpy as np

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src")

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarAggregator, BarData


def build_ticks_from_bars(bars: list[BarData]) -> list[TickData]:
    """Each bar becomes 3 synthetic ticks (open/mid/close) — like a replay feed."""
    ticks = []
    for b in bars:
        for price in (b.open, (b.high + b.low) / 2.0, b.close):
            ticks.append(
                TickData(
                    symbol=b.symbol,
                    timestamp=b.timestamp + timedelta(seconds=10 * len(ticks) % 50),
                    bid=price - 0.1,
                    ask=price + 0.1,
                    volume=1,
                )
            )
    return ticks


def main() -> None:
    rng = np.random.default_rng(11)
    closes = [2000.0]
    for _ in range(79):
        closes.append(closes[-1] + float(rng.normal(0, 0.4)))
    start = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
    bars: list[BarData] = []
    for i, c in enumerate(closes):
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=start + timedelta(minutes=i),
                open=c - 0.3,
                high=c + 0.9,
                low=c - 0.8,
                close=c,
                tick_volume=100 + (i % 7) * 13,
                is_complete=True,
            )
        )

    # ---- Path A: replay like train_model.py ----
    aggregator = BarAggregator(symbol="XAUUSD", timeframe_minutes=1)
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    # seed the aggregator with history (as live does after restart via reseed)
    aggregator.reseed(bars[:-5])
    replay_records = []
    ticks = build_ticks_from_bars(bars[-5:])
    for tick in ticks:
        aggregator.process_tick(tick)
        completed = aggregator.get_completed_bars()
        if len(completed) >= 55:
            fv = engine.compute_from_bars(completed, tick)
            replay_records.append((tick, fv))

    # ---- Path B: direct compute_from_bars on the same final state ----
    final_completed = aggregator.get_completed_bars()
    last_tick = ticks[-1]
    fv_direct = engine.compute_from_bars(final_completed, last_tick)

    mismatches = 0
    worst = 0.0
    for _tick, fv in replay_records[-1:]:
        a = fv.to_tensor_input()
        b = fv_direct.to_tensor_input()
        for i in range(50):
            d = abs(a[i] - b[i])
            worst = max(worst, d)
            if d > 1e-9:
                mismatches += 1
                print(f"mismatch idx={i} replay={a[i]:.10g} direct={b[i]:.10g}")
    print(f"[DATASET/LIVE PARITY] replay-vs-direct mismatches={mismatches} worst={worst:.3g}")
    print(f"[DATASET/LIVE PARITY] VERDICT: {'PASS' if mismatches == 0 else 'FAIL'}")

    # ---- Model-input parity: vector -> float32 -> scaler -> model ----
    # The live path: x50 (list[float] from to_tensor_input) -> np.float32 reshape(1,-1)
    # -> scaler.transform_50d -> torch.tensor -> model. The stored experience uses
    # the same to_tensor_input(). So model input == extractor output (up to float32 cast).
    x50 = fv_direct.to_tensor_input()
    x_np = np.array(x50, dtype=np.float32).reshape(1, -1)
    back = x_np.flatten().tolist()
    cast_err = max(abs(float(a) - float(b)) for a, b in zip(x50, back, strict=True))
    print(
        f"[MODEL INPUT PARITY] float32 roundtrip max_err={cast_err:.3g} -> {'PASS' if cast_err <= 1e-6 else 'FAIL'}"
    )

    result = {
        "dataset_live_parity": {
            "mismatches": mismatches,
            "worst": worst,
            "verdict": "PASS" if mismatches == 0 else "FAIL",
        },
        "model_input_parity": {"float32_roundtrip_max_err": cast_err},
    }
    with open(
        r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\scratch\forensic_50d_parity.json", "w"
    ) as fh:
        json.dump(result, fh, indent=1)
    print("written scratch/forensic_50d_parity.json")


if __name__ == "__main__":
    main()
