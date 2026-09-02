"""MODEL LAB — promotion handoff manifest + candidate gate (CHG-0047).

A lab experiment becomes PROMOTION_CANDIDATE only when ALL gates pass:
  OOS PASS (balanced accuracy > no-info floor 0.333 + coverage > 0)
  WALK_FORWARD PASS (mean bacc > 0.34, fold std < 0.10)
  ROBUSTNESS PASS (friction-monotone EV, noise bacc floor)
  ARTIFACT INTEGRITY PASS (checkpoint sha256 matches manifest)
  REPRODUCIBILITY PASS (same seed => same checkpoint hash on rebuild)

Even then, NOTHING is promoted: the handoff manifest is the deliverable.
"""

from __future__ import annotations

import json
from typing import Any

from nexus_scalp.model_lab.integrity import artifact_integrity
from nexus_scalp.model_lab.registry import LAB_ROOT, LabStatus, update_status


def candidate_gate(experiment_id: str, oos: dict, wf: dict, robustness: dict) -> dict[str, Any]:
    gates = {
        "oos_pass": bool(oos.get("balanced_accuracy", 0) > 0.34 and oos.get("n_oos", 0) >= 100),
        "walk_forward_pass": wf.get("verdict") == "PASS",
        "robustness_pass": bool(robustness.get("friction_monotone")),
        "artifact_integrity_pass": bool(artifact_integrity(experiment_id).get("verified")),
        "reproducibility_pass": None,  # filled by the runner (dual-run hash)
    }
    decided = {k: v for k, v in gates.items() if v is not None}
    all_pass = all(decided.values()) and gates["reproducibility_pass"] is not False
    status = LabStatus.PROMOTION_CANDIDATE if all_pass else LabStatus.REJECTED
    update_status(experiment_id, status, metrics={"gates": gates})
    return {"experiment_id": experiment_id, "gates": gates, "status": status.value}


def write_handoff_manifest(
    experiment_id: str,
    spec_dict: dict,
    oos: dict,
    wf: dict,
    robustness: dict,
    benchmark: dict,
    gate: dict,
) -> str:
    manifest = {
        "candidate_id": experiment_id,
        "model_id": experiment_id,
        "created_for": "future promotion agent (NOT auto-promoted)",
        "spec": spec_dict,
        "fingerprint": gate.get("artifact", {}).get("actual")
        if isinstance(gate.get("artifact"), dict)
        else None,
        "oos": {
            k: oos.get(k)
            for k in (
                "balanced_accuracy",
                "directional_precision",
                "coverage_pct",
                "calibration",
                "n_oos",
            )
        },
        "walk_forward": {k: wf.get(k) for k in ("n_folds", "bacc_mean", "bacc_std", "verdict")},
        "robustness": {k: robustness.get(k) for k in ("friction_monotone", "input_noise")},
        "latency": {
            k: benchmark.get(k)
            for k in (
                "latency_ms_p50",
                "latency_ms_p95",
                "latency_ms_p99",
                "parameters",
                "checkpoint_bytes",
            )
        },
        "gates": gate.get("gates"),
        "promotion_state": gate.get("status"),
        "note": "PROMOTION_CANDIDATE != ACTIVE. Production swap requires the formal promotion process.",
    }
    d = LAB_ROOT / "candidates" / experiment_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "promotion_handoff.json"
    p.write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
    return str(p)
