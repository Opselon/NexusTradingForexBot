"""Phase-2 timing micro-replay: ScalpFeatureEngine + SignalPolicy.evaluate_probabilities
on REAL captured XAUUSD M1 bars (scratch/data/forensic_50d_live_T1_bars.json, 900 bars,
MT5 capture 2026-08-18). READ-ONLY probe — no src changes.

Replays bar-by-bar (<=2000 bars): each step i uses completed_bars[:i] + synthetic tick
at bar i close (mirrors LiveEngine cold-start warmup pattern, live_engine.py:3170-3180).
Feeds torch probability vectors through evaluate_probabilities with regime_state=None:
  - all-NO_TRADE style (0.6/0.2/0.2/0.0) and BUY/SELL-biased vectors
Measures wall time per evaluate_probabilities call; tabulates NO_TRADE reason_code /
blocked_by / decision_stage distribution (confidence-gate rejection instrumentation
by reading outputs only).
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import torch

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.signals.policy import SignalPolicy

WT = Path(__file__).resolve().parent.parent  # scratch/ parent = worktree root
sys.path.insert(0, str(WT / "src")) if str(WT / "src") not in sys.path else None

BARS_PATH = WT / "scratch" / "data" / "forensic_50d_live_T1_bars.json"
MAX_BARS = 2000
WARMUP_BARS = 60  # engine needs >=55 bars before real (non cold-start) features


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round(p / 100.0 * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def main() -> None:
    payload = json.loads(BARS_PATH.read_text())
    bars = payload["bars"][:MAX_BARS]
    print(f"dataset={BARS_PATH.name} bars={len(bars)} first={bars[0]['time']} last={bars[-1]['time']}")

    engine = ScalpFeatureEngine(symbol="XAUUSD")
    policy = SignalPolicy()  # defaults: confidence_threshold=0.20, telemetry 4s

    # Probability vectors: leader (trained-class semantics NO_TRADE/BUY/SELL/WAIT)
    prob_sets = {
        "NO_TRADE_0.6": [0.6, 0.2, 0.2, 0.0],
        "NO_TRADE_0.5": [0.5, 0.3, 0.15, 0.05],
        "BUY_bias": [0.2, 0.55, 0.15, 0.10],
        "SELL_bias": [0.2, 0.15, 0.55, 0.10],
        "BUY_sweep": [0.15, 0.60, 0.15, 0.10],
        "SELL_sweep": [0.15, 0.15, 0.60, 0.10],
    }

    feature_ms: list[float] = []
    evaluate_ms: list[float] = []
    reason_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    blocked_counts: dict[str, int] = {}
    conf_gate_reasons: list[tuple[float, str]] = []  # (confidence, reason) at CONFIDENCE_GATE
    action_counts: dict[str, int] = {}
    n_evals = 0
    n_features = 0
    cold_start = 0

    t0 = time.perf_counter()
    base_ts = datetime(2026, 8, 18, 1, 14, tzinfo=UTC)
    for i, bar in enumerate(bars):
        completed = bars[:i]
        if i < WARMUP_BARS:
            continue
        # Build BarData-like objects lazily via bar_aggregator model
        from nexus_scalp.market_data.bar_aggregator import BarData

        hist = [
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=datetime.fromisoformat(b["time"]),
                open=b["open"],
                high=b["high"],
                low=b["low"],
                close=b["close"],
                tick_volume=b["tick_volume"],
                is_complete=True,
            )
            for b in completed
        ]
        cur = bars[i]
        ts = datetime.fromisoformat(cur["time"])
        spread = 0.20
        tick = TickData(
            symbol="XAUUSD", timestamp=ts, bid=cur["close"], ask=cur["close"] + spread,
            volume=float(cur["tick_volume"]),
        )

        tf0 = time.perf_counter()
        fv = engine.compute_from_bars(hist, tick)
        tf1 = time.perf_counter()
        feature_ms.append((tf1 - tf0) * 1000.0)
        n_features += 1
        if fv.atr_m1 == 1.50 and len(hist) < 55:
            cold_start += 1

        for name, probs in prob_sets.items():
            pt = torch.tensor(probs, dtype=torch.float32)
            te0 = time.perf_counter()
            proposal = policy.evaluate_probabilities(
                probabilities=pt,
                current_tick=tick,
                feature_vector=fv,
                regime_state=None,
                completed_bars=hist,
            )
            te1 = time.perf_counter()
            evaluate_ms.append((te1 - te0) * 1000.0)
            n_evals += 1

            reason = proposal.reason_code
            # collapse dynamic numeric parts for stable counting
            key = reason.split(" (")[0][:80]
            reason_counts[key] = reason_counts.get(key, 0) + 1
            stage_counts[proposal.decision_stage or ""] = (
                stage_counts.get(proposal.decision_stage or "", 0) + 1
            )
            blocked_counts[proposal.blocked_by or ""] = (
                blocked_counts.get(proposal.blocked_by or "", 0) + 1
            )
            action_counts[str(proposal.action.value)] = (
                action_counts.get(str(proposal.action.value), 0) + 1
            )
            if "CONFIDENCE_GATE" == (proposal.decision_stage or ""):
                conf_gate_reasons.append((proposal.confidence, reason))

    t1 = time.perf_counter()

    fm = sorted(feature_ms)
    em = sorted(evaluate_ms)
    print("\n=== LATENCY (real replay) ===")
    print(f"feature compute_from_bars : n={n_features} p50={percentile(fm,50):.3f}ms "
          f"p95={percentile(fm,95):.3f}ms p99={percentile(fm,99):.3f}ms max={fm[-1]:.3f}ms mean={statistics.fmean(fm):.3f}ms")
    print(f"evaluate_probabilities    : n={n_evals} p50={percentile(em,50):.3f}ms "
          f"p95={percentile(em,95):.3f}ms p99={percentile(em,99):.3f}ms max={em[-1]:.3f}ms mean={statistics.fmean(em):.3f}ms")
    print(f"total wall: {t1-t0:.2f}s  evals/sec at p50: {1000.0/percentile(em,50):.0f}")

    print("\n=== DECISION DISTRIBUTION (all prob vectors) ===")
    print("actions:", dict(sorted(action_counts.items(), key=lambda kv: -kv[1])))
    print("\nblocked_by:", dict(sorted(blocked_counts.items(), key=lambda kv: -kv[1])))
    print("\ndecision_stage:", dict(sorted(stage_counts.items(), key=lambda kv: -kv[1])))
    print("\nreason_code (top 15):")
    for r, c in sorted(reason_counts.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {c:5d}  {r}")

    print("\n=== CONFIDENCE GATE REJECTIONS ===")
    print(f"count={len(conf_gate_reasons)} of {n_evals} evals "
          f"({100.0*len(conf_gate_reasons)/max(1,n_evals):.1f}%)")
    if conf_gate_reasons:
        confs = sorted(c for c, _ in conf_gate_reasons)
        print(f"rejected-confidence p50={percentile(confs,50):.3f} "
              f"p95={percentile(confs,95):.3f} max={confs[-1]:.3f}")
        # per prob-vector rejection: derive from reason threshold text? count by confidence bucket
        b1 = sum(1 for c, _ in conf_gate_reasons if c < 0.20)
        b2 = sum(1 for c, _ in conf_gate_reasons if 0.20 <= c < 0.30)
        b3 = sum(1 for c, _ in conf_gate_reasons if 0.30 <= c < 0.40)
        b4 = sum(1 for c, _ in conf_gate_reasons if c >= 0.40)
        print(f"conf buckets: <0.20:{b1} 0.20-0.30:{b2} 0.30-0.40:{b3} >=0.40:{b4}")
    print(f"\ncold_start_feature_vectors={cold_start}")


if __name__ == "__main__":
    main()
