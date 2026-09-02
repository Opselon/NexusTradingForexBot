"""MODEL LAB — production baseline freeze (BASELINE_PRODUCTION_REFERENCE).

Captures a complete, immutable provenance snapshot of the current production
Champion WITHOUT loading it into any serving path and WITHOUT copying or
moving its bytes. Reads-only: hashes, metadata, architecture shape.

Captured per the model-lab brief (section 4):
  model fingerprint, architecture, input dim, output dim, class order,
  scaler fingerprint, training metadata, feature schema, git revision.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHAMPION_DIR = Path("artifacts/models/scalp/XAUUSD/70d_liquidity")
CLASS_ORDER_4LOGIT = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET", "WAIT"]
LABEL_MAPPING_3CLASS = {"NO_TRADE": 0, "BUY_MARKET": 1, "SELL_MARKET": 2}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def freeze_baseline(git_revision: str) -> dict[str, Any]:
    """Read-only provenance snapshot of the production Champion."""
    model_path = CHAMPION_DIR / "model.pt"
    scaler_path = CHAMPION_DIR / "model.scaler.npz"
    meta_path = CHAMPION_DIR / "model.meta.json"
    if not model_path.exists():
        raise FileNotFoundError(f"production champion missing: {model_path}")

    meta: dict[str, Any] = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # Architecture probe: input projection + classifier shapes from the
    # state dict — read WITHOUT executing the model (inference_mode off,
    # weights_only=True, map_location cpu, no training, no saving).
    import torch

    sd = torch.load(model_path, map_location="cpu", weights_only=True)
    in_proj = sd.get("input_projection.weight")
    classifier = sd.get("classifier.weight")
    input_dim = int(in_proj.shape[1]) if in_proj is not None else None
    head_width = int(classifier.shape[0]) if classifier is not None else None
    hidden_dim = int(in_proj.shape[0]) if in_proj is not None else None
    param_count = int(sum(v.numel() for v in sd.values()))

    snapshot = {
        "reference_id": "BASELINE_PRODUCTION_REFERENCE",
        "captured_at": datetime.now(UTC).isoformat(),
        "git_revision": git_revision,
        "architecture": "LEGACY_SCALPNET_V1 (ScalpNet, dual-path 2D/3D, 4-logit head)",
        "artifact": {
            "path": str(model_path),
            "sha256": sha256_file(model_path),
            "scaler_sha256": sha256_file(scaler_path) if scaler_path.exists() else "",
            "meta_sha256": sha256_file(meta_path) if meta_path.exists() else "",
        },
        "input_dimension": input_dim,
        "head_width": head_width,
        "hidden_dim": hidden_dim,
        "parameter_count": param_count,
        "class_order_head": CLASS_ORDER_4LOGIT,
        "label_mapping_trained": LABEL_MAPPING_3CLASS,
        "trained_class_count": int(meta.get("num_classes", 3)),
        "feature_schema_id": meta.get("feature_schema_id"),
        "feature_schema_dimension": meta.get("feature_schema_dimension"),
        "training_metadata": {
            k: meta.get(k)
            for k in (
                "train_ratio",
                "num_folds",
                "purge_gap_bars",
                "embargo_bars",
                "epochs_per_fold",
                "learning_rate",
                "batch_size",
                "active_class_boost",
                "seed",
                "device_at_training",
                "use_feature_scaling",
                "clip_features_min",
                "clip_features_max",
            )
        },
        "isolation": {
            "mutated": False,
            "note": "read-only hash/metadata probe; champion bytes untouched",
        },
    }
    out_dir = Path("artifacts/models/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "baseline_production_reference.json"
    out.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
    snapshot["report_path"] = str(out)
    return snapshot
