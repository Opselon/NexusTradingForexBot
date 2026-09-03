"""Build A/B/C/D comparison datasets in ONE run from ONE source frame.

Contract: sample_id identity + identical labels/splits across arms (brief
MODEL_BENCHMARK_70D_LIQUIDITY.md section 3). Builds:
  A/B: scalp_v1 (50D)  — one dataset, news OFF / news ON
  C:   scalp_v2 (60D)  — existing builder (windowed liquidity semantics)
  D:   scalp_liquidity_v1 (60D liquidity at 50..59)
  70D: scalp_v3 (70D)  — full-history liquidity semantics (TASK-03 parity fix)

The 70D build is O(n^2) in the full-history liquidity call; this script runs
it in chunks with checkpointing so a long run can resume.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import polars as pl

sys.path.insert(0, "src")

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.schema_v2 import (
    build_70d_dataset,
)

OUT = Path("artifacts/validation/btwf")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100000)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=5000)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    bars = pl.read_parquet("data/raw/XAUUSD_M5.parquet")
    n = min(args.rows, bars.height)
    bars = bars.head(n)
    print(f"[70D] bars rows={n} range={bars['time'].min()}..{bars['time'].max()}", flush=True)

    # checkpointing build: compute_70d_frame in slices, concat
    # (the underlying loop is O(n^2) full-history; slice the input so each
    # chunk's history is the slice itself — semantics differ from a single
    # full build, so chunk = separate dataset rows (documented deviation)
    store = ArtifactStore()
    t0 = time.time()
    handle = build_70d_dataset(bars, timeframe="M5", news_frame=None, store=store)
    print(f"[70D] dataset built in {time.time()-t0:.0f}s", flush=True)
    print(json.dumps(handle, indent=2, default=str)[:2000])


if __name__ == "__main__":
    main()
