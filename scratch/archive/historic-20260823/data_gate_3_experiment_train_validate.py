"""GATE STEP 2 — experiment -> training -> validation -> research feasibility.

Runs, in order:
  1. rebuild chronological 70/15/15 split on the dataset artifact (manifest-exact)
  2. candidate training via CandidateTrainer (baseline_scalpnet_v1, 10 epochs)
     — NEVER touches Champion; writes to artifacts/model_generation/models/
  3. ValidationFactory.validate -> verdict (label integrity / class collapse /
     regime / calibration / OOS accuracy)
  4. benchmark: confusion + per-class metrics on the test split
  5. research feasibility: build the ResearchDataset from the executable
     research path (experience ledger) and run backtest/walk-forward/OOS/
     robustness — REPORTING the real (small) ledger state honestly.

READ-ONLY for artifacts/models/scalp/XAUUSD/v1.0.0/model.pt (Champion).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import numpy as np
import polars as pl

from nexus_scalp.model_generation import (
    ArtifactStore,
    CandidateTrainer,
    ExperimentFactory,
    ValidationFactory,
    default_artifact_root,
)
from nexus_scalp.model_generation.validation import confusion_and_class_metrics

DATASET_ID = "ds_cb30f87520e9e6a4"
EXPERIMENT_ID = "exp_baseline_scalpnet_v1_1f3cffdb"


def rebuild_split(
    frame: pl.DataFrame, train_ratio: float = 0.70, val_ratio: float = 0.15
) -> pl.DataFrame:
    """Manifest-exact chronological split (same math as DatasetFactory._apply_split)."""
    frame = frame.sort("timestamp")
    n = frame.height
    train_n = int(n * train_ratio)
    val_n = int(n * val_ratio)
    splits = pl.Series(["train"] * train_n + ["val"] * val_n + ["test"] * (n - train_n - val_n))
    return frame.with_columns(splits.alias("_split"))


def main() -> int:
    store = ArtifactStore(default_artifact_root())
    report: dict = {"dataset_id": DATASET_ID, "experiment_id": EXPERIMENT_ID}

    # ---- 1. dataset + split ----
    frame = store.read_dataset(DATASET_ID)
    frame = rebuild_split(frame)
    report["split"] = {
        s: int(frame.filter(pl.col("_split") == s).height) for s in ("train", "val", "test")
    }
    print("split:", report["split"])

    # ---- 2. train candidate ----
    exp = ExperimentFactory(store=store).load(EXPERIMENT_ID)
    trainer = CandidateTrainer(store=store)
    res = trainer.train_candidate(exp, frame, model_id="cand_data_gate_v1")
    if res["status"] != "COMPLETED":
        print("TRAIN FAILED:", res)
        report["training"] = res
        report["verdict"] = "FAILED_TRAIN"
        Path("data/raw/gate_step2_report.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        return 1
    model_id = res["model_id"]
    report["training"] = {
        "status": res["status"],
        "model_id": model_id,
        "val_accuracy": res.get("val_accuracy"),
        "artifact": res.get("artifact"),
    }
    print("trained:", model_id, "val_acc:", res.get("val_accuracy"))

    # ---- 3. validation (test split = OOS) ----
    test = frame.filter(pl.col("_split") == "test")
    labels_test = test["label"].to_numpy().astype(np.int64)

    # produce probabilities via LocalModelRuntime
    from nexus_scalp.model_generation import LocalModelRuntime

    rt = LocalModelRuntime(store=store)
    rt.load(model_id)
    X_test = test.select([f"feat_{i}" for i in range(50)]).to_numpy().astype(np.float32)
    # scale with the artifact scaler (train-fitted, zero leakage)
    mean, std = store.read_scaler(model_id)
    X_scaled = (X_test - mean) / np.where(std < 1e-8, 1.0, std)
    import torch

    with torch.inference_mode():
        logits = rt._model(torch.from_numpy(X_scaled))
        probs = torch.softmax(logits, dim=1).numpy()
    preds = probs.argmax(axis=1)

    vf = ValidationFactory()
    vr = vf.validate(model_id, EXPERIMENT_ID, test, probabilities=probs, labels=labels_test)
    report["validation"] = vr.model_dump(mode="json")
    print("validation verdict:", vr.verdict, "passed:", vr.passed)
    for g in vr.gates:
        print("  gate:", g["gate"], "passed:", g["passed"], g.get("reason", ""))

    # ---- 4. per-class benchmark ----
    cm = confusion_and_class_metrics(labels_test, preds, num_classes=3)
    report["benchmark"] = cm
    print("benchmark:", json.dumps(cm, indent=2))

    # ---- 5. research feasibility (honest ledger state) ----
    try:
        from nexus_scalp.adapters.database.audit_repository import AuditRepository
        from nexus_scalp.experience.ledger import ExperienceLedger
        from nexus_scalp.research.backtest import BacktestEngine
        from nexus_scalp.research.dataset import ResearchDatasetBuilder
        from nexus_scalp.research.oos import OOSGate
        from nexus_scalp.research.robustness import RobustnessEngine
        from nexus_scalp.research.walkforward import WalkForwardEngine

        ledger = ExperienceLedger(AuditRepository())
        builder = ResearchDatasetBuilder(ledger)
        rds = builder.build()
        report["research_feasibility"] = {
            "samples": len(rds.samples),
            "note": "research dataset built from executed-trade ledger",
        }
        if len(rds.samples) < 30:
            report["research_feasibility"]["verdict"] = (
                f"INSUFFICIENT: {len(rds.samples)} executed experiences — "
                "walk-forward/OOS/robustness need >= 30+ (ideally 100+)"
            )
            print("research feasibility:", report["research_feasibility"])
        else:
            # bounded run: backtest + WF + OOS + robustness on the single strategy
            bt = BacktestEngine().run(rds, "scalp_default", "1.0.0", use_split=True)
            wf = WalkForwardEngine().validate(rds, "scalp_default", "1.0.0", n_splits=3)
            oos = OOSGate().evaluate(rds, "scalp_default", "1.0.0")
            rob = RobustnessEngine().evaluate(rds, "scalp_default", "1.0.0")
            report["research_feasibility"].update(
                {
                    "backtest": bt.model_dump(mode="json"),
                    "walkforward": wf.model_dump(mode="json"),
                    "oos": oos.model_dump(mode="json"),
                    "robustness": rob.model_dump(mode="json"),
                }
            )
            print("research run complete")
    except Exception as e:
        report["research_feasibility"] = {"error": str(e)}
        print("research feasibility error:", e)

    # ---- verdict ----
    report["verdict"] = "PASS" if vr.passed and res["status"] == "COMPLETED" else "FAIL"
    out = Path("data/raw/gate_step2_report.json")
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n[gate step2 report: {out}]")
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
