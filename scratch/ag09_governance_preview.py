"""AGENT-09: governance preview (verify_candidate) for the 70D candidates.

Read-only — NEVER calls promotion/execute. Runs the canonical 14-gate
verify_candidate on:
  - task5_abc_C_v1 (the fair-benchmark 70D candidate, scalp_v3)
  - ag09_oos_C_v1  (the true-temporal-OOS 70D candidate, scalp_v3)
with the OOS + shadow evidence that ACTUALLY exists (no fabrication).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from nexus_scalp.governance.verify import verify_candidate
from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.features.schema_contract import feature_schema_hash

store = ArtifactStore()

CANDIDATES = ["task5_abc_C_v1", "ag09_oos_C_v1"]

results = {}
for mid in CANDIDATES:
    man = store.read_model_manifest(mid) or {}
    art = store.model_weights_path(mid)
    sca = store.model_scaler_path(mid)
    # OOS evidence actually recorded (ag09_oos_C_v1 has a temporal OOS artifact)
    oos_artifact = "artifacts/validation/70d_oos_results.json" if mid == "ag09_oos_C_v1" else ""
    shadow_evidence = None  # NO validated candidate entered shadow; honest
    try:
        v = verify_candidate(
            model_id=mid,
            model_version=man.get("model_version", "1.0.0"),
            artifact_path=art,
            scaler_path=sca,
            manifest=man,
            runtime_schema_id="scalp_v3",
            runtime_dimension=70,
            feature_schema_hash=feature_schema_hash(),
            liquidity_algorithm_version="70d-v1.0.0",
            training_commit="c5d6739" if mid == "task5_abc_C_v1" else "HEAD-ag09",
            oos_artifact=oos_artifact,
            shadow_evidence=shadow_evidence,
            news_contract={"valid": True, "detail": "news block FEATURE_DISABLED (neutral zeros) — documented policy"},
            liquidity_contract={"valid": True, "detail": "liquidity_engine 70d-v1.0.0 parity-verified"},
        )
        results[mid] = v
    except Exception as e:
        results[mid] = {"error": str(e)}

out = {
    "task": "TASK-09-70D-CANDIDATE-VALIDATION governance preview",
    "read_only": True,
    "promotion_not_called": True,
    "candidates": results,
}
Path("artifacts/validation/70d_governance_preview.json").write_text(
    json.dumps(out, indent=2, default=str), encoding="utf-8"
)
for mid, v in results.items():
    if "error" in v:
        print(mid, "ERROR", v["error"])
        continue
    print(f"--- {mid}: eligible={v['eligible']} reason={v['reason'][:80]}")
    for g, d in v["gates"].items():
        print(f"   {g}: {d['status']}")