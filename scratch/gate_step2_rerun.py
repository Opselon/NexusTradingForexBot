"""GATE STEP 2 RE-RUN — with the restored training loop (true post-fix numbers)."""

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
    ValidationFactory,
    default_artifact_root,
)
from nexus_scalp.model_generation.validation import confusion_and_class_metrics

DATASET_ID = "ds_cb30f87520e9e6a4"
EXPERIMENT_ID = "exp_baseline_scalpnet_v1_1f3cffdb"


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
    frame = rebuild_split(store.read_dataset(DATASET_ID))
    exp = ExperimentFactory(store=store).load(EXPERIMENT_ID)

    report: dict = {"dataset_id": DATASET_ID, "experiment_id": EXPERIMENT_ID}

    # --- train (real, with loop restored) ---
    mid = "cand_data_gate_v2"
    res = CandidateTrainer(store=store).train_candidate(exp, frame, model_id=mid, epochs=10)
    print("train:", res.get("status"), "val_acc:", res.get("val_accuracy"))
    if res["status"] != "COMPLETED":
        print("FAILED:", res)
        return 1
    report["training"] = {"model_id": mid, "val_accuracy": res.get("val_accuracy")}

    # --- test-split eval ---
    test = frame.filter(pl.col("_split") == "test")
    y = test["label"].to_numpy().astype(np.int64)
    rt = LocalModelRuntime(store=store)
    rt.load(mid)
    X = test.select([f"feat_{i}" for i in range(50)]).to_numpy().astype(np.float32)
    mean, std = store.read_scaler(mid)
    Xs = (X - mean) / np.where(std < 1e-8, 1.0, std)
    with torch.inference_mode():
        probs = torch.softmax(rt._model(torch.from_numpy(Xs)), dim=1).numpy()
    preds = probs.argmax(axis=1)
    print("pred dist:", np.bincount(preds, minlength=4).tolist())
    print("true dist:", np.bincount(y, minlength=4).tolist())

    cm = confusion_and_class_metrics(y, preds, num_classes=3)
    report["benchmark"] = cm
    print(
        "macro_f1:",
        cm["macro_f1"],
        "acc:",
        cm["accuracy"],
        "per_class:",
        json.dumps(cm["per_class"]),
    )

    # --- validation factory ---
    vr = ValidationFactory().validate(mid, EXPERIMENT_ID, test, probabilities=probs, labels=y)
    report["validation"] = vr.model_dump(mode="json")
    print("verdict:", vr.verdict, "passed:", vr.passed)
    for g in vr.gates:
        print("  gate:", g["gate"], g["passed"], g.get("reason", ""))

    report["verdict"] = "PASS" if vr.passed else "REJECTED"
    Path("data/raw/gate_step2_v2_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print("[report written: data/raw/gate_step2_v2_report.json]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
