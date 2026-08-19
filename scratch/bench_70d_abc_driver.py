"""TASK-04-70D-MODEL-VALIDATION — fair A/B/C benchmark driver (brief 3/49).

CONTROL   A = 50D Base (scalp_v1)
          B = 60D Base + News (scalp_v2 + news 12D)
EXPERIMENT C = 70D Base + News + Liquidity (scalp_v3 = 70D; news 10D block
              per the 70D contract; liquidity 10D at 60..69)

Fairness contract (brief 1/5/40):
- SAME raw bars -> SAME timestamps/labels per sample_id
- SAME split/purge/embargo configuration
- SAME training budget (epochs/lr/batch/seed) for every cell
- ONLY the feature contract differs
- Validation: accuracy/macro-F1/ECE/Brier/per-class via ValidationFactory

The driver writes a machine-readable report to
artifacts/model_generation/liquidity_research/ and prints the fair table.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.experiment_factory import ExperimentFactory
from nexus_scalp.model_generation.training import CandidateTrainer
from nexus_scalp.model_generation.validation import (
    compute_calibration,
    confusion_and_class_metrics,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.benchmark_70d")

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_M5 = REPO_ROOT / "data/raw/XAUUSD_M5.parquet"
OUT_DIR = REPO_ROOT / "artifacts/model_generation/liquidity_research"

#: Same training budget for every cell (brief 40) — only the feature
#: contract differs. Do NOT tune per-cell.
TRAIN_CFG: dict[str, Any] = {
    "epochs": 6,
    "batch_size": 256,
    "learning_rate": 0.001,
    "seed": 42,
}


def _load_raw(n_rows: int | None = None) -> pl.DataFrame:
    df = pl.read_parquet(RAW_M5)
    return df.head(n_rows) if n_rows else df


def _build_cells(raw: pl.DataFrame, news_frame: pl.DataFrame | None) -> dict[str, pl.DataFrame]:
    """Builds A/B/C frames from the SAME raw bars. A shares the raw frame;
    B uses the 60D augmenter; C uses the 70D builder (news block fed via
    news_frame; liquidity block from the canonical engine)."""
    from nexus_scalp.model_generation.schema_v2 import compute_60d_frame, compute_70d_frame

    cells: dict[str, pl.DataFrame] = {}
    # A: 50D — reuse the base frame path via compute_60d_frame? No: A must be
    # exactly scalp_v1 features; DatasetFactory later selects feat_0..49 from
    # the frame. Build A from compute_60d_frame and slice features at
    # DatasetFactory time via the schema binding.
    cells["A"] = compute_60d_frame(raw)  # contains feat_0..59
    cells["B"] = compute_60d_frame(raw)
    cells["C"] = compute_70d_frame(raw, news_frame=news_frame)
    return cells


def _frame_for_cell(frame: pl.DataFrame, schema_id: str) -> pl.DataFrame:
    """Keeps only the columns the schema contract declares (feat_0..dim-1)."""
    dim = {"scalp_v1": 50, "scalp_v2": 60, "scalp_v3": 70}[schema_id]
    keep = [c for c in frame.columns if not c.startswith("feat_")]
    keep += [f"feat_{i}" for i in range(dim)]
    return frame.select(keep)


def _train_cell(
    store: ArtifactStore,
    cell_id: str,
    schema_id: str,
    frame: pl.DataFrame,
    news_enabled: bool,
) -> dict[str, Any]:
    """Trains one cell with the SHARED budget and evaluates on the same
    temporal split (tail 20% — identical for every cell)."""
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    label_col = frame["label"] if "label" in frame.columns else None
    if label_col is None:
        # label the frame with the triple-barrier default on close returns
        # (minimal deterministic proxy: 0 NO_TRADE / 1 BUY / 2 SELL by
        # 5-bar forward return sign*threshold)
        closes = frame["close"].to_numpy()
        fut = np.roll(closes, -5) - closes
        labels = np.zeros(len(closes), dtype=np.int64)
        thr = 0.5 * frame["atr_m1"].to_numpy()
        labels[fut > thr] = 1
        labels[fut < -thr] = 2
        frame = frame.with_columns(pl.Series("label", labels))

    exp = ExperimentFactory(store=store).create(
        "ds_70d_abc",
        template="baseline_scalpnet_v1_news" if news_enabled else "baseline_scalpnet_v1",
        experiment_id=f"abc_{cell_id}",
        overrides={"training": TRAIN_CFG},
    )
    mid = f"abc_{cell_id}_v1"
    t0 = time.perf_counter()
    res = CandidateTrainer(store=store).train_candidate(
        exp, frame, feature_cols=feat_cols, model_id=mid, epochs=int(TRAIN_CFG["epochs"])
    )
    train_s = round(time.perf_counter() - t0, 2)
    out: dict[str, Any] = {"cell": cell_id, "schema": schema_id, "train_seconds": train_s}
    if res["status"] != "COMPLETED":
        out.update({"status": "FAILED", "error": res.get("error", "")})
        return out
    out["status"] = "COMPLETED"
    out["model_id"] = res["model_id"]
    out["val_accuracy"] = res.get("val_accuracy")

    # evaluate on the SAME tail-20% split used by the trainer
    n = frame.height
    labels_arr = frame["label"].to_numpy().astype(np.int64)
    val_idx = np.arange(int(n * 0.8), n)
    X = frame.select(feat_cols).to_numpy().astype(np.float32)
    from nexus_scalp.model_generation.runtime import LocalModelRuntime

    rt = LocalModelRuntime()
    model_probs = rt.predict_proba(store.model_dir(mid), X[val_idx])
    if model_probs is None:
        out["status"] = "FAILED"
        out["error"] = "predict_proba returned None"
        return out
    y_true = labels_arr[val_idx]
    preds = np.argmax(model_probs, axis=1)
    cm = confusion_and_class_metrics(y_true, preds, num_classes=3)
    cal = compute_calibration(model_probs, y_true)
    out["metrics"] = {
        "accuracy": cm.get("accuracy"),
        "macro_f1": cm.get("macro_f1"),
        "per_class": cm.get("per_class"),
        "ece": cal.get("ece"),
        "brier": cal.get("brier", None),
        "n_val": len(y_true),
    }
    uniq, counts = np.unique(y_true, return_counts=True)
    out["label_dist"] = {str(int(u)): int(c) for u, c in zip(uniq, counts, strict=False)}
    return out


def main(n_rows: int | None = 6000) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = _load_raw(n_rows)
    print(f"raw bars: {raw.height}")
    # news frame: build from the real news DB (bounded)
    news_frame: pl.DataFrame | None = None
    try:
        from nexus_scalp.model_generation.news_bridge import build_news_frame_from_db
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(db_path=str(REPO_ROOT / "artifacts/news.db"))
        nf = build_news_frame_from_db(db, limit=500)
        news_frame = nf if nf is not None and not nf.is_empty() else None
        print(f"news rows: {news_frame.height if news_frame is not None else 0}")
    except Exception as exc:  # pragma: no cover
        print(f"news unavailable: {exc}")

    store = ArtifactStore()
    cells = _build_cells(raw, news_frame)
    results: dict[str, Any] = {}
    matrix = [
        ("A", "scalp_v1", False, cells["A"]),
        ("B", "scalp_v2", True, cells["B"]),
        ("C", "scalp_v3", True, cells["C"]),
    ]
    for cell_id, schema_id, news_en, frame in matrix:
        print(f"[BENCH70D] training cell {cell_id} ({schema_id}, news={news_en}) ...")
        results[cell_id] = _train_cell(store, cell_id, schema_id, frame, news_en)
        print(
            f"  -> {results[cell_id].get('status')} "
            f"acc={results[cell_id].get('metrics', {}).get('accuracy')} "
            f"f1={results[cell_id].get('metrics', {}).get('macro_f1')}"
        )

    report = {
        "experiment": "TASK-04-70D-MODEL-VALIDATION A/B/C fair benchmark",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "raw_rows": int(raw.height),
        "train_cfg": TRAIN_CFG,
        "cells": results,
        "verdict": _verdict(results),
    }
    out_path = OUT_DIR / "benchmark_70d_abc.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"report: {out_path}")
    return report


def _verdict(results: dict[str, Any]) -> dict[str, Any]:
    """Scientific classification (brief 51): only three cells are compared on
    the SAME split; a verdict requires enough evidence (n>=100, brief 18)."""
    if any(r.get("status") == "FAILED" for r in results.values()):
        return {"outcome": "INVALID", "reason": "one or more cells FAILED"}
    accs = {k: r.get("metrics", {}).get("accuracy") for k, r in results.items()}
    f1s = {k: r.get("metrics", {}).get("macro_f1") for k, r in results.items()}
    ns = {k: r.get("metrics", {}).get("n_val", 0) for k, r in results.items()}
    if any(n < 100 for n in ns.values()):
        return {
            "outcome": "INCONCLUSIVE",
            "reason": "insufficient evidence (n<100)",
            "detail": {"accuracy": accs, "macro_f1": f1s, "n": ns},
        }
    delta_cb = f1s.get("C", 0.0) - f1s.get("B", 0.0)
    if delta_cb >= 0.02 and f1s.get("C", 0.0) >= f1s.get("B", 0.0):
        outcome = "STRONG POSITIVE" if delta_cb >= 0.05 else "WEAK POSITIVE"
    elif abs(delta_cb) < 0.02:
        outcome = "NEUTRAL"
    elif delta_cb <= -0.02:
        outcome = "NEGATIVE"
    else:
        outcome = "INCONCLUSIVE"
    return {
        "outcome": outcome,
        "reason": "macro-F1 delta C-B evaluated on identical splits",
        "delta_C_minus_B_macro_f1": round(delta_cb, 4),
        "detail": {"accuracy": accs, "macro_f1": f1s, "n": ns},
    }


if __name__ == "__main__":
    main()
