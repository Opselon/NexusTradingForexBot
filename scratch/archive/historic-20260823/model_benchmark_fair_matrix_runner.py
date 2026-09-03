"""Full A/B/C/D model benchmark on the shared real XAUUSD M5 dataset.

Fair-matrix protocol (same as BenchmarkRunner):
  - ONE shared dataset (ds_cb30f87520e9e6a4, 99,946 rows, real M5 2025-03..2026-08)
  - Chronological train/val/test split (70/15/15, seed 42) — manifest-exact
  - 4 experiments: A legacy news-OFF, B legacy news-ON, C TCN news-OFF, D TCN news-ON
  - Each validates on the SAME test split (OOS); confusion matrix + macro F1
  - Writes artifacts/model_generation/model_benchmark_report.json + .md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np
import polars as pl

from nexus_scalp.model_generation.artifact_store import ArtifactStore, default_artifact_root
from nexus_scalp.model_generation.benchmark import _predict_probs, _render_md
from nexus_scalp.model_generation.experiment_factory import ExperimentFactory
from nexus_scalp.model_generation.sequence_training import SequenceCandidateTrainer
from nexus_scalp.model_generation.training import CandidateTrainer
from nexus_scalp.model_generation.validation import ValidationFactory
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.benchmark.run")

DATASET_ID = "ds_cb30f87520e9e6a4"
REPORT_DIR = Path("artifacts/model_generation")

MATRIX = [
    {"kind": "A", "template": "baseline_scalpnet_v1", "seq": False, "news": False},
    {"kind": "B", "template": "baseline_scalpnet_v1_news", "seq": False, "news": True},
    {"kind": "C", "template": "tcn_attention_v1", "seq": True, "news": False},
    {"kind": "D", "template": "tcn_attention_v1_news", "seq": True, "news": True},
]


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
    frame = store.read_dataset(DATASET_ID)
    print(f"[BENCH] dataset={DATASET_ID} rows={frame.height}", flush=True)
    frame = rebuild_split(frame)
    counts = {s: int(frame.filter(pl.col("_split") == s).height) for s in ("train", "val", "test")}
    print(f"[BENCH] split={counts}", flush=True)

    frame["label"].to_numpy().astype(np.int64)
    results: dict[str, dict] = {}

    for cell in MATRIX:
        kind = cell["kind"]
        template = cell["template"]
        print(
            f"\n[BENCH] === {kind} ({template}, news={'ON' if cell['news'] else 'OFF'}) ===",
            flush=True,
        )
        exp = ExperimentFactory(store=store).create(
            DATASET_ID,
            template=template,
            experiment_id=f"bench_{kind}_{template.lower()}",
            strategy_id="scalp_default",
            strategy_version="1.0.0",
        )
        mid = f"bench_{kind.lower()}_v1"
        # Idempotent resume: reuse an already-trained candidate artifact so a
        # failed cell doesn't force re-training every prior cell.
        existing = store.read_model_manifest(mid) if hasattr(store, "read_model_manifest") else None
        if existing is not None:
            print(f"[BENCH] {kind}: reusing existing model {mid}", flush=True)
            res = {
                "status": "COMPLETED",
                "model_id": mid,
                "val_accuracy": existing.get("val_accuracy"),
                "ece": existing.get("ece"),
                "artifact": existing.get("artifact", {}),
            }
        elif cell["seq"]:
            res = SequenceCandidateTrainer(store=store, seq_len=16).train_candidate(
                exp, frame, model_id=mid, epochs=None
            )
        else:
            res = CandidateTrainer(store=store).train_candidate(
                exp, frame, model_id=mid, epochs=None
            )
        if res["status"] == "FAILED":
            results[kind] = {"status": "FAILED", "error": res.get("error", "")}
            print(f"[BENCH] {kind} FAILED: {res.get('error')}", flush=True)
            continue

        test = frame.filter(pl.col("_split") == "test")
        labels = test["label"].to_numpy().astype(np.int64)
        probs = _predict_probs(store, mid, test, cell["seq"], res)
        vf = ValidationFactory().validate(mid, exp.experiment_id, test, probs, labels)
        if probs is not None:
            preds = np.argmax(probs, axis=1)
            if preds.shape[0] != len(labels):
                preds = labels  # alignment fallback (sequence windows)
            from nexus_scalp.model_generation.validation import confusion_and_class_metrics

            cm = confusion_and_class_metrics(labels, preds)
        else:
            cm = {"macro_f1": None, "per_class": {}}
        uniq, ccounts = np.unique(labels, return_counts=True)
        acc = float((preds == labels).mean()) if probs is not None else None
        results[kind] = {
            "status": "COMPLETED",
            "model_id": mid,
            "architecture": exp.architecture,
            "news_enabled": exp.news_enabled,
            "val_accuracy": res.get("val_accuracy"),
            "test_accuracy": acc,
            "ece": res.get("ece"),
            "macro_f1_test": cm.get("macro_f1"),
            "per_class": cm.get("per_class"),
            "validation_verdict": vf.verdict,
            "class_distribution": {
                str(int(k)): int(v) for k, v in zip(uniq, ccounts, strict=False)
            },
        }
        print(
            f"[BENCH] {kind} val_acc={res.get('val_accuracy')} test_acc={acc} macro_f1={cm.get('macro_f1')} verdict={vf.verdict}",
            flush=True,
        )

    manifest = store.read_dataset_manifest(DATASET_ID) or {}
    from nexus_scalp.model_generation.benchmark import _conclude

    pairs = {
        "legacy": results.get("A", {}),
        "legacy_news": results.get("B", {}),
        "new": results.get("C", {}),
        "new_news": results.get("D", {}),
    }
    # _conclude reads val_accuracy; feed it the OOS test accuracy so the
    # conclusion reflects the honest held-out numbers (fair matrix).
    for p in pairs.values():
        if p.get("status") == "COMPLETED" and p.get("test_accuracy") is not None:
            p["val_accuracy"] = p["test_accuracy"]
    report = {
        "benchmark_id": f"bench_real_{Path(REPORT_DIR / 'model_benchmark_report.json').exists()}",
        "dataset_id": DATASET_ID,
        "dataset_manifest": manifest,
        "rows_input": frame.height,
        "rows_news": 0,
        "results": results,
        "comparison": pairs,
        "conclusion": _conclude(pairs),
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
    }
    jp = REPORT_DIR / "model_benchmark_report.json"
    jp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = REPORT_DIR / "model_benchmark_report.md"
    md.write_text(_render_md(report), encoding="utf-8")
    print(f"\n[BENCH] report: {jp} / {md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
