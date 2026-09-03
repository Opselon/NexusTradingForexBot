"""Equivalence probe: canonical compute_70d_frame vs incremental fast version (BUG-106)."""
import sys
import time

import polars as pl

sys.path.insert(0, "src")
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(400)

t0 = time.perf_counter()
canon = compute_70d_frame(df, news_frame=None)
t_canon = time.perf_counter() - t0

t0 = time.perf_counter()
fast = compute_70d_frame_fast(df, news_frame=None)
t_fast = time.perf_counter() - t0

print(f"canonical: {t_canon:.2f}s rows={canon.height}")
print(f"fast:      {t_fast:.2f}s rows={fast.height}")
print(f"speedup:   {t_canon / t_fast:.1f}x")

# byte-identical comparison on all feature columns
feat_cols = [c for c in canon.columns if c.startswith("feat_")]
diffs = 0
for c in feat_cols:
    a = canon[c].to_list()
    b = fast[c].to_list()
    for i, (x, y) in enumerate(zip(a, b, strict=True)):
        if x != y:
            diffs += 1
            if diffs <= 5:
                print(f"DIFF {c} row {i}: canon={x} fast={y}")
print(f"total feature diffs: {diffs} / {len(feat_cols) * canon.height}")

# timestamp columns identical?
ts_diff = (canon["timestamp"].to_list() != fast["timestamp"].to_list())
print("timestamps identical:", not ts_diff)