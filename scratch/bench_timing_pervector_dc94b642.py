"""Follow-up: per-prob-vector breakdown of the real replay (which vector produced
candidates / confidence-gate rejections) + per-vector latency, same data, no src edits."""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import torch

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.signals.policy import SignalPolicy

WT = Path(__file__).resolve().parent.parent
BARS_PATH = WT / "scratch" / "data" / "forensic_50d_live_T1_bars.json"
WARMUP_BARS = 60


def pct(vals, p):
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round(p / 100.0 * (len(vals) - 1)))))
    return vals[k]


def main():
    bars = json.loads(BARS_PATH.read_text())["bars"][:2000]
    from nexus_scalp.market_data.bar_aggregator import BarData

    all_bar_objs = [
        BarData(symbol="XAUUSD", timeframe="M1", timestamp=datetime.fromisoformat(b["time"]),
                open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                tick_volume=b["tick_volume"], is_complete=True)
        for b in bars
    ]
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    policy = SignalPolicy()

    prob_sets = {
        "NO_TRADE_0.6": [0.6, 0.2, 0.2, 0.0],
        "NO_TRADE_0.5": [0.5, 0.3, 0.15, 0.05],
        "BUY_bias": [0.2, 0.55, 0.15, 0.10],
        "SELL_bias": [0.2, 0.15, 0.55, 0.10],
        "BUY_sweep": [0.15, 0.60, 0.15, 0.10],
        "SELL_sweep": [0.15, 0.15, 0.60, 0.10],
    }
    # fresh policy per vector-set so cooldown/throttle state of one set doesn't
    # gate the next (policies are cheap to construct)
    per_vec = {n: {"ms": [], "actions": {}, "reasons": {}, "conf_gate": 0, "cand": 0}
               for n in prob_sets}

    for name, probs in prob_sets.items():
        p = SignalPolicy()
        for i in range(WARMUP_BARS, len(bars)):
            hist = all_bar_objs[:i]
            cur = bars[i]
            ts = datetime.fromisoformat(cur["time"])
            tick = TickData(symbol="XAUUSD", timestamp=ts, bid=cur["close"],
                            ask=cur["close"] + 0.20, volume=float(cur["tick_volume"]))
            fv = engine.compute_from_bars(hist, tick)
            t0 = time.perf_counter()
            prop = p.evaluate_probabilities(
                probabilities=torch.tensor(probs, dtype=torch.float32),
                current_tick=tick, feature_vector=fv, regime_state=None,
                completed_bars=hist)
            dt = (time.perf_counter() - t0) * 1000.0
            v = per_vec[name]
            v["ms"].append(dt)
            v["actions"][str(prop.action.value)] = v["actions"].get(str(prop.action.value), 0) + 1
            key = prop.reason_code.split(" (")[0][:60]
            v["reasons"][key] = v["reasons"].get(key, 0) + 1
            if (prop.decision_stage or "") == "CONFIDENCE_GATE":
                v["conf_gate"] += 1
            if prop.model_action and prop.model_action != "NO_TRADE":
                v["cand"] += 1

    print(f"{'vector':<14} {'p50ms':>7} {'p95ms':>7} {'cand':>5} {'confGate':>8}  actions")
    for name, v in per_vec.items():
        acts = dict(sorted(v["actions"].items(), key=lambda kv: -kv[1]))
        print(f"{name:<14} {pct(v['ms'],50):7.3f} {pct(v['ms'],95):7.3f} "
              f"{v['cand']:5d} {v['conf_gate']:8d}  {acts}")
    print("\nPer-vector reason codes (non-NO_TRADE-noteable):")
    for name, v in per_vec.items():
        tops = sorted(v["reasons"].items(), key=lambda kv: -kv[1])[:4]
        print(f"  {name}: " + "; ".join(f"{r}={c}" for r, c in tops))

    # dedicated NO_TRADE_0.6 rejection anatomy
    v = per_vec["NO_TRADE_0.6"]
    print(f"\nNO_TRADE_0.6: candidates={v['cand']}/840, confidence-gate rejections={v['conf_gate']}")
    print("NO_TRADE_0.6 top reasons:", sorted(v["reasons"].items(), key=lambda kv: -kv[1])[:6])


if __name__ == "__main__":
    main()
