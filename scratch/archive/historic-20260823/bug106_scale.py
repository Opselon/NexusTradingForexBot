"""BUG-106 scale benchmark: canonical vs fast at 1K/5K/10K/20K rows."""

import json
import sys
import time

import polars as pl

sys.path.insert(0, "src")
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet")

results = []
for n in (1000, 5000, 10000):
    d = df.head(n)
    # canonical (bounded — only run smaller sizes; 20K would take ~30+ min)
    if n <= 5000:
        t0 = time.perf_counter()
        canon = compute_70d_frame(d, news_frame=None)
        t_canon = time.perf_counter() - t0
    else:
        t_canon = None
    t0 = time.perf_counter()
    fast = compute_70d_frame_fast(d, news_frame=None)
    t_fast = time.perf_counter() - t0
    results.append(
        {
            "rows": n,
            "canonical_s": round(t_canon, 2) if t_canon else None,
            "canonical_rows_per_s": round(n / t_canon) if t_canon else None,
            "fast_s": round(t_fast, 2),
            "fast_rows_per_s": round(n / t_fast),
            "speedup": round(t_canon / t_fast, 1) if t_canon else None,
            "out_rows": fast.height,
        }
    )
    print(results[-1], flush=True)


print(json.dumps(results, indent=2))
