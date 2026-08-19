"""TASK-05 — build A (50D) and B (60D) datasets on the SAME raw slice as the 70D C arm.

Fairness contract (brief 7 / MODEL_BENCHMARK_70D_LIQUIDITY.md section 3):
same raw bars -> same timestamps -> same labels -> same splits across arms.
Only the feature contract differs (50D / 60D / 70D).

A: scalp_v1 (50D)  — base features only
B: scalp_v2 (60D)  — base + TASK-5 momentum extras
C: scalp_v3 (70D)  — already built (ds_task5_real70d_2500)

Uses the existing 50D/60D artifact builders on the 2500-bar slice.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import polars as pl

RAW_M5 = Path("data/raw/XAUUSD_M5.parquet")
N_ROWS = 2500


def main() -> None:
    bars = pl.read_parquet(RAW_M5).head(N_ROWS)
    print(f"raw slice: {bars.height} rows", flush=True)
    from nexus_scalp.model_generation.artifact_store import ArtifactStore
    from nexus_scalp.model_generation.dataset_factory import DatasetFactory
    from nexus_scalp.model_generation.sample_factory import SampleFactory
    from nexus_scalp.model_generation.schema_v2 import compute_60d_frame

    store = ArtifactStore()

    # ---- B: 60D frame (feat_0..59) ----
    t0 = time.time()
    frame60 = compute_60d_frame(bars)
    print(f"60D frame: {frame60.height} rows in {time.time()-t0:.0f}s", flush=True)
    dh_b = DatasetFactory(store=store, sample_factory=SampleFactory(feature_schema_id="scalp_v2")).build(
        frame60, symbol="XAUUSD", timeframe="M5", news_frame=None,
        strategy_id="scalp_default", strategy_version="1.0.0",
    )
    print(f"B dataset: {dh_b['dataset_id']} rows={dh_b.get('counts', {}).get('total', dh_b.get('row_counts', {}).get('total'))}", flush=True)

    # ---- A: 50D — same bars through the base path (feat_0..49) ----
    # the 50D engine needs the raw frame with time/ohlc; DatasetFactory builds
    # over the base sample factory. The compute_60d_frame output contains
    # feat_0..59; a scalp_v1 sample factory reads feat_0..49 only (schema
    # controlled), so we can feed the same frame.
    dh_a = DatasetFactory(store=store, sample_factory=SampleFactory(feature_schema_id="scalp_v1")).build(
        frame60.select(
            [c for c in frame60.columns if not c.startswith("feat_") or c in [f"feat_{i}" for i in range(50)]]
        ),
        symbol="XAUUSD", timeframe="M5", news_frame=None,
        strategy_id="scalp_default", strategy_version="1.0.0",
    )
    print(f"A dataset: {dh_a['dataset_id']} rows={dh_a.get('counts', {}).get('total', dh_a.get('row_counts', {}).get('total'))}", flush=True)

    summary = {
        "A_50d": dh_a.get("dataset_id"),
        "B_60d": dh_b.get("dataset_id"),
        "C_70d": "ds_task5_real70d_2500",
        "raw_rows": int(bars.height),
        "note": "same 2500-bar slice; identical timestamps/labels/splits by construction (DatasetFactory)",
    }
    with open("artifacts/benchmarks/task5_abc_dataset_ids.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()