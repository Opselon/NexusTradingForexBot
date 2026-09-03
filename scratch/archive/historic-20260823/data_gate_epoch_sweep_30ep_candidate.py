"""Epoch-sweep for the baseline candidate (same recipe, more epochs).

Trains cand_data_gate_v1 with 30 epochs (same focal+oversample recipe) and
reports test-split metrics. Tests whether the class-separation signal is
fundamentally learnable or the 10-epoch budget was the bottleneck.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import numpy as np
import polars as pl
import torch

from nexus_scalp.model_generation import (
    ArtifactStore,
    CandidateTrainer,
    ExperimentFactory,
    LocalModelRuntime,
    default_artifact_root,
)
from nexus_scalp.model_generation.validation import confusion_and_class_metrics


def rebuild_split(
    frame: pl.DataFrame, train_ratio: float = 0.70, val_ratio: float = 0.15
) -> pl.DataFrame:
    frame = frame.sort("timestamp")
    n = frame.height
    train_n = int(n * train_ratio)
    val_n = int(n * val_ratio)
    splits = pl.Series(["train"] * train_n + ["val"] * val_n + ["test"] * (n - train_n - val_n))
    return frame.with_columns(splits.alias("_split"))


def main() -> int:
    store = ArtifactStore(default_artifact_root())
    frame = store.read_dataset("ds_cb30f87520e9e6a4")
    frame = rebuild_split(frame)
    exp = ExperimentFactory(store=store).load("exp_baseline_scalpnet_v1_1f3cffdb")

    report = {}
    # sweep epochs: 20, 30
    for ep in (20, 30):
        mid = f"cand_data_gate_ep{ep}"
        res = CandidateTrainer(store=store).train_candidate(exp, frame, model_id=mid, epochs=ep)
        if res["status"] != "COMPLETED":
            report[f"ep{ep}"] = {"status": "FAILED", "error": res.get("error")}
            print(f"ep{ep}: FAILED {res.get('error')}")
            continue
        # test-split metrics
        test = frame.filter(pl.col("_split") == "test")
        labels_test = test["label"].to_numpy().astype(np.int64)
        rt = LocalModelRuntime(store=store)
        rt.load(mid)
        X_test = test.select([f"feat_{i}" for i in range(50)]).to_numpy().astype(np.float32)
        mean, std = store.read_scaler(mid)
        X_scaled = (X_test - mean) / np.where(std < 1e-8, 1.0, std)
        with torch.inference_mode():
            logits = rt._model(torch.from_numpy(X_scaled))
            probs = torch.softmax(logits, dim=1).numpy()
        preds = probs.argmax(axis=1)
        cm = confusion_and_class_metrics(labels_test, preds, num_classes=3)

        # calibration
        from nexus_scalp.model_generation.validation import compute_calibration

        cal = compute_calibration(probs, labels_test)

        report[f"ep{ep}"] = {
            "val_accuracy": res.get("val_accuracy"),
            "benchmark": cm,
            "calibration": cal,
            "model_id": mid,
        }
        print(
            f"ep{ep}: val_acc={res.get('val_accuracy')} macro_f1={cm['macro_f1']} "
            f"acc={cm['accuracy']} ece={cal['ece']} well_cal={cal['well_calibrated']}"
        )
        print(f"   per_class: {json.dumps(cm['per_class'])}")

    Path("data/raw/gate_epoch_sweep.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
