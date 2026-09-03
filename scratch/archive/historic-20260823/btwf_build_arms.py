"""Build A/B/C/D comparison dataset arms in ONE run from ONE source frame.

Arms (MODEL_BENCHMARK_70D_LIQUIDITY.md matrix):
  A: scalp_v1 50D (news OFF)
  B: scalp_v1 50D + news 12D (news ON — real news frame; near-zero coverage)
  C: scalp_v2 60D (TASK-5 extras at 50..59)
  D: scalp_liquidity_v1 60D (TASK-1 liquidity at 50..59)

All arms share: same raw bars, same labeler, same split (seed 42, 0.7/0.15/0.15),
same purge/embargo (3/3). Only the feature contract differs (fairness gate).
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, "src")
sys.path.insert(0, "scratch")

import polars as pl
from data_gate_2_bridge_raw_to_dataset import bars_to_feature_frame

from nexus_scalp.model_generation.artifact_store import ArtifactStore, default_artifact_root
from nexus_scalp.model_generation.dataset_factory import DatasetFactory
from nexus_scalp.model_generation.sample_factory import SampleFactory
from nexus_scalp.model_generation.schema_v2 import build_60d_dataset, build_liquidity_dataset

BARS = pl.read_parquet("data/raw/XAUUSD_M5.parquet")
store = ArtifactStore(default_artifact_root())


def main() -> None:
    print(f"bars: {BARS.height} {BARS['time'].min()}..{BARS['time'].max()}", flush=True)

    # ---- A/B: 50D feature frame (data-gate canonical, 55-bar window) ----
    t0 = time.time()
    feat50 = bars_to_feature_frame(BARS, "M5")
    print(f"[50D] feature frame: {feat50.height} rows {time.time()-t0:.0f}s", flush=True)
    feat50 = feat50.sort("timestamp")

    # ---- A: 50D news OFF ----
    t0 = time.time()
    hA = DatasetFactory(
        store=store, sample_factory=SampleFactory(feature_schema_id="scalp_v1")
    ).build(
        feat50, symbol="XAUUSD", timeframe="M5", news_frame=None,
        strategy_id="scalp_default", strategy_version="1.0.0", seed=42,
    )
    print(f"[A] 50D news-OFF dataset {hA['dataset_id']} {hA['counts']} {time.time()-t0:.0f}s", flush=True)

    # ---- B: 50D news ON (real news frame; near-zero overlap documented) ----
    news = (
        pl.read_parquet("data/raw/news.parquet")
        if os.path.exists("data/raw/news.parquet")
        else None
    )
    if news is None:
        print("[B] NO news parquet in data/raw — news arms use news=None (documented)", flush=True)

    t0 = time.time()
    hB = DatasetFactory(
        store=store, sample_factory=SampleFactory(feature_schema_id="scalp_v1")
    ).build(
        feat50, symbol="XAUUSD", timeframe="M5", news_frame=news,
        strategy_id="scalp_default", strategy_version="1.0.0", seed=42,
    )
    print(f"[B] 50D news-ON dataset {hB['dataset_id']} {hB['counts']} {time.time()-t0:.0f}s", flush=True)

    # ---- C: scalp_v2 60D ----
    t0 = time.time()
    hC = build_60d_dataset(BARS, timeframe="M5", news_frame=None, store=store)
    print(f"[C] 60D dataset {hC['dataset_id']} {hC['counts']} {time.time()-t0:.0f}s", flush=True)

    # ---- D: scalp_liquidity_v1 60D ----
    t0 = time.time()
    hD = build_liquidity_dataset(BARS, timeframe="M5", news_frame=None, store=store)
    print(f"[D] LIQ60D dataset {hD['dataset_id']} {hD['counts']} {time.time()-t0:.0f}s", flush=True)

    summary = {
        "A_50D_news_off": hA["dataset_id"],
        "B_50D_news_on": hB["dataset_id"],
        "C_60D": hC["dataset_id"],
        "D_liq60D": hD["dataset_id"],
        "bars": int(BARS.height),
        "news_available": news is not None,
    }
    with open("artifacts/validation/btwf/arm_datasets.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
