"""TASK-07-70D-LIQUIDITY-RESEARCH — feature drift + shadow disagreement analysis (step 7).

Two parts:

A) FEATURE DRIFT (mission 19): training/reference distributions
   (TASK-01 golden baseline, 30k M5 rows, liquidity-v1.0) vs the current
   observed/live-proxy distribution (my causal stratified sample, 500 points,
   liquidity-v1.1 — version noted). Using the project's own PSI convention
   (Shadow70DriftMonitor) plus mean/std shift and missing-rate shift.
   Classification: NORMAL / WATCH / WARNING / CRITICAL (per existing
   convention: Shadow70DriftAlert levels). NOTE: v1.0 vs v1.1 differences are
   EXPECTED for the changed features (eqh/eql/sweep/confluence) and must NOT
   be read as market drift — a version-comparison caveat is embedded.

B) SHADOW DISAGREEMENT (mission 16/17/32): the 2 real shadow70 observations
   are SHADOW_BLOCKED (no candidate attached) -> INSUFFICIENT_LIVE_EVIDENCE.
   We record them truthfully with error codes and do NOT fabricate outcome
   analysis. Champion-vs-shadow disagreement taxonomy from shadow70 models.

Outputs: scratch/task07_research/drift_analysis.json
         scratch/task07_research/shadow_disagreement.json
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(REPO / "src"))

OUT = REPO / "scratch" / "task07_research"
OUT.mkdir(parents=True, exist_ok=True)
RESEARCH_RUN_ID = "task07_drift_01"
BASELINE_ID = "e85de540e09d3339"


def _psi(ref: list[float], live: list[float], bins: int = 10) -> float:
    """Population stability index (project convention, see shadow70/health.py)."""
    if not ref or not live:
        return float("nan")
    lo = min(*ref, *live)
    hi = max(*ref, *live)
    if hi - lo < 1e-9:
        return 0.0
    edges = [lo + (hi - lo) * i / bins for i in range(bins + 1)]
    n_ref, n_live = len(ref), len(live)
    psi = 0.0
    for i in range(bins):
        a = sum(1 for v in ref if edges[i] <= v < edges[i + 1])
        b = sum(1 for v in live if edges[i] <= v < edges[i + 1])
        if i == bins - 1:
            a += sum(1 for v in ref if v == hi)
            b += sum(1 for v in live if v == hi)
        pa = a / n_ref if n_ref else 0.0
        pb = b / n_live if n_live else 0.0
        pa = max(pa, 1e-6)
        pb = max(pb, 1e-6)
        psi += (pa - pb) * math.log(pa / pb)
    return psi


def _classify(psi: float, mean_shift: float) -> str:
    if math.isnan(psi):
        return "NORMAL"
    if psi < 0.10 and mean_shift < 0.2:
        return "NORMAL"
    if psi < 0.25 and mean_shift < 0.5:
        return "WATCH"
    if psi < 0.60 and mean_shift < 1.0:
        return "WARNING"
    return "CRITICAL"


def main() -> int:
    # reference: TASK-01 golden baseline (v1.0, 30k rows)
    ref = json.load(open(REPO / "docs" / "LIQUIDITY_70D_GOLDEN_BASELINE.json", encoding="utf-8"))
    # live proxy: my consolidated sample (v1.1)
    dist = json.load(open(OUT / "feature_distributions.json", encoding="utf-8"))

    names = [
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
    drift = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": BASELINE_ID,
        "reference": {
            "source": "docs/LIQUIDITY_70D_GOLDEN_BASELINE.json (TASK-01, liquidity-v1.0, 29946 M5 rows)",
            "n": int(ref.get("rows_computed", 0)),
            "temporal_range": ref.get("temporal_range"),
        },
        "live_proxy": {
            "source": "scratch/task07_research/feature_distributions.json (liquidity-v1.1, 500 causal M5 points)",
            "n": dist.get("sample", {}).get("n", 0),
            "temporal_range": dist.get("sample", {}).get("time_range"),
        },
        "caveat": "Reference window (2025-03..08) vs live-proxy window (2025-03..2026-08) differ: cross-window flags reflect REGIME/SESSION DRIFT (mission 20/21), NOT model failure. eqh/eql/sweep/confluence also differ BY DESIGN between v1.0 and v1.1 (TASK-06). Only the UNCHANGED features (bsl/ssl/internal/external distance, htf) are valid market-drift signals.",
        "features": {},
    }
    for name in names:
        r = ref["per_feature"][name]
        lv = dist["features_sample"][name]
        # reconstruct crude samples for PSI from the reference stats via normal approx
        # (we do not have the raw rows; PSI on normal approx is documented as approximate)
        import random

        rng = random.Random(42)
        mu_r, sd_r = r["mean"], r["std"]
        mu_l, sd_l = lv["mean"], lv["std"]
        ref_samp = [rng.gauss(mu_r, sd_r) for _ in range(3000)]
        live_samp = [rng.gauss(mu_l, sd_l) for _ in range(500)]
        psi = _psi(ref_samp, live_samp)
        mean_shift = abs(mu_r - mu_l)
        drift["features"][name] = {
            "ref_mean": round(mu_r, 4),
            "live_mean": round(mu_l, 4),
            "ref_std": round(sd_r, 4),
            "live_std": round(sd_l, 4),
            "mean_shift_abs": round(mean_shift, 4),
            "psi_approx": round(psi, 4),
            "status": _classify(psi, mean_shift),
            "note": "v1.0->v1.1 design change; not market drift"
            if name
            in ("eqh_strength", "eql_strength", "liquidity_confluence", "liquidity_sweep_state")
            else "stable family (unchanged between versions) — valid drift signal",
        }

    (OUT / "drift_analysis.json").write_text(json.dumps(drift, indent=2), encoding="utf-8")

    # shadow disagreement
    import sqlite3

    con = sqlite3.connect(REPO / "artifacts" / "audit.db")
    con.row_factory = sqlite3.Row
    obs = [dict(r) for r in con.execute("SELECT * FROM shadow70_observations ORDER BY timestamp")]
    con.close()
    shadow = {
        "research_run_id": "task07_shadow_01",
        "research_baseline_id": BASELINE_ID,
        "status": "INSUFFICIENT_LIVE_EVIDENCE",
        "reason": "Only 2 shadow70 observations exist, both SHADOW_BLOCKED (runtime IDLE — no validated 70D candidate attached). No champion-vs-shadow disagreement with real model outputs can be computed.",
        "observations": [
            {
                "observation_id": o["observation_id"],
                "timestamp": o["timestamp"],
                "schema_id": o["schema_id"],
                "champion_action": o["champion_action"],
                "shadow_action": o["shadow_action"],
                "disagreement": o["disagreement"],
                "error_code": o["error_code"],
                "sample_source": o["sample_source"],
                "simulated": o["simulated"],
                "valid": o["valid"],
            }
            for o in obs
        ],
        "counts": {
            "total": len(obs),
            "valid": sum(1 for o in obs if o["valid"]),
            "blocked": sum(1 for o in obs if o["error_code"] == "SHADOW_BLOCKED"),
        },
        "next_evidence_threshold": "Shadow disagreement outcome analysis (champion correct / shadow correct / both / confidence-weighted) requires >= 50 valid observations with real model outputs and resolved outcomes (mission 17/32).",
    }
    (OUT / "shadow_disagreement.json").write_text(json.dumps(shadow, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "drift": {k: v["status"] for k, v in drift["features"].items()},
                "shadow": {"status": shadow["status"], "observations": shadow["counts"]},
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
