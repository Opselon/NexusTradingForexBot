"""TASK-05 — build the REAL 70D (scalp_v3) dataset from raw XAUUSD M5 bars.

Uses the BUG-106-fixed build_70d_dataset (bounded 4000-bar history =
live semantics). Bounded row count for benchmark feasibility
(default 12000 rows ~ 6 weeks; ~1.2 s/row worst case -> ~1-2 h single
thread with the news frame disabled and symmetric costs).

Records: dataset_id, dataset_hash, source, time range, row count,
schema_id, schema_hash, feature_dimension, label_version (brief 6).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import polars as pl

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.schema_v2 import build_70d_dataset

RAW_M5 = Path("data/raw/XAUUSD_M5.parquet")


def main(n_rows: int = 12000) -> None:
    bars = pl.read_parquet(RAW_M5).head(n_rows)
    print(
        f"[TASK5] raw bars: {bars.height} range {bars['time'].min()}..{bars['time'].max()}",
        flush=True,
    )
    t0 = time.time()
    handle = build_70d_dataset(
        bars,
        timeframe="M5",
        news_frame=None,
        store=ArtifactStore(),
        dataset_id=f"ds_task5_real70d_{n_rows}",
    )
    dt = time.time() - t0
    print(f"[TASK5] built in {dt:.0f}s", flush=True)
    # summarize the record required by brief 6
    summary = {
        "dataset_id": handle.get("dataset_id"),
        "dataset_hash": handle.get("dataset_hash"),
        "source": str(RAW_M5),
        "time_range": handle.get("temporal_range"),
        "row_counts": handle.get("counts") or handle.get("row_counts"),
        "feature_schema_id": handle.get("feature_schema_id"),
        "feature_schema_hash": handle.get("feature_schema_hash"),
        "feature_dimension": 70,
        "label_schema_id": handle.get("label_schema_id"),
        "build_seconds": round(dt, 1),
    }
    with open("artifacts/benchmarks/task5_real70d_dataset.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str), flush=True)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=12000)
    args = ap.parse_args()
    main(args.rows)