"""Evidence assembly for the confidence-calibration blocker decision.

Answers ONE question with repo data, no fabrication:

    Does a population of VALID, LABELED, outcome-resolved, model-identity-
    pinned confidence observations exist that satisfies the calibration
    contract's MIN_CALIBRATION_SAMPLES (30) for BOTH a calibration slice and
    a disjoint validation slice, for the model the artifact would calibrate?

Eligibility rules enforced here (mission phases 1-3):
  * real model confidence recorded at decision time (signal_confidence);
  * executed (broker truth) AND closed AND non-zero realized PnL
    (unambiguous outcome; zero-PnL rows excluded as instructed);
  * model identity = (model_id, artifact_fingerprint) from the experience
    provenance — a calibration is valid ONLY for one exact artifact;
  * outcome AFTER decision (causality — enforced by the ledger schema);
  * NO OOS-membership flag exists in the ledger (verified: neither
    audit_experiences nor audit_experience_outcomes records dataset/OOS
    membership) — paper outcomes are post-deployment observations of the
    serving artifact, which is the closest legitimate analogue to a
    holdout, but per-artifact counts still fail the contract.

Writes a JSON evidence census (no trading behavior is touched).
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, UTC
from pathlib import Path

ARTIFACT_TARGET = "artifacts/models/scalp/XAUUSD/70d_liquidity/confidence_calibration.json"
MIN_CAL = 30  # must equal MIN_CALIBRATION_SAMPLES in confidence_calibration.py
EVIDENCE_PATH = Path("scratch/calibration_evidence_census.json")


def census() -> dict:
    con = sqlite3.connect("artifacts/audit.db")
    cur = con.cursor()
    q = """
    SELECT e.model_id, e.payload, e.signal_confidence, o.realized_pnl_usd,
           o.outcome_timestamp
    FROM audit_experiences e
    JOIN audit_experience_outcomes o ON o.idempotency_key = e.idempotency_key
    WHERE o.is_executed = 1 AND o.is_closed = 1
      AND o.realized_pnl_usd IS NOT NULL AND o.realized_pnl_usd != 0.0
    ORDER BY o.outcome_timestamp
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for model_id, payload, conf, pnl, ts in cur.execute(q).fetchall():
        prov = json.loads(payload).get("provenance", {})
        fp = str(prov.get("artifact_fingerprint", ""))
        groups[(model_id, str(prov.get("model_version", "")), fp)].append(
            {"ts": ts, "conf": float(conf or 0.0), "pnl": float(pnl)}
        )
    con.close()

    out_groups = {}
    for (mid, ver, fp), rows in sorted(groups.items()):
        eligible = [r for r in rows if r["conf"] >= 0.05]
        half = len(eligible) // 2
        out_groups[f"{mid}|{ver}|{fp}"] = {
            "eligible_total": len(eligible),
            "wins": sum(1 for r in eligible if r["pnl"] > 0),
            "losses": sum(1 for r in eligible if r["pnl"] < 0),
            "date_range": [eligible[0]["ts"][:10], eligible[-1]["ts"][:10]] if eligible else [],
            "cal_half_n": half,
            "val_half_n": len(eligible) - half,
            "meets_min_both_splits": half >= MIN_CAL and (len(eligible) - half) >= MIN_CAL,
            "artifact_still_on_disk": _artifact_on_disk(fp),
        }

    serving_fp = _sha16("artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt")
    serving_rows = [
        g for k, g in out_groups.items() if k.endswith(serving_fp) and "scalp_v3" in k or k.endswith(serving_fp)
    ]
    serving_n = sum(g["eligible_total"] for k, g in out_groups.items() if k.endswith(serving_fp))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "contract": {
            "min_per_split": MIN_CAL,
            "artifact_target": ARTIFACT_TARGET,
            "outcome_definition": "executed AND closed AND realized_pnl_usd != 0",
            "confidence_definition": "signal_confidence (DIRECTIONAL_NORMALIZED) >= 0.05 at decision time",
            "identity_binding": "(model_id, model_version, artifact_fingerprint) from experience provenance",
        },
        "serving_artifact_fingerprint": serving_fp,
        "serving_artifact_eligible_outcomes": serving_n,
        "groups": out_groups,
        "verdict": _verdict(out_groups, serving_fp),
    }


def _sha16(path: str) -> str:
    import hashlib
    import os

    if not os.path.exists(path):
        return "MISSING"
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def _artifact_on_disk(fp: str) -> bool:
    import hashlib
    import os

    for root, _dirs, files in os.walk("artifacts/models"):
        for f in files:
            if f.endswith(".pt"):
                p = os.path.join(root, f)
                try:
                    if hashlib.sha256(open(p, "rb").read()).hexdigest()[:16] == fp:
                        return True
                except OSError:
                    continue
    return False


def _verdict(groups: dict, serving_fp: str) -> dict:
    any_ok = [k for k, g in groups.items() if g["meets_min_both_splits"]]
    serving_ok = [k for k in any_ok if k.endswith(serving_fp)]
    return {
        "any_group_meets_min_both_splits": bool(any_ok),
        "groups_meeting": any_ok,
        "serving_artifact_meets": bool(serving_ok),
        "decision": (
            "FIT_ALLOWED"
            if serving_ok
            else ("FIT_BLOCKED_IDENTITY_MISMATCH" if any_ok else "INSUFFICIENT_EVIDENCE")
        ),
    }


if __name__ == "__main__":
    result = census()
    EVIDENCE_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["verdict"], indent=1))
    print("serving artifact eligible outcomes:", result["serving_artifact_eligible_outcomes"])
    for k, g in result["groups"].items():
        print(f"{k}: n={g['eligible_total']} cal/val={g['cal_half_n']}/{g['val_half_n']} "
              f"on_disk={g['artifact_still_on_disk']} range={g['date_range']}")
