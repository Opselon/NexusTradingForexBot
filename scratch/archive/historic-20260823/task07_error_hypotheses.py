"""TASK-07-70D-LIQUIDITY-RESEARCH — model-error attribution status + research hypotheses (step 8).

Mission 27: "Analyze where the 70D model fails" — requires a trained 70D model.
None exists (only smoke/test exp_liq* on ds_test; no validated candidate).
The framework below is therefore a PRECISE, executable definition of what will
be computed when the model lands (TASK-3/4), plus an honest NOT_COMPUTABLE state.

Mission 25/26: research hypotheses, never production rules. Each hypothesis is
recorded with definition, dataset, sample, effect, confidence, OOS evidence,
status (DISCOVERED/EVALUATING/VALIDATED/REJECTED per research lifecycle).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
OUT = REPO / "scratch" / "task07_research"
OUT.mkdir(parents=True, exist_ok=True)

BASELINE_ID = "e85de540e09d3339"


def main() -> int:
    # ---- model error attribution (honest: not computable yet) ----
    error_attr = {
        "research_run_id": "task07_error_attr_01",
        "research_baseline_id": BASELINE_ID,
        "status": "NOT_COMPUTABLE",
        "reason": "No trained 70D model exists (exp_liq* are ds_test smoke experiments; no validated candidate in experience_model_registry). Error attribution requires model predictions on labeled data.",
        "framework_when_model_exists": {
            "error_classes": ["false_BUY", "false_SELL", "missed_BUY", "missed_SELL", "NO_TRADE_when_profitable_move_followed", "trade_when_adverse_move_followed"],
            "liquidity_state_cross": ["sweep", "distance_bin", "confluence_bucket", "htf_sign", "internal_external_zone"],
            "output": "per error class x liquidity-state contingency: lift = P(error | liquidity_state) / P(error); n per cell; LOW_EVIDENCE when n < 30",
            "note": "identical protocol to event_studies.json (causal features, forward labels)",
        },
    }
    (OUT / "model_error_attribution.json").write_text(json.dumps(error_attr, indent=2), encoding="utf-8")

    # ---- research hypotheses (mission 25/26) ----
    hypotheses = {
        "research_run_id": "task07_hypotheses_01",
        "research_baseline_id": BASELINE_ID,
        "lifecycle": "research-only: DISCOVERED -> EVALUATING -> VALIDATED/REJECTED by governance. None of these are production rules.",
        "hypotheses": [
            {
                "hypothesis_id": "HYP-LIQ-001",
                "definition": "High liquidity confluence (>2) is followed by LOWER volatility and LOWER directional magnitude than low confluence.",
                "dataset": "data/raw/XAUUSD_M5.parquet (causal sample, 1000 events)",
                "sample": "n=603 high vs n=114 low (H5)",
                "effect": "mean abs move 1.12 (high) vs 1.68 (low) ATR at H5; vol ratio 2.32 vs 2.94",
                "confidence": "MODERATE (distribution overlap; exploratory)",
                "oos_evidence": "none yet (no model-level OOS)",
                "status": "DISCOVERED",
                "note": "Do NOT convert to a rule (e.g. 'avoid high confluence'). Could be a calm-structure artifact.",
            },
            {
                "hypothesis_id": "HYP-LIQ-002",
                "definition": "Liquidity sweep state carries volatility/activity information but NOT directional information (no reversal/continuation edge).",
                "dataset": "data/raw/XAUUSD_M5.parquet (causal sample, 1000 events)",
                "sample": "n=785 positive sweep, n=214 negative (zero n=1 LOW_EVIDENCE)",
                "effect": "reversal prob ~0.68 (H5) / ~0.80 (H15) for both directions; abs move nearly identical",
                "confidence": "MODERATE (consistent across horizons, but no directional separation)",
                "oos_evidence": "none yet",
                "status": "DISCOVERED",
                "note": "Sweep may HELP as a volatility/regime gate, not as a direction signal.",
            },
            {
                "hypothesis_id": "HYP-LIQ-003",
                "definition": "Price FAR from liquidity (far distance bins) trends/extends more than price NEAR liquidity.",
                "dataset": "data/raw/XAUUSD_M5.parquet (causal sample)",
                "sample": "n=334 far vs 353 near (BSL H15)",
                "effect": "abs move 2.58 (far) vs 2.19 (near) ATR at H15; monotone across bins",
                "confidence": "WEAK-MODERATE (monotone but overlapping; direction-agnostic)",
                "oos_evidence": "none yet",
                "status": "DISCOVERED",
            },
            {
                "hypothesis_id": "HYP-LIQ-004",
                "definition": "HTF liquidity score sign does NOT dominate the 70D signal (positive vs negative similar forward outcomes).",
                "dataset": "data/raw/XAUUSD_M5.parquet (causal sample)",
                "sample": "n=453 negative, n=547 positive",
                "effect": "abs move 2.20 vs 2.41 ATR at H15; no meaningful separation",
                "confidence": "MODERATE",
                "oos_evidence": "none yet",
                "status": "DISCOVERED",
            },
            {
                "hypothesis_id": "HYP-LIQ-005",
                "definition": "liquidity-v1.1 (TASK-06) resolves the v1.0 degeneracies (confluence saturation, sweep flood, eqh step) and is therefore the correct version for any model-level evaluation.",
                "dataset": "version-isolation run (identical causal inputs, 400 points)",
                "sample": "400 points",
                "effect": "confluence unique 2->225, saturation 0.9975->0.4325; eqh std 0.14->0.18; sweep 65% of rows changed",
                "confidence": "HIGH (distributional evidence; model-level verdict still required)",
                "oos_evidence": "TASK-4 benchmark protocol pending model training",
                "status": "EVALUATING",
                "note": "This is a VERSION decision, not a trading rule — mission 43 isolation.",
            },
        ],
        "global_notes": [
            "All event-study results are exploratory (multiple testing: 45+ feature/outcome dimensions; corrections needed before confirmatory claims — mission 35).",
            "No hypothesis has been converted to a strategy/parameter change (mission 26/47).",
            "sweep_zero bucket n=1 excluded — LOW_EVIDENCE (mission 8/34).",
        ],
    }
    (OUT / "research_hypotheses.json").write_text(json.dumps(hypotheses, indent=2), encoding="utf-8")

    print(json.dumps({
        "error_attribution": error_attr["status"],
        "hypotheses": [h["hypothesis_id"] + ":" + h["status"] for h in hypotheses["hypotheses"]],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())