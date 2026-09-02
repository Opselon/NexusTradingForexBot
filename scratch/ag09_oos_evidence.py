"""AGENT-09: OOS + calibration + robustness evidence for task5_abc_C_v1 (the
real 70D candidate from the fair A/B/C benchmark). This is the honest
evidence chain: evaluate the EXISTING candidate on its held-out eval split,
compute calibration + robustness under perturbation. NO retraining, NO
tuning after OOS observation (OOS LOCK — brief §16).
"""

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, "src")

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.validation import (
    compute_calibration,
)

MODEL = "task5_abc_C_v1"
DATASET = "ds_task5_real70d_2500"

store = ArtifactStore()
man = store.read_model_manifest(MODEL)
ds_frame = store.read_dataset(DATASET)
print(
    "model:", MODEL, "schema:", man.get("feature_schema_id"), "dim:", man.get("feature_dimension")
)
print("dataset rows:", ds_frame.height)

# ---- held-out eval split (OOS LOCK: only is_eval_sample rows) ----
eval_mask = ds_frame["is_eval_sample"].to_numpy().astype(bool)
n_eval = int(eval_mask.sum())
print("eval rows:", n_eval)
feat_cols = [c for c in ds_frame.columns if c.startswith("feat_")]
X_eval = ds_frame.filter(pl.col("is_eval_sample")).select(feat_cols).to_numpy().astype(np.float32)
y_eval = ds_frame.filter(pl.col("is_eval_sample"))["label"].to_numpy().astype(np.int64)

# ---- load the model + scaler (artifact integrity first) ----
import torch  # noqa: E402

from nexus_scalp.model_generation.model_factory import ModelFactory  # noqa: E402

weights_path = store.model_weights_path(MODEL)
scaler_path = store.model_scaler_path(MODEL)
state = torch.load(weights_path, map_location="cpu", weights_only=False)
scaler = np.load(scaler_path)
mean = scaler["mean"].astype(np.float32)
std = scaler["std"].astype(np.float32)

arch = man.get("architecture_id", "LEGACY_SCALPNET_V1")
model = ModelFactory().build(
    architecture=arch,
    num_classes=4,
    parameters={"input_dim": man.get("feature_dimension", 70)},
)
model.load_state_dict(state)
model.eval()

X_scaled = (X_eval - mean) / np.where(std < 1e-8, 1.0, std)
with torch.inference_mode():
    logits = model(torch.from_numpy(X_scaled))
    probs_4 = torch.softmax(logits, dim=1).numpy()
# 3-class probabilities (drop WAIT head if 4-wide; label space is 3)
if probs_4.shape[1] == 4:
    probs = probs_4[:, :3]
    probs = probs / probs.sum(axis=1, keepdims=True)
else:
    probs = probs_4
preds = np.argmax(probs, axis=1)

# ---- OOS metrics ----
from nexus_scalp.model_generation.validation import confusion_and_class_metrics  # noqa: E402

cm = confusion_and_class_metrics(y_eval, preds)
acc = float(np.mean(preds == y_eval))
bal_acc = float(
    (np.array([np.mean(preds[y_eval == c] == c) for c in (0, 1, 2) if (y_eval == c).any()])).mean()
)

# ---- calibration ----
cal = compute_calibration(probs, y_eval)
# Brier
brier = float(np.mean(np.sum((probs - np.eye(3)[y_eval]) ** 2, axis=1)))

oos_result = {
    "model_id": MODEL,
    "dataset_id": DATASET,
    "oos_rows": int(n_eval),
    "oos_accuracy": round(acc, 4),
    "oos_balanced_accuracy": round(bal_acc, 4),
    "macro_f1": cm.get("macro_f1"),
    "per_class": cm.get("per_class_metrics"),
    "confusion_matrix": cm.get("matrix") or cm.get("confusion_matrix"),
    "brier": round(brier, 4),
    "ece": cal.get("ece"),
    "well_calibrated": cal.get("well_calibrated"),
    "calibration_detail": cal,
    "label_counts": {
        str(k): int(v) for k, v in zip(*np.unique(y_eval, return_counts=True), strict=False)
    },
    "verdict_note": "OOS LOCKED: no tuning after this observation",
}

# ---- robustness: feature perturbation (spread/slippage noise) ----
rng = np.random.default_rng(42)
noise_levels = {"none": 0.0, "small": 0.01, "medium": 0.03, "large": 0.05}
robustness = {}
for name, level in noise_levels.items():
    X_p = X_scaled + rng.normal(0.0, level, X_scaled.shape)
    with torch.inference_mode():
        lp = model(torch.from_numpy(X_p.astype(np.float32)))
        pp = torch.softmax(lp, dim=1).numpy()
    if pp.shape[1] == 4:
        pp = pp[:, :3]
        pp = pp / pp.sum(axis=1, keepdims=True)
    pr = np.argmax(pp, axis=1)
    robustness[name] = {
        "accuracy": round(float(np.mean(pr == y_eval)), 4),
        "macro_f1": confusion_and_class_metrics(y_eval, pr).get("macro_f1"),
        "delta_acc": round(float(np.mean(pr == y_eval)) - acc, 4),
    }

result = {
    "oos": oos_result,
    "robustness_sweep": robustness,
    "note": "perturbation on SCALED features (spread/slippage proxy); OOS lock respected",
}
out = Path("artifacts/validation/70d_oos_results.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
print(json.dumps(result, indent=2, default=str)[:2500])
