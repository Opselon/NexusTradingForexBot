"""AGENT-09: TRUE temporal OOS for the 70D candidate (OOS LOCK, brief 16).

The existing task5_abc_C_v1 was validated on an INTERSPERSED eval split —
not a held-out temporal OOS. Per the OOS-LOCK rule, a genuine OOS must be
the last temporal window NEVER seen by training. This script:
  1. Splits ds_task5_real70d_2500 at 2025-03-24 (train < that date).
  2. Retrains C (scalp_v3, feat_0..69) with the IDENTICAL benchmark
     hyperparameters (epochs=6, lr=1e-3, seed=42, CandidateTrainer) — NO
     tuning.
  3. Evaluates on the untouched tail (317 rows) — the OOS result.
  4. Records OOS artifact + metrics. Nothing after this may tune against it.
"""
import json
import sys
from pathlib import Path

import datetime as dt
import numpy as np
import polars as pl
import torch

sys.path.insert(0, "src")

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.experiment_factory import ExperimentConfig
from nexus_scalp.model_generation.sample_factory import SampleFactory
from nexus_scalp.model_generation.training import CandidateTrainer
from nexus_scalp.model_generation.validation import (
    ValidationFactory,
    compute_calibration,
    confusion_and_class_metrics,
)

store = ArtifactStore()
ds = store.read_dataset("ds_task5_real70d_2500")
CUTOFF = dt.datetime(2025, 3, 24, 0, 0, 0)
train_frame = ds.filter(pl.col("timestamp") < CUTOFF)
oos_frame = ds.filter(pl.col("timestamp") >= CUTOFF)
print("train rows:", train_frame.height, "OOS rows:", oos_frame.height)
feat_cols = [c for c in ds.columns if c.startswith("feat_")]
assert len(feat_cols) == 70

exp = ExperimentConfig(
    experiment_id="ag09_oos_C",
    architecture="LEGACY_SCALPNET_V1",
    class_count=3,
    seed=42,
    dataset_id="ds_task5_real70d_2500",
    strategy_id="scalp_default",
    strategy_version="1.0.0",
    news_enabled=False,
    training={
        "epochs": 6,
        "learning_rate": 1e-3,
        "batch_size": 256,
        "evidence": {
            "oos_artifact": "artifacts/validation/70d_oos_results.json",
            "robustness_artifact": "artifacts/validation/70d_robustness_results.json",
        },
    },
    architecture_parameters={"hidden_dim": 128, "num_heads": 4, "dropout_rate": 0.25},
)
trainer = CandidateTrainer(store=store)
res = trainer.train_candidate(exp, train_frame, model_id="ag09_oos_C_v1")
print("train:", res.get("status"), res.get("model_id"))
if res.get("status") != "COMPLETED":
    print("TRAIN FAILED:", res.get("error"))
    sys.exit(1)

# ---- evaluate on the OOS tail (never touched by training) ----
man = store.read_model_manifest("ag09_oos_C_v1")
state = torch.load(store.model_weights_path("ag09_oos_C_v1"), map_location="cpu", weights_only=False)
scaler = np.load(store.model_scaler_path("ag09_oos_C_v1"))
mean = scaler["mean"].astype(np.float32)
std = scaler["std"].astype(np.float32)

from nexus_scalp.model_generation.model_factory import ModelFactory
model = ModelFactory().build(
    architecture="LEGACY_SCALPNET_V1",
    num_classes=4,
    parameters={"input_dim": 70},
)
model.load_state_dict(state)
model.eval()

X_oos = oos_frame.select(feat_cols).to_numpy().astype(np.float32)
y_oos = oos_frame["label"].to_numpy().astype(np.int64)
X_s = (X_oos - mean) / np.where(std < 1e-8, 1.0, std)
with torch.inference_mode():
    lp = model(torch.from_numpy(X_s))
    pp = torch.softmax(lp, dim=1).numpy()
if pp.shape[1] == 4:
    pp = pp[:, :3]
    pp = pp / pp.sum(axis=1, keepdims=True)
preds = np.argmax(pp, axis=1)
acc = float(np.mean(preds == y_oos))
cm = confusion_and_class_metrics(y_oos, preds)
bal = float(np.mean([np.mean(preds[y_oos == c] == c) for c in (0, 1, 2) if (y_oos == c).any()]))
cal = compute_calibration(pp, y_oos)
brier = float(np.mean(np.sum((pp - np.eye(3)[y_oos]) ** 2, axis=1)))

result = {
    "model_id": "ag09_oos_C_v1",
    "oos_split": {"cutoff": str(CUTOFF), "train_rows": train_frame.height, "oos_rows": oos_frame.height},
    "hyperparameters": {"epochs": 6, "lr": 1e-3, "batch_size": 256, "seed": 42, "architecture": "LEGACY_SCALPNET_V1"},
    "train_status": res.get("status"),
    "artifact_hash": store.read_model_manifest("ag09_oos_C_v1").get("artifact_hash", ""),
    "results": {
        "accuracy": round(acc, 4),
        "balanced_accuracy": round(bal, 4),
        "macro_f1": cm.get("macro_f1"),
        "confusion_matrix": cm.get("matrix") or cm.get("confusion_matrix"),
        "ece": cal.get("ece"),
        "brier": round(brier, 4),
        "class_distribution": {str(k): int(v) for k, v in zip(*np.unique(y_oos, return_counts=True))},
    },
    "gate_comparison": {
        "oos_accuracy_floor_0_30": acc >= 0.30,
        "oos_macro_f1_floor_0_34": bool(cm.get("macro_f1", 0) > 0.34),
        "oos_balanced_acc_floor_0_34": bal > 0.34,
        "ece_floor_0_15": float(cal.get("ece", 1.0)) <= 0.15,
    },
    "verdict": "OOS LOCKED — no tuning after this observation",
}
out = Path("artifacts/validation/70d_oos_results.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
print(json.dumps(result, indent=2, default=str))