"""Phase 0 / item 1,4,6: dataset identity + per-dimension feature statistics.

Read-only w.r.t. production artifacts. Writes JSON to this research dir.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

DS = "ds_70d_clean_m1_20260904"
OUT = REPO / "artifacts" / "research" / "phase0_20260921"
OUT.mkdir(parents=True, exist_ok=True)


def per_dim_stats(arr: np.ndarray, cols: list[str], tag: str) -> list[dict]:
    """min/max/mean/std/nonzero/nan/unique per dimension."""
    n, d = arr.shape
    out = []
    for i in range(d):
        col = arr[:, i]
        finite = col[np.isfinite(col)]
        out.append(
            {
                "dim": i,
                "column": cols[i],
                "min": round(float(np.min(finite)), 6) if finite.size else None,
                "max": round(float(np.max(finite)), 6) if finite.size else None,
                "mean": round(float(np.mean(finite)), 6) if finite.size else None,
                "std": round(float(np.std(finite)), 6) if finite.size else None,
                "nonzero": int(np.count_nonzero(col)),
                "nan": int(np.count_nonzero(~np.isfinite(col))),
                "unique": int(len(np.unique(finite))) if finite.size else 0,
                "n": int(n),
                "stage": tag,
            }
        )
    return out


def main() -> None:
    store_path = REPO / "artifacts" / "model_generation" / "datasets" / DS / "dataset.parquet"
    man_path = REPO / "artifacts" / "model_generation" / "datasets" / DS / "dataset_manifest.json"
    ver_path = REPO / "artifacts" / "model_generation" / "datasets" / DS / "verification.json"

    df = pl.read_parquet(store_path)
    man = json.loads(man_path.read_text(encoding="utf-8"))
    ver = json.loads(ver_path.read_text(encoding="utf-8"))

    feat_cols = [f"feat_{i}" for i in range(70)]
    assert df.shape[1] >= 71 and all(c in df.columns for c in feat_cols)

    # ---------- full frame stats (raw feature generation stage) ----------
    X_all = df.select(feat_cols).to_numpy().astype(np.float64)

    # trainable subset == what the trainer sees (mirror _filter_trainable_rows)
    trainable = df
    if "label_evaluated" in trainable.columns:
        trainable = trainable.filter(pl.col("label_evaluated"))
    if "is_purged" in trainable.columns:
        trainable = trainable.filter(~pl.col("is_purged"))
    X_trainable = trainable.select(feat_cols).to_numpy().astype(np.float64)

    # eval samples only (the label distribution the manifest reports)
    eval_mask = df["is_eval_sample"].to_list()
    if "is_purged" in df.columns:
        eval_mask = [e and not p for e, p in zip(eval_mask, df["is_purged"].to_list(), strict=True)]
    ev = df.filter(pl.Series(eval_mask))
    X_eval = ev.select(feat_cols).to_numpy().astype(np.float64)

    # chronological split mirrors the trainer: 70% train / 30% OOS holdout
    dts = df["timestamp"].to_list()
    order = np.argsort(np.array([t.timestamp() for t in dts]), kind="stable")
    cut = int(len(order) * 0.70)
    tr_idx, ho_idx = order[:cut], order[cut:]
    X_hold = X_all[ho_idx]
    y_hold_all = np.asarray(df["label"].to_list(), dtype=np.int64)[ho_idx]

    tr_rows = X_all[tr_idx]
    scaler_mean = tr_rows.mean(axis=0)
    scaler_std = np.maximum(tr_rows.std(axis=0), 1e-3)
    X_hold_scaled = np.clip((X_hold - scaler_mean) / scaler_std, -5.0, 5.0)
    X_tr_scaled = np.clip((tr_rows - scaler_mean) / scaler_std, -5.0, 5.0)

    stats_all = per_dim_stats(X_all, feat_cols, "raw_full")
    stats_tr = per_dim_stats(tr_rows, feat_cols, "raw_train70")
    stats_hold = per_dim_stats(X_hold, feat_cols, "raw_holdout30")
    stats_prescaler = per_dim_stats(X_hold, feat_cols, "prescaler_holdout")
    stats_postscaler = per_dim_stats(X_hold_scaled, feat_cols, "postscaler_holdout")
    stats_prodscaler = None

    # production scaler stage: apply the SHIPPED 70d_liquidity scaler to the
    # holdout raw features (read-only load)
    prod_npz = REPO / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity" / "model.scaler.npz"
    if prod_npz.exists():
        z = np.load(prod_npz)
        pm, ps = z["mean"].reshape(1, -1), z["std"].reshape(1, -1)
        X_prod = np.clip((X_hold - pm) / ps, -5.0, 5.0)
        stats_prodscaler = per_dim_stats(X_prod, feat_cols, "prod_scaler_holdout")
        prod_scaler_info = {
            "mean_50_59": [round(float(pm[0, i]), 8) for i in range(50, 60)],
            "std_50_59": [round(float(ps[0, i]), 8) for i in range(50, 60)],
            "mean_60_69": [round(float(pm[0, i]), 8) for i in range(60, 70)],
            "std_60_69": [round(float(ps[0, i]), 8) for i in range(60, 70)],
        }
    else:
        prod_scaler_info = None

    report = {
        "dataset_id": man.get("dataset_id"),
        "dataset_sha256": man.get("dataset_hash") or man.get("dataset_sha256"),
        "feature_schema_id": man.get("feature_schema_id"),
        "feature_schema_hash": man.get("feature_schema_hash"),
        "label_schema_id": man.get("label_schema_id"),
        "rows_total": int(df.height),
        "row_counts_manifest": man.get("row_counts"),
        "temporal_range": man.get("temporal_range"),
        "trainable_rows": int(trainable.height),
        "eval_rows_manifest": man.get("eval_rows"),
        "eval_rows_computed": int(ev.height),
        "sequence_windows": man.get("sequence_windows"),
        "label_distribution": man.get("label_distribution"),
        "label_distribution_all_rows": {
            str(k): int(v) for k, v in df["label"].value_counts().sort("label").iter_rows()
        },
        "label_distribution_holdout30": {
            str(int(k)): int(v) for k, v in zip(*np.unique(y_hold_all, return_counts=True), strict=True)
        },
        "purge_parameters": man.get("purge_parameters"),
        "contract": man.get("contract"),
        "source_bars": man.get("source_bars"),
        "seed": man.get("seed"),
        "news_schema_id": man.get("news_schema_id"),
        "news_data_range": man.get("news_data_range"),
        "news_columns_in_dataset": [c for c in df.columns if c.startswith("news_")],
        "verification_gates": ver.get("gates"),
        "split_used_here": {
            "scheme": "chronological 70/30 by timestamp (train head / holdout tail)",
            "train_rows": int(len(tr_idx)),
            "holdout_rows": int(len(ho_idx)),
            "train_ts_range": [str(df["timestamp"][int(tr_idx[0])]), str(df["timestamp"][int(tr_idx[-1])])],
            "holdout_ts_range": [str(df["timestamp"][int(ho_idx[0])]), str(df["timestamp"][int(ho_idx[-1])])],
        },
        "prod_scaler_info": prod_scaler_info,
        "per_dim": {
            "raw_full": stats_all,
            "raw_train70": stats_tr,
            "raw_holdout30": stats_hold,
            "prescaler_holdout": stats_prescaler,
            "postscaler_holdout": stats_postscaler,
            "prod_scaler_holdout": stats_prodscaler,
        },
        "news_block_summary_raw_full": [
            {
                "dim": s["dim"],
                "column": s["column"],
                "min": s["min"],
                "max": s["max"],
                "mean": s["mean"],
                "std": s["std"],
                "nonzero": s["nonzero"],
                "unique": s["unique"],
            }
            for s in stats_all[50:60]
        ],
        "liquidity_block_summary_raw_full": [
            {
                "dim": s["dim"],
                "column": s["column"],
                "min": s["min"],
                "max": s["max"],
                "mean": s["mean"],
                "std": s["std"],
                "nonzero": s["nonzero"],
                "unique": s["unique"],
            }
            for s in stats_all[60:70]
        ],
    }

    # news_* sidecar columns present in the artifact (item 3 trace evidence)
    news_side = {}
    for c in [c for c in df.columns if c.startswith("news_")]:
        s = df[c]
        if str(s.dtype) not in ("f64", "Float64", "f32", "Float32"):
            news_side[c] = {
                "dtype": str(s.dtype),
                "unique": int(s.n_unique()),
                "note": "non-numeric column (schema id stamp) — not part of the feature vector",
            }
            continue
        arr = s.to_numpy().astype(np.float64)
        news_side[c] = {
            "min": round(float(np.min(arr)), 6),
            "max": round(float(np.max(arr)), 6),
            "mean": round(float(np.mean(arr)), 6),
            "std": round(float(np.std(arr)), 6),
            "nonzero": int(np.count_nonzero(arr)),
            "unique": int(len(np.unique(arr))),
        }
    report["news_sidecar_columns_stats"] = news_side

    (OUT / "dataset_stats.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # console digest
    print("dataset_id", report["dataset_id"], "sha", report["dataset_sha256"])
    print("rows", report["rows_total"], "trainable", report["trainable_rows"], "eval", report["eval_rows_computed"])
    print("seq windows", report["sequence_windows"])
    print("labels(manifest)", report["label_distribution"])
    print("labels(all rows)", report["label_distribution_all_rows"])
    print("NEWS 50..59 raw full:")
    for s in stats_all[50:60]:
        print(
            f"  {s['dim']:>3} {s['column']:<28} min={s['min']} max={s['max']} "
            f"mean={s['mean']} std={s['std']} nz={s['nonzero']} uniq={s['unique']} nan={s['nan']}"
        )
    print("LIQ 60..69 raw full:")
    for s in stats_all[60:70]:
        print(
            f"  {s['dim']:>3} {s['column']:<28} min={s['min']} max={s['max']} "
            f"mean={s['mean']} std={s['std']} nz={s['nonzero']} uniq={s['unique']} nan={s['nan']}"
        )
    print("prod scaler 50..59 std:", prod_scaler_info["std_50_59"] if prod_scaler_info else None)
    print("written:", OUT / "dataset_stats.json")


if __name__ == "__main__":
    main()
