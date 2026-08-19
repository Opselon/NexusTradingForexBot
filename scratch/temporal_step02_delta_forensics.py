"""STEP-02 — feature delta forensics + flip attribution (TASK-TEMPORAL-01).

Consumes artifacts/forensics/70d_signal_flapping_trace.json (the STEP-01b
real-model event trace) and computes:

1. Delta(i) = feature[i] - feature[i-1] for all 70 dims across every
   consecutive inference.
2. Liquidity 60..69 focus: current / previous / delta / abs-delta /
   percentage delta / time-since-change per feature.
3. Flip attribution: for every BUY<->SELL event, which features changed
   materially (|delta| > threshold), classified dominant/secondary/unchanged,
   bucketed by family (base/news/liquidity).
4. Decision-margin distribution (PBUY - PSELL) for stable vs flip events.

Outputs:
  artifacts/forensics/liquidity_feature_deltas.json
  (liquidity deltas + per-feature statistics + per-flip attribution)
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRACE = REPO / "artifacts/forensics/70d_signal_flapping_trace.json"
OUT = REPO / "artifacts/forensics/liquidity_feature_deltas.json"

LIQ_NAMES = [
    "bsl_distance_atr",
    "ssl_distance_atr",
    "eqh_strength",
    "eql_strength",
    "htf_liquidity_score",
    "internal_liquidity_distance",
    "external_liquidity_distance",
    "liquidity_confluence",
    "liquidity_sweep_state",
    "post_sweep_displacement",
]

MATERIAL_DELTA = 0.05  # liquidity values are [-3,3]; 0.05 is a material move


def direction_of(ev: dict) -> str:
    if ev["predicted_class"] == 1:
        return "BUY"
    if ev["predicted_class"] == 2:
        return "SELL"
    return "NONE"


def main() -> None:
    trace = json.loads(TRACE.read_text(encoding="utf-8"))
    events = trace["events"]
    n = len(events)
    print(f"[STEP-02] events={n} source={trace['capture']['harness']}")

    vec = [e["vector70"] for e in events]

    # ---- per-liquidity-feature delta stats --------------------------------
    liq_rows: list[dict] = []
    for fidx in range(10):
        cur = [vec[i][60 + fidx] for i in range(n)]
        [None, *cur[:-1]]
        deltas = [None] + [cur[i] - cur[i - 1] for i in range(1, n)]
        abs_deltas = [d for d in deltas if d is not None]
        # time since change: seconds since last nonzero delta (per bar)
        times_since: list[float] = []
        last_change = 0
        for i in range(1, n):
            last_change = 0 if abs(deltas[i]) > 1e-12 else last_change + 1
            times_since.append(last_change)
        changed = sum(1 for d in deltas if d is not None and abs(d) > 1e-12)
        [
            (deltas[i] / abs(cur[i - 1]) * 100.0) if i > 0 and abs(cur[i - 1]) > 1e-9 else None
            for i in range(n)
        ]
        liq_rows.append(
            {
                "name": LIQ_NAMES[fidx],
                "index": 60 + fidx,
                "current_first": cur[0],
                "current_last": cur[-1],
                "min": round(min(cur), 6),
                "max": round(max(cur), 6),
                "mean": round(statistics.mean(cur), 6),
                "stdev": round(statistics.pstdev(cur), 6),
                "unique_values": len(set(round(v, 9) for v in cur)),
                "changed_rows": changed,
                "change_fraction": round(changed / (n - 1), 4),
                "mean_abs_delta": round(statistics.mean(abs_deltas), 6) if abs_deltas else 0.0,
                "max_abs_delta": round(max(abs_deltas), 6) if abs_deltas else 0.0,
                "median_abs_delta": round(statistics.median(abs_deltas), 6) if abs_deltas else 0.0,
                "times_since_change_max_bars": max(times_since) if times_since else 0,
                "sample_deltas": [round(d, 6) if d is not None else None for d in deltas[:12]],
            }
        )

    # ---- flip events + attribution ----------------------------------------
    seq = [direction_of(e) for e in events]
    flips: list[dict] = []
    for i in range(1, n):
        if seq[i] != seq[i - 1] and seq[i] != "NONE" and seq[i - 1] != "NONE":
            delta70 = [vec[i][j] - vec[i - 1][j] for j in range(70)]
            material = {j: abs(delta70[j]) > MATERIAL_DELTA for j in range(70)}
            fam = {"base": sum(1 for j in range(50) if material[j]),
                   "news": sum(1 for j in range(50, 60) if material[j]),
                   "liquidity": sum(1 for j in range(60, 70) if material[j])}
            liq_material = [j for j in range(60, 70) if material[j]]
            dominant = max(fam, key=fam.get) if any(fam.values()) else "NONE"
            ev_i = events[i]
            flips.append(
                {
                    "at_index": i,
                    "timestamp": ev_i["timestamp"],
                    "from": seq[i - 1],
                    "to": seq[i],
                    "interval_seconds": (datetime.fromisoformat(ev_i["timestamp"])
                                         - datetime.fromisoformat(events[i - 1]["timestamp"])).total_seconds(),
                    "decision_margin_before": round(
                        abs(events[i - 1]["probabilities"][1] - events[i - 1]["probabilities"][2]), 6),
                    "decision_margin_after": round(
                        abs(ev_i["probabilities"][1] - ev_i["probabilities"][2]), 6),
                    "pbuy_after": round(ev_i["probabilities"][1], 6),
                    "psell_after": round(ev_i["probabilities"][2], 6),
                    "material_changes": sum(material.values()),
                    "family_counts": fam,
                    "dominant_family": dominant,
                    "liquidity_material_indices": liq_material,
                    "liquidity_material_names": [LIQ_NAMES[j - 60] for j in liq_material],
                    "liquidity_delta": {LIQ_NAMES[j - 60]: round(delta70[j], 6) for j in range(60, 70)},
                    "attribution": "NOT_AVAILABLE" if not any(fam.values()) else dominant,
                }
            )

    # ---- decision margin stats (boundary analysis) ------------------------
    margins = [abs(e["probabilities"][1] - e["probabilities"][2]) for e in events]
    margin_stats = {
        "min": round(min(margins), 6),
        "p25": round(sorted(margins)[int(len(margins) * 0.25)], 6),
        "median": round(statistics.median(margins), 6),
        "p75": round(sorted(margins)[int(len(margins) * 0.75)], 6),
        "p95": round(sorted(margins)[int(len(margins) * 0.95) - 1], 6),
        "max": round(max(margins), 6),
        "mean": round(statistics.mean(margins), 6),
    }
    flip_margins = [f["decision_margin_before"] for f in flips]
    stable_margins = [
        margins[i] for i in range(n) if not any(f["at_index"] == i for f in flips) and margins[i] < 0.5
    ]
    margin_stats["flip_event_margin_median"] = (
        round(statistics.median(flip_margins), 6) if flip_margins else None)
    margin_stats["flip_event_margin_max"] = max(flip_margins) if flip_margins else None
    margin_stats["stable_event_margin_median"] = (
        round(statistics.median(stable_margins), 6) if stable_margins else None)

    payload = {
        "analysis": "STEP-02 feature delta forensics (TASK-TEMPORAL-01)",
        "source_trace": str(TRACE),
        "events": n,
        "material_delta_threshold": MATERIAL_DELTA,
        "flip_count": len(flips),
        "flip_events": flips,
        "liquidity_feature_stats": liq_rows,
        "decision_margin_stats": margin_stats,
        "fidelity": {
            "note": "deltas are computed on the captured vector70 sequence "
                    "(real 70D baseline probabilities when logits_source=TRAINED_70D_BASELINE)",
            "logits_source": trace["capture"].get("model_id", "?"),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    print(f"[STEP-02] wrote {OUT}")
    print(f"[STEP-02] flips={len(flips)} margin_stats={json.dumps(margin_stats)}")
    print("[STEP-02] per-feature (name, uniq, chg_frac, mean_abs_delta):")
    for r in liq_rows:
        print(f"   {r['name']:32s} uniq={r['unique_values']:3d} chg={r['change_fraction']:.3f} "
              f"mad={r['mean_abs_delta']:.4f} tsc_max={r['times_since_change_max_bars']}")


if __name__ == "__main__":
    main()