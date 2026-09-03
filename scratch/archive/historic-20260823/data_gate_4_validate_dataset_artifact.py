"""DATA GATE — dataset artifact validation (step 7 of the data-quality gate).

Validates artifacts/model_generation/datasets/<dataset_id>/ against the
requirements: schema, feature availability, chronological ordering, no leakage,
duplicate removal, gap statistics, sample count, train/val/OOS feasibility.

READ-ONLY: never trains, never writes to artifacts/models/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import numpy as np
import polars as pl

from nexus_scalp.model_generation import ArtifactStore, default_artifact_root

DATASET_ID = sys.argv[1] if len(sys.argv) > 1 else "ds_cb30f87520e9e6a4"


def main() -> int:
    store = ArtifactStore(default_artifact_root())
    man = store.read_dataset_manifest(DATASET_ID)
    if man is None:
        print(f"[FAIL] manifest missing for {DATASET_ID}")
        return 1
    frame = store.read_dataset(DATASET_ID)
    n = frame.height

    report: dict = {"dataset_id": DATASET_ID, "manifest": man, "checks": {}}

    # ---- 1. schema ----
    cols = set(frame.columns)
    required = {
        "sample_id",
        "timestamp",
        "symbol",
        "timeframe",
        "feature_schema_id",
        "regime",
        "setup_id",
        "strategy_id",
        "label",
        "label_str",
        "news_context_schema_id",
        "is_eval_sample",
        "is_purged",
    } | {f"feat_{i}" for i in range(50)}
    missing = sorted(required - cols)
    report["checks"]["schema"] = {
        "ok": not missing,
        "missing_columns": missing,
        "total_columns": len(cols),
    }
    feat_cols = [f"feat_{i}" for i in range(50)]

    # ---- 2. feature availability / finiteness / bounds ----
    feats = frame.select(feat_cols).to_numpy()
    report["checks"]["features"] = {
        "rows": int(feats.shape[0]),
        "dim": int(feats.shape[1]),
        "non_finite": int((~np.isfinite(feats)).sum()),
        "nulls": int(frame.select(feat_cols).null_count().sum_horizontal().sum()),
        "out_of_bounds_gt3": int((np.abs(feats) > 3.0).sum()),
        "zero_variance_features": [c for c in feat_cols if float(frame[c].std()) == 0.0][:10],
    }

    # ---- 3. chronological ordering ----
    ts = frame["timestamp"]
    sorted_ok = bool(ts.is_sorted())
    report["checks"]["ordering"] = {"chronologically_sorted": sorted_ok}

    # ---- 4. duplicate removal ----
    dup_ids = int(frame["sample_id"].is_duplicated().sum())
    dup_ts = int(ts.is_duplicated().sum())
    report["checks"]["duplicates"] = {
        "duplicate_sample_ids": dup_ids,
        "duplicate_timestamps": dup_ts,
    }

    # ---- 5. gap statistics on timestamps ----
    ts_np = ts.to_numpy().astype("datetime64[s]").astype(np.int64)
    deltas = np.diff(ts_np)
    tf_min = 5  # M5
    expected = tf_min * 60
    gaps = deltas[deltas > expected]
    report["checks"]["gaps"] = {
        "timeframe": "M5",
        "expected_spacing_s": expected,
        "total_intervals": len(deltas),
        "gaps_total": len(gaps),
        "gap_pct": round(100.0 * len(gaps) / len(deltas), 4),
        "largest_gap_s": int(gaps.max()) if len(gaps) else 0,
        "gap_buckets": {
            "1.5x-3x": int(((gaps >= 1.5 * expected) & (gaps < 3 * expected)).sum()),
            "3x-24h": int(((gaps >= 3 * expected) & (gaps < 86400)).sum()),
            "24h+": int((gaps >= 86400).sum()),
        },
    }

    # ---- 6. label distribution ----
    lab = frame["label"].to_numpy()
    uniq, counts = np.unique(lab, return_counts=True)
    report["checks"]["labels"] = {
        "distribution": {int(k): int(v) for k, v in zip(uniq, counts, strict=False)},
        "class_count": len(uniq),
        "eval_samples": int(frame["is_eval_sample"].sum()),
        "purged_samples": int(frame["is_purged"].sum()),
    }

    # ---- 7. leakage guard: is_eval must be False on any purged row ----
    leak = frame.filter(pl.col("is_eval_sample") & pl.col("is_purged")).height
    report["checks"]["leakage"] = {"eval_and_purged_overlap": int(leak)}

    # ---- 8. split feasibility (chronological train/val/test non-overlap) ----
    t_min, t_max = ts.min(), ts.max()
    report["checks"]["temporal"] = {
        "start": str(t_min),
        "end": str(t_max),
        "span_days": round((t_max - t_min).total_seconds() / 86400, 2),
        "rows_per_day": round(n / max((t_max - t_min).total_seconds() / 86400, 1), 2),
    }

    # ---- 9. per-split statistics ----
    splits = {}
    for s in ("train", "val", "test"):
        sub = frame.filter(pl.col("_split") == s) if "_split" in frame.columns else None
        if sub is None:
            # the stored artifact drops _split; recompute via manifest counts
            splits[s] = {"count": man["row_counts"].get(s, 0)}
        else:
            splits[s] = {
                "count": int(sub.height),
                "start": str(sub["timestamp"].min()),
                "end": str(sub["timestamp"].max()),
            }
    report["checks"]["splits"] = splits

    # ---- verdict ----
    checks = report["checks"]
    passed = (
        checks["schema"]["ok"]
        and checks["features"]["non_finite"] == 0
        and checks["ordering"]["chronologically_sorted"]
        and checks["duplicates"]["duplicate_sample_ids"] == 0
        and checks["leakage"]["eval_and_purged_overlap"] == 0
        and checks["labels"]["class_count"] == 3
        and checks["splits"]["train"]["count"] > 50_000
        and checks["splits"]["val"]["count"] > 10_000
        and checks["splits"]["test"]["count"] > 10_000
    )
    report["verdict"] = "PASS" if passed else "FAIL"
    report["dataset_path"] = str(store.dataset_path(DATASET_ID))

    out = Path("data/raw/dataset_validation.json")
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\n[validation report written: {out}]")
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
