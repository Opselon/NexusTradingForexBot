"""TASK-11 STEP-05: build the REAL canonical 70D XAUUSD M5 dataset.

Uses data/raw/XAUUSD_M5.parquet (100,000 real M5 bars, zero nulls,
2025-03-12..2026-08-17) via the CANONICAL build_70d_dataset pipeline
(scalp_v3 / schema hash 235b8fccc96b7e0e).

Correct timestamp handling: the raw 'time' column is epoch SECONDS and
'time_utc' is the datetime column — use time_utc (never the broken
epoch-as-microseconds conversion that produced ds_d3f35's 1970 rows).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus_scalp.model_generation.schema_v2 import (
    build_70d_dataset,
    verify_70d_artifact,
)

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    src = REPO / "data/raw/XAUUSD_M5.parquet"
    df = pl.read_parquet(src)
    # Canonical input frame: use the datetime column; drop the int epoch.
    if "time_utc" in df.columns and df["time_utc"].dtype == pl.Datetime:
        # rename would collide with the int 'time' column: drop it first.
        frame = df.drop("time").rename({"time_utc": "time"})
    else:
        frame = df.with_columns(
            pl.from_epoch(pl.col("time"), time_unit="s").alias("time")
        )
    frame = frame.select(
        ["time", "open", "high", "low", "close", "tick_volume"]
    ).sort("time")
    # drop any duplicate timestamps (bar continuity)
    before = frame.height
    frame = frame.unique(subset=["time"], keep="first").sort("time")
    print(f"input rows: {before} -> unique {frame.height}")
    print(f"range: {frame['time'][0]} .. {frame['time'][-1]}")

    # Use the FULL 100k frame (70D builder is O(n^2); time it, use all rows
    # that fit the causal warm-up).
    t0 = time.perf_counter()
    handle = build_70d_dataset(frame, timeframe="M5", news_frame=None)
    dt = time.perf_counter() - t0
    did = handle.get("dataset_id")
    print(f"BUILD OK in {dt:.1f}s dataset_id={did}")
    print(json.dumps({"dataset_id": did, "counts": handle.get("counts"), "hash": handle.get("dataset_hash", "")}, indent=1))

    v = verify_70d_artifact(did)
    keys = ("ok", "feature_count", "rows", "schema_id_ok", "dimension_ok", "schema_hash_ok", "all_finite", "all_in_range")
    print(json.dumps({"verify": {k: v.get(k) for k in keys if k in v}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())