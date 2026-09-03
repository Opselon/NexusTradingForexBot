"""
TASK-5 Champion Baseline Snapshot (2026-08-18)
==============================================
Freeze of the CURRENT production Champion before any TASK-5 work, so the
Champion is a verifiable control group. READ-ONLY — never mutate.

Canonical contract: 50D `scalp_v1` (features/schema.py), 4-logit ScalpNet
(NO_TRADE/BUY/SELL/WAIT-policy-bridge), loaded by LiveEngine at
config.model.model_artifact_path.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if REPO.name != "NexusTradingForexBot":
    # executed from another CWD: resolve against the repo root explicitly
    REPO = Path("C:/Users/Capsizer/source/repos/NexusTradingForexBot")
CHAMPION_DIR = REPO / "artifacts/models/scalp/XAUUSD/v1.0.0"
ARTIFACT = CHAMPION_DIR / "model.pt"
SCALER = CHAMPION_DIR / "model.scaler.npz"
OUT = REPO / "docs/task5_champion_baseline.json"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not ARTIFACT.exists():
        print("CHAMPION MISSING", ARTIFACT)
        return 1
    artifact_hash = sha256_file(ARTIFACT)
    scaler_hash = sha256_file(SCALER) if SCALER.exists() else "MISSING"

    import numpy as np

    scaler = np.load(SCALER)
    mean, std = scaler["mean"], scaler["std"]

    import torch

    sd = torch.load(ARTIFACT, map_location="cpu", weights_only=False)
    if isinstance(sd, dict) and "state_dict" in sd:
        sd = sd["state_dict"]
    in_dim = None
    out_dim = None
    for k, v in sd.items():
        if getattr(v, "ndim", 0) == 2 and in_dim is None:
            in_dim = v.shape[1]
        if k == "classifier.weight":
            out_dim = v.shape[0]

    snapshot = {
        "task": "TASK-5",
        "purpose": "CURRENT CHAMPION BASELINE FREEZE (control group)",
        "captured_at_utc": __import__("datetime")
        .datetime.now(__import__("datetime").UTC)
        .isoformat(),
        "champion": {
            "artifact_path": str(ARTIFACT),
            "model_id": "primary_scalp",
            "model_version": "1.0.0",
            "feature_schema_id": "scalp_v1",
            "feature_dimension": int(in_dim),
            "class_count": int(out_dim),
            "artifact_hash_sha256": artifact_hash,
            "scaler_hash_sha256": scaler_hash,
            "scaler_mean_shape": list(mean.shape),
            "scaler_std_shape": list(std.shape),
            "scaler_mean_first5": [round(float(x), 6) for x in mean[:5]],
            "scaler_std_first5": [round(float(x), 6) for x in std[:5]],
            "input_projection_first": "input_projection.weight  (128, dim)",
            "git_tracked": False,  # model artifacts are gitignored
        },
        "notes": [
            "Champion is loaded by LiveEngine from config.model.model_artifact_path",
            "CandidateTrainer NEVER writes to this path (candidate staging only)",
            "TASK-5 creates NO promotion path; Champion remains untouched until an",
            "operator-approved future workflow",
        ],
    }
    OUT.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    print(json.dumps(snapshot, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
