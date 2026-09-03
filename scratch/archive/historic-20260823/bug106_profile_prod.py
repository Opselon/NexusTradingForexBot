"""BUG-106 production-path benchmark: the BOUNDED builder (TASK-05 fix) at scale.

The canonical production path (schema_v2 with LIQUIDITY_HISTORY_LIMIT=4000)
is the parity-proven builder. Measure it at 1K/5K/10K/20K to prove the
O(n*4000) curve and produce artifacts/benchmarks/bug106_profile.json.
"""
import json
import sys
import time

import polars as pl

sys.path.insert(0, "src")
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet")
results = []
for n in (1000, 5000, 10000, 20000):
    d = df.head(n)
    t0 = time.perf_counter()
    fast = compute_70d_frame(d, news_frame=None)
    dt = time.perf_counter() - t0
    results.append(
        {
            "rows_input": n,
            "rows_output": fast.height,
            "seconds": round(dt, 2),
            "rows_per_second": round(n / dt),
        }
    )
    print(results[-1], flush=True)

out = {
    "probe": "AGENT-09 BUG-106 production-path profile (bounded builder, LIQUIDITY_HISTORY_LIMIT=4000)",
    "date": "2026-08-19",
    "machine": "Windows x64",
    "complexity": "O(n * 4000) per row (bounded causal window)",
    "curve": results,
    "note": "canonical full-history builder (pre-fix) measured separately: 1000 rows 82.7s (12 rows/s); bounded builder same input completes in seconds:",
    "comparison": {
        "1000_rows_canonical_seconds": 82.72,
        "1000_rows_bounded_seconds": results[0]["seconds"],
    },
}
with open("artifacts/benchmarks/bug106_profile.json", "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2)
print(json.dumps(out, indent=2))