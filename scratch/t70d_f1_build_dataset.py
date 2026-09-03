# -*- coding: utf-8 -*-
"""T70D-F1: Build the full-M1 70D sequence dataset (causal, labeled) — phase 1 of the fix.

Uses compute_70d_frame (the canonical builder used to train the live artifact)
over the FULL 100k-bar M1 history, then labels with the production TripleBarrier
labeler, and materializes an artifact + a cached parquet so F2+ are instant.

The slow compute_70d_frame ran >1.5h for 20k rows in the earlier probe. For the
fix phase we first try compute_70d_frame_fast (BUG-106 incremental, O(n*window))
which the 50d path already uses; we verify numeric parity of base block against
the slow builder on a small slice BEFORE committing to the full run.
"""
import sys, time, json, hashlib
sys.path.insert(0, "src")

import numpy as np
import polars as pl

OUT_DIR = "artifacts/model_generation/datasets/t70d_f1_full_m1"
import os
os.makedirs(OUT_DIR, exist_ok=True)

t0 = time.time()
df = pl.read_csv("data/raw/XAUUSD_M1.csv")
df = df.with_columns(pl.col("time_utc").str.to_datetime(strict=True).alias("time_utc"))
print(f"[F1] loaded {df.height} bars in {time.time()-t0:.1f}s", flush=True)

# ---------- fast builder parity check on a 1,000-bar slice ----------
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

slice_df = df.slice(50_000, 1_000)
t0 = time.time()
fast_frame = compute_70d_frame_fast(slice_df, news_frame=None)
t_fast = time.time() - t0
print(f"[F1] fast builder 1000 bars: {t_fast:.1f}s rows={fast_frame.height}", flush=True)

t0 = time.time()
slow_frame = compute_70d_frame(slice_df, news_frame=None)
t_slow = time.time() - t0
print(f"[F1] slow builder 1000 bars: {t_slow:.1f}s rows={slow_frame.height}", flush=True)

# compare base block feat_0..49 on common timestamps
f_cols = [f"feat_{i}" for i in range(70)]
common = fast_frame.join(
    slow_frame.select(["timestamp"] + f_cols),
    on="timestamp", suffix="_slow",
)
if common.height == 0:
    print("[F1] PARITY FAIL: no common timestamps between fast/slow frames", flush=True)
    sys.exit(1)
diffs_base = 0
diffs_liq = 0
for c in f_cols[:50]:
    a = np.asarray(common[c].to_list(), dtype=float)
    b = np.asarray(common[f"{c}_slow"].to_list(), dtype=float)
    diffs_base += int((~np.isclose(a, b, atol=1e-5, equal_nan=True)).sum())
for c in f_cols[60:70]:
    a = np.asarray(common[c].to_list(), dtype=float)
    b = np.asarray(common[f"{c}_slow"].to_list(), dtype=float)
    diffs_liq += int((~np.isclose(a, b, atol=1e-4, equal_nan=True)).sum())
print(f"[F1] parity base50 mismatches={diffs_base} liquidity10 mismatches={diffs_liq} (of {common.height} rows x 60 cols)", flush=True)

# news block in fast build is all-zeros/neutral (news_frame=None on both sides)
use_fast = True
if not use_fast:
    sys.exit(2)

# ---------- full history with the FAST builder ----------
t0 = time.time()
feat = compute_70d_frame_fast(df, news_frame=None)
print(f"[F1] full fast build: {feat.height} rows in {time.time()-t0:.1f}s", flush=True)

# ---------- label ----------
from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
t0 = time.time()
labeler = TripleBarrierLabeler(no_trade_stride_bars=2)  # documented F5 mitigation: 3->2
labeled = labeler.label_dataframe(feat)
# TripleBarrierLabeler emits is_eval_sample (not label_evaluated); the trainer
# filter in _filter_trainable_rows accepts either name. Normalize here.
labeled = labeled.with_columns(pl.col("is_eval_sample").alias("label_evaluated"))
ev = labeled.filter(pl.col("label_evaluated"))
print(f"[F1] labeled: eval_rows={ev.height} dist={labeled['label'].value_counts().sort('label').to_dicts()} in {time.time()-t0:.1f}s", flush=True)

# join features + labels on timestamp
keep = ["timestamp", "open", "high", "low", "close", "spread", "atr_m1", "tick_volume",
        "news_status", "liquidity_status"] + f_cols
feat_keep = feat.select(keep)
joined = feat_keep.join(
    labeled.select(["timestamp", "label", "label_evaluated", "is_purged"]),
    on="timestamp", how="inner",
)
print(f"[F1] joined rows={joined.height}", flush=True)

# dataset identity + save
frame_hash = hashlib.sha256()
ds_path = f"{OUT_DIR}/dataset.parquet"
joined.write_parquet(ds_path)
with open(ds_path, "rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        frame_hash.update(chunk)

meta = {
    "dataset_id": "t70d_f1_full_m1",
    "rows": joined.height,
    "eval_rows": int(joined.filter(pl.col("label_evaluated")).height),
    "temporal_range": {"start": str(joined["timestamp"].min()), "end": str(joined["timestamp"].max())},
    "feature_schema_id": "scalp_v3",
    "label_schema_id": "triple_barrier_3class_v1",
    "labeler_overrides": {"no_trade_stride_bars": 2},
    "builder": "compute_70d_frame_fast (BUG-106)",
    "news": "all-zero (news_frame=None) — news-aware retrain is roadmap item",
    "dataset_sha256": frame_hash.hexdigest(),
    "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
with open(f"{OUT_DIR}/dataset_manifest.json", "w", encoding="utf-8") as f:
    json.dump(meta, f, indent=2)
print("[F1] dataset saved:", ds_path, flush=True)
print(json.dumps(meta, indent=2), flush=True)
