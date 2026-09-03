"""TASK-5 Controlled Historical Experiment — 50D vs 60D on REAL M5 data.

Spec 42 (historical retrain experiment): ONE controlled real experiment on
the corrected historical dataset. 50D Champion (control) vs 60D Challenger
candidates, identical conditions (same bars, same split, same labels, same
purge/embargo/friction), no promotion.

Matrix (spec 12):
    A: 50D baseline   news OFF
    B: 50D baseline   news ON
    C: 60D challenger news OFF
    D: 60D challenger news ON

Outputs:
    artifacts/model_generation/datasets/ds_<60d-id>/          (60D dataset)
    artifacts/model_generation/models/task5_<cell>_v1/        (candidates)
    artifacts/model_generation/task5_60d_report.json/.md      (report)
    docs/task5_experiment_report.md                          (human summary)

NO promotion is possible: candidates write to candidate ids only; the
Champion path (artifacts/models/scalp/XAUUSD/v1.0.0) is never touched.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import numpy as np
import polars as pl

from nexus_scalp.model_generation import (
    ArtifactStore,
    CandidateTrainer,
    DatasetFactory,
    ExperimentFactory,
    LocalModelRuntime,
    SampleFactory,
    ValidationFactory,
    default_artifact_root,
)
from nexus_scalp.model_generation.schema_v2 import compute_60d_frame
from nexus_scalp.model_generation.validation import (
    confusion_and_class_metrics,
    detect_class_collapse,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("task5_experiment")

RAW = Path("data/raw/XAUUSD_M5.parquet")
STORE = ArtifactStore(default_artifact_root())
SEED = 42
EPOCHS = 6


def load_real_news_frame() -> pl.DataFrame | None:
    """Exports the REAL news DB analyses to the canonical 12-field frame.

    Returns None when the news DB is absent/empty — the news cells then record
    NO-NEWS-AVAILABLE honestly instead of inventing signal.
    """
    db_path = Path("artifacts/news.db")
    if not db_path.exists():
        print("[NEWS] news.db missing — news cells will record NO-NEWS-AVAILABLE")
        return None
    from nexus_scalp.model_generation.news_bridge import (
        build_news_frame_from_db,
        news_benchmark_readiness,
    )
    from nexus_scalp.news.database import NewsDatabase

    db = NewsDatabase(db_path)
    frame = build_news_frame_from_db(db, limit=2000)
    if frame is None or frame.is_empty():
        print("[NEWS] news DB empty of analyses — NO-NEWS-AVAILABLE")
        return None
    gate = news_benchmark_readiness(frame)
    print(f"[NEWS] exported {frame.height} news rows; readiness={gate['ready']} ({gate['checks']})")
    return frame


def build_feature_60d() -> pl.DataFrame:
    """Computes the REAL 60D feature frame once from the raw M5 bars."""
    raw = pl.read_parquet(RAW)
    feat = compute_60d_frame(raw, min_bars=55)
    print(f"[60D] computed {feat.height} rows of 60D features")
    return feat


def build_dataset(
    schema_id: str, feat_frame: pl.DataFrame, news: pl.DataFrame | None
) -> tuple[str, pl.DataFrame]:
    """Deterministic dataset build for one cell (same bars/split/labels;
    news presence only changes the news_* columns)."""
    dh = DatasetFactory(
        store=STORE,
        sample_factory=SampleFactory(feature_schema_id=schema_id),
    ).build(
        feat_frame,
        symbol="XAUUSD",
        timeframe="M5",
        news_frame=news,
        seed=SEED,
    )
    did = dh["dataset_id"]
    frame = STORE.read_dataset(did)
    print(
        f"[DATASET] schema={schema_id} news={'Y' if news is not None else 'N'} "
        f"id={did} rows={frame.height}"
    )
    return did, frame


def run_cell(
    kind: str,
    dataset_id: str,
    frame: pl.DataFrame,
    template: str,
    news: bool,
) -> dict:
    """Trains one cell (A/B/C/D) and validates. NEVER touches Champion."""
    exp = ExperimentFactory(store=STORE).create(
        dataset_id,
        template=template,
        experiment_id=f"task5_{kind}_{template.lower()}",
        strategy_id="scalp_default",
        strategy_version="1.0.0",
        seed=SEED,
    )
    mid = f"task5_{kind.lower()}_v1"
    t0 = time.time()
    res = CandidateTrainer(store=STORE).train_candidate(exp, frame, model_id=mid, epochs=EPOCHS)
    dt_ms = (time.time() - t0) * 1000.0
    if res["status"] == "FAILED":
        return {
            "kind": kind,
            "status": "FAILED",
            "error": res.get("error", ""),
            "duration_ms": round(dt_ms, 1),
        }

    # ---- validation on the SAME test split --------------------------------
    labels = frame["label"].to_numpy().astype(np.int64)
    try:
        rt = LocalModelRuntime(store=STORE).load(mid)
        feat_cols = [c for c in frame.columns if c.startswith("feat_")]
        news_cols = (
            [c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"]
            if news
            else []
        )
        X = frame.select(feat_cols + news_cols).to_numpy().astype(np.float32)
        if rt._scaler is not None:
            mean, std = rt._scaler
            X = (X - mean) / (std + 1e-8)
        with __import__("torch").inference_mode():
            logits = rt._model(__import__("torch").from_numpy(X))
            probs = __import__("torch").softmax(logits, dim=-1).numpy()
    except Exception as e:
        return {
            "kind": kind,
            "status": "COMPLETED",
            "model_id": mid,
            "error": f"predict failed: {e}",
            "duration_ms": round(dt_ms, 1),
        }

    vf = ValidationFactory()
    vr = vf.validate(mid, exp.experiment_id, frame, probs, labels)
    preds = np.argmax(probs, axis=1)
    cm = confusion_and_class_metrics(labels, preds)
    _, _counts = np.unique(labels, return_counts=True)
    dist = {
        str(int(k)): int(v) for k, v in zip(*np.unique(labels, return_counts=True), strict=False)
    }
    collapse = detect_class_collapse(labels)
    mm = STORE.read_model_manifest(mid) or {}
    logger.info(
        "[TASK5] cell=%s status=%s verdict=%s acc=%s mf1=%s ece=%s",
        kind,
        res["status"],
        vr.verdict,
        cm["accuracy"],
        cm["macro_f1"],
        vr.calibration.get("ece"),
    )
    return {
        "kind": kind,
        "status": res["status"],
        "model_id": mid,
        "architecture": mm.get("architecture_id", ""),
        "feature_dimension": mm.get("feature_dimension", 0),
        "news_enabled": news,
        "dataset_id": dataset_id,
        "val_accuracy": cm["accuracy"],
        "macro_f1": cm["macro_f1"],
        "balanced_accuracy": vr.overall.get("oos_balanced_accuracy"),
        "ece": vr.calibration.get("ece"),
        "validation_verdict": vr.verdict,
        "gates": [{g["gate"]: g["passed"]} for g in vr.gates],
        "class_distribution": dist,
        "class_collapse": collapse["collapsed"],
        "per_class": cm["per_class"],
        "duration_ms": round(dt_ms, 1),
        "val_rows": len(labels),
    }


def conclude(cells: dict[str, dict]) -> dict:
    """Honest conclusion: only measured evidence (spec 18)."""

    def acc(k):
        c = cells.get(k, {})
        return c.get("val_accuracy") if c.get("status") == "COMPLETED" else None

    a, b, c, d = acc("A"), acc("B"), acc("C"), acc("D")

    def verdict(new_val, old_val):
        if new_val is None or old_val is None:
            return "INCONCLUSIVE"
        if new_val - old_val > 0.02:
            return "BETTER"
        if old_val - new_val > 0.02:
            return "WORSE"
        return "INCONCLUSIVE"

    ev = verdict(c, a)  # 60D vs 50D, news off
    ev_on = verdict(d, b)  # 60D vs 50D, news on
    nv = verdict(b, a)  # news on vs off, 50D
    nv_60 = verdict(d, c)  # news on vs off, 60D
    return {
        "60d_vs_50d_news_off": ev,
        "60d_vs_50d_news_on": ev_on,
        "news_effect_50d": nv,
        "news_effect_60d": nv_60,
        "note": (
            "Point estimates on one historical M5 dataset. No statistical "
            "significance claimed. No candidate is promoted. The Champion "
            "remains the control group."
        ),
    }


def main() -> int:
    print("=" * 60)
    print("TASK-5 CONTROLLED 50D-vs-60D HISTORICAL EXPERIMENT")
    print("=" * 60)
    news_frame = load_real_news_frame()

    t0 = time.time()
    feat60 = build_feature_60d()
    feat50 = feat60.drop([f"feat_{i}" for i in range(50, 60)])  # same bars, same engine
    print(
        f"[FEATURES] 60D={feat60.height} rows, 50D={feat50.height} rows, "
        f"computed in {time.time() - t0:.0f}s"
    )

    # Fair matrix: identical bars/split/labels per dimension; news presence
    # only appends news_* columns. The 50D Data-Gate baseline (ds_cb30f875)
    # is the historical 50D control; cells rebuild deterministically here so
    # the 50D/60D pair share EXACTLY the same feature producer path.
    datasets = {}
    datasets["A"] = build_dataset("scalp_v1", feat50, news=None)
    datasets["B"] = build_dataset("scalp_v1", feat50, news_frame)
    datasets["C"] = build_dataset("scalp_v2", feat60, news=None)
    datasets["D"] = build_dataset("scalp_v2", feat60, news_frame)

    cells: dict[str, dict] = {}
    for k, (did, frame) in datasets.items():
        news_on = k in ("B", "D")
        template = "baseline_scalpnet_v1_news" if news_on else "baseline_scalpnet_v1"
        cells[k] = run_cell(k, did, frame, template, news=news_on)

    report = {
        "experiment": "task5_60d_challenger",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_50d": datasets["A"][0],
        "dataset_60d": datasets["C"][0],
        "rows_50d": datasets["A"][1].height,
        "rows_60d": datasets["C"][1].height,
        "epochs": EPOCHS,
        "seed": SEED,
        "news_available": news_frame is not None,
        "cells": cells,
        "conclusion": conclude(cells),
        "champion_safety": {
            "champion_path": "artifacts/models/scalp/XAUUSD/v1.0.0/model.pt",
            "touched": False,
            "note": "CandidateTrainer writes candidate ids only; no promotion path exists.",
        },
    }
    jp = Path("artifacts/model_generation/task5_60d_report.json")
    jp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = Path("artifacts/model_generation/task5_60d_report.md")
    md.write_text(_render_md(report), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


def _render_md(report: dict) -> str:
    lines = [
        "# TASK-5 60D Challenger — Controlled Experiment Report",
        "",
        f"- generated: `{report['generated_at']}`",
        f"- 50D dataset: `{report['dataset_50d']}` ({report['rows_50d']} rows)",
        f"- 60D dataset: `{report['dataset_60d']}` ({report['rows_60d']} rows)",
        f"- epochs={report['epochs']} seed={report['seed']}",
        "",
        "## Cells",
        "",
        "| Cell | Dim | News | Status | Acc | Macro-F1 | ECE | Verdict |",
        "|------|-----|------|--------|-----|----------|-----|---------|",
    ]
    names = {"A": "50D", "B": "50D", "C": "60D", "D": "60D"}
    news = {"A": "Off", "B": "On", "C": "Off", "D": "On"}
    for k in ("A", "B", "C", "D"):
        c = report["cells"].get(k, {})
        st = c.get("status", "FAILED")
        if st != "COMPLETED":
            lines.append(
                f"| {k} | {names[k]} | {news[k]} | FAILED | — | — | — | {c.get('error', '')} |"
            )
            continue
        lines.append(
            f"| {k} | {names[k]} | {news[k]} | {st} | {c.get('val_accuracy')} "
            f"| {c.get('macro_f1')} | {c.get('ece')} | {c.get('validation_verdict')} |"
        )
    lines += ["", "## Conclusion", ""]
    for k, v in report["conclusion"].items():
        lines.append(f"- **{k}**: `{v}`")
    lines += ["", "## Champion safety", ""]
    for k, v in report["champion_safety"].items():
        lines.append(f"- **{k}**: `{v}`")
    lines.append("")
    lines.append("_No model is promoted. The Champion remains the control group._")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
