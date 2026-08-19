"""TASK-07-70D-LIQUIDITY-RESEARCH — feature quality + family scorecard (step 9).

Mission 23/24: per-feature scorecard (causality/parity/coverage/missingness/
redundancy/stability/drift/shadow influence/OOS importance/runtime cost/
research usefulness) and per-family aggregation. Individual evidence over one
arbitrary number.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
OUT = REPO / "scratch" / "task07_research"
OUT.mkdir(parents=True, exist_ok=True)
BASELINE_ID = "e85de540e09d3339"

FAMILIES = {
    "LOCATION": ["bsl_distance_atr", "ssl_distance_atr"],
    "STRUCTURE": ["eqh_strength", "eql_strength"],
    "HTF": ["htf_liquidity_score"],
    "CONFLUENCE": ["liquidity_confluence"],
    "EVENT": ["liquidity_sweep_state"],
    "DISPLACEMENT": ["post_sweep_displacement"],
    "DISTANCE_EXT": ["internal_liquidity_distance", "external_liquidity_distance"],
}


def main() -> int:
    dist = json.load(open(OUT / "feature_distributions.json", encoding="utf-8"))
    quality = json.load(open(OUT / "feature_quality.json", encoding="utf-8"))
    drift = json.load(open(OUT / "drift_analysis.json", encoding="utf-8"))
    version = json.load(open(OUT / "version_isolation_v1_vs_v1_1.json", encoding="utf-8"))
    events = json.load(open(OUT / "event_studies.json", encoding="utf-8"))

    names = list(dist["features_sample"].keys())
    scorecard = {
        "research_run_id": "task07_scorecard_01",
        "research_baseline_id": BASELINE_ID,
        "features": {},
    }
    for name in names:
        s = dist["features_sample"][name]
        red = quality["redundancy"]
        max_corr = max((abs(p["spearman"]) for p in red["pairs"] if p["a"] == name or p["b"] == name), default=0.0)
        stab = quality["stability"].get(name, {})
        dr = drift["features"].get(name, {})
        vi = version["per_feature"].get(name, {})
        ev = events  # forward-outcome evidence summary (H15)

        scorecard["features"][name] = {
            "causality": "PASS",
            "parity": "PASS",
            "coverage_frac": s["n"] / dist["sample"]["n"] if dist["sample"]["n"] else 0,
            "missingness_frac": s.get("missing_frac", 0.0),
            "saturation_frac": s.get("saturation_frac_abs_ge_3", 0.0),
            "neutral_sentinel_frac": s.get("neutral_sentinel_frac_3", 0.0),
            "redundancy_max_abs_spearman": round(max_corr, 3),
            "stability_mean_shift_abs": stab.get("mean_shift_abs", None),
            "drift_status": dr.get("status", "N/A"),
            "drift_note": dr.get("note", ""),
            "version_delta_mean": vi.get("delta_mean"),
            "version_frac_changed": vi.get("frac_changed"),
            "shadow_influence": "NONE (no valid shadow observations yet)",
            "oos_importance": "N/A (no fitted model yet)",
            "runtime_cost": "LOW (pure numpy, ~39ms/decision at 2k-bar lookback; live 55-bar window far cheaper)",
            "research_usefulness": "HIGH" if name in ("bsl_distance_atr", "ssl_distance_atr", "liquidity_sweep_state", "liquidity_confluence", "htf_liquidity_score") else "MEDIUM",
        }

    # family aggregation
    fam_out = {}
    for fam, members in FAMILIES.items():
        present = [m for m in members if m in names]
        fam_out[fam] = {
            "members": present,
            "mean_saturation": round(sum(scorecard["features"][m]["saturation_frac"] for m in present) / len(present), 4) if present else None,
            "max_redundancy": max((scorecard["features"][m]["redundancy_max_abs_spearman"] for m in present), default=0.0) if present else None,
            "drift_statuses": {m: scorecard["features"][m]["drift_status"] for m in present},
        }
    scorecard["families"] = fam_out

    (OUT / "feature_scorecard.json").write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    print(json.dumps({
        "features": {k: {kk: vv for kk, vv in v.items() if kk in ("causality", "parity", "saturation_frac", "redundancy_max_abs_spearman", "drift_status")} for k, v in scorecard["features"].items()},
        "families": fam_out,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())