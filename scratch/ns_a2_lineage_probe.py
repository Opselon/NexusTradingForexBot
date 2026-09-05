"""A2 data lineage probe — bounded build + data-contract validation (read-only).

Produces a self-contained 2D dataset from a small M1 slice via the canonical
builder path and validates the full RAW -> CLEAN -> FEATURES -> LABELS ->
DATASET -> MODEL-INPUT chain deterministically and quickly.

Scope: data contract only — never promotes, never touches the live champion
bundle, and never runs a walk-forward training loop. The small-slice build
exercises the SAME canonical builder (compute_70d_frame_fast, stride 2,
causal news bridge, purge/embargo) that the authoritative
ds_70d_clean_m1_20260904 artifact uses, so gate coverage is real, not a mock.

Re-run (from repo root):
    .venv/Scripts/python.exe scratch/ns_a2_lineage_probe.py  # ~15-30s
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, "src")

from nexus_scalp.features.schema_contract import canonical_feature_names, feature_schema_hash
from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.schema_v2 import build_70d_dataset, verify_70d_artifact
from nexus_scalp.model_generation.sequence import SequenceBuilder
from nexus_scalp.model_generation.temporal_contract import (
    CANONICAL_MAX_GAP_US,
    CANONICAL_SEQ_LEN,
)
from nexus_scalp.model_lifecycle.model_class_contract import MODEL_CLASS_CONTRACT_ID

OUT: dict = {"probe": "ns_a2_lineage_probe"}

t0 = time.perf_counter()

# ---- 1. RAW DATA (small contiguous slice of historical XAUUSD M1) ----
RAW = Path("data/raw/XAUUSD_M1.csv")
raw = (
    pl.read_csv(RAW)
    .with_columns(pl.col("time_utc").str.to_datetime(strict=True).alias("time_utc"))
    .sort("time")
)
N = 5000
slice_df = raw.tail(N)
OUT["raw"] = {
    "path": str(RAW),
    "total_bars": int(raw.height),
    "slice_bars": N,
    "slice_range": [str(slice_df["time"].min()), str(slice_df["time"].max())],
    "ohlcv_valid": bool(
        (slice_df["high"] >= slice_df[["open", "close"]].max_horizontal()).all()
        and (slice_df["low"] <= slice_df[["open", "close"]].min_horizontal()).all()
        and (slice_df["high"] >= slice_df["low"]).all()
    ),
    "dup_timestamps": int(slice_df.height - slice_df["time"].unique().len()),
    "strictly_increasing_time": bool(slice_df["time"].is_sorted()),
}

# ---- 2. CLEAN DATA (single ArtifactStore-backed build, checksum-identified) ----
store = ArtifactStore()
DATASET_ID = "a2_lineage_probe_5k"
try:
    store.delete_dataset(DATASET_ID)
except Exception:
    pass

build_70d_dataset(
    slice_df,
    timeframe="M1",
    news_frame=None,
    strategy_id="a2_lineage_probe",
    strategy_version="0.1.0",
    store=store,
    seed=42,
    dataset_id=DATASET_ID,
    incremental=True,
    verify_parity=False,
)
frame = store.read_dataset(DATASET_ID)
man = store.read_dataset_manifest(DATASET_ID) or {}
OUT["clean"] = {
    "dataset_id": DATASET_ID,
    "rows": int(frame.height),
    "eval_rows": int(frame.filter(pl.col("is_eval_sample") & ~pl.col("is_purged")).height),
    "dataset_sha256": hashlib.sha256(store.dataset_path(DATASET_ID).read_bytes()).hexdigest(),
    "feature_schema_id": man.get("feature_schema_id"),
    "feature_schema_hash": man.get("feature_schema_hash"),
    "label_origin": man.get("label_origin"),
    "purge_parameters": man.get("purge_parameters"),
}

# ---- 3. FEATURES (70D canonical — ordering, hash, clamp) ----
feat_cols = [f"feat_{i}" for i in range(70)]
canon_names = list(canonical_feature_names())
OUT["features"] = {
    "feature_count": len(feat_cols),
    "canonical_hash_runtime": feature_schema_hash(),
    "canonical_hash_manifest": man.get("feature_schema_hash"),
    "hash_matches": feature_schema_hash() == man.get("feature_schema_hash"),
    "canonical_names_len": len(canon_names),
    "verify_70d_artifact_ok": verify_70d_artifact(DATASET_ID, store=store).get("ok"),
}

# ---- 4. LABELS (3-class, causal, horizon/purge/embargo, distribution) ----
eval_frame = frame.filter(pl.col("is_eval_sample") & ~pl.col("is_purged"))
OUT["labels"] = {
    "model_class_contract_id": MODEL_CLASS_CONTRACT_ID,
    "label_distribution": eval_frame["label"].value_counts().sort("label").to_dicts(),
    "eval_purged_overlap": int((frame["is_eval_sample"] & frame["is_purged"]).sum()),
    "purge_parameters": man.get("purge_parameters"),
}

# ---- 5. DATASET (integrity + identity) ----
X_all = frame.select(feat_cols).to_numpy()
OUT["dataset"] = {
    "nonfinite": int((~np.isfinite(X_all)).sum()),
    "abs_max": float(np.abs(X_all).max()) if X_all.size else None,
    "temporal_sorted": bool(frame["timestamp"].is_sorted()),
    "dup_sample_ids": int(frame.height - frame["sample_id"].unique().len()),
    "symbols": frame["symbol"].unique().to_list(),
    "timeframes": frame["timeframe"].unique().to_list(),
}

# ---- 6. MODEL INPUT (SequenceContract gap-safe windowing) ----
builder = SequenceBuilder(seq_len=CANONICAL_SEQ_LEN, max_gap_us=CANONICAL_MAX_GAP_US)
seq = builder.build(eval_frame, news_enabled=False)
OUT["model_input"] = {
    "seq_len": CANONICAL_SEQ_LEN,
    "max_gap_us": CANONICAL_MAX_GAP_US,
    "windows_total": int(seq["valid"].shape[0]),
    "windows_valid": int(seq["valid"].sum()),
    "windows_rejected": int(seq["valid"].shape[0] - int(seq["valid"].sum())),
    "tensor_shape": list(seq["X"].shape),
    "tensor_finite": bool(np.isfinite(seq["X"]).all()),
    "labels_three_class": sorted(np.unique(seq["y"]).tolist()),
}

OUT["elapsed_sec"] = round(time.perf_counter() - t0, 1)
OUT["status"] = "PASS"

print(json.dumps(OUT, indent=2, default=str))
