"""Build the REAL 70D (scalp_v3) dataset from real XAUUSD M5 history.

Uses build_70d_dataset(incremental=True, verify_parity=True) — the
byte-identical incremental builder with an embedded parity self-check.
Records dataset_id/hash/rows/schema/dimension/label-version + quality gate.
"""
import json
import sys
import time

import polars as pl

sys.path.insert(0, "src")
from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.schema_v2 import (
    build_70d_dataset,
    verify_70d_artifact,
)

BARS = "data/raw/XAUUSD_M5.parquet"
NEWS = None  # news frame built separately (real news DB) — see news step

df = pl.read_parquet(BARS).head(20000)
print(f"raw bars: {df.height} rows (bounded 20K — statistically meaningful, session-bounded)", flush=True)

store = ArtifactStore()
t0 = time.perf_counter()
handle = build_70d_dataset(
    df,
    timeframe="M5",
    news_frame=None,
    store=store,
    seed=42,
    incremental=True,
    verify_parity=True,
    dataset_id="ag09_real_70d_v1",
)
dt = time.perf_counter() - t0
print(f"build: {dt:.1f}s", flush=True)
print("handle:", json.dumps(handle, default=str)[:600], flush=True)

checks = verify_70d_artifact("ag09_real_70d_v1", store=store)
print("verify:", json.dumps(checks, default=str), flush=True)

man = store.read_dataset_manifest("ag09_real_70d_v1") or {}
summary = {
    "dataset_id": "ag09_real_70d_v1",
    "dataset_hash": man.get("dataset_hash", ""),
    "source": BARS,
    "symbol": "XAUUSD",
    "timeframe": "M5",
    "rows": checks.get("rows"),
    "schema": man.get("feature_schema_id"),
    "dimension": checks.get("feature_count"),
    "label_schema": man.get("label_schema_id"),
    "build_seconds": round(dt, 1),
    "verify_ok": checks.get("ok"),
    "checks": checks,
}
with open("artifacts/validation/70d_dataset_real.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, default=str)
print("SUMMARY:", json.dumps(summary, default=str)[:800], flush=True)