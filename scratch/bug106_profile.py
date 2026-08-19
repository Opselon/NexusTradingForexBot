"""Profile compute_70d_frame to prove the BUG-106 bottleneck (AGENT-09)."""
import cProfile
import io
import pstats
import sys
import time

import polars as pl

sys.path.insert(0, "src")
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(400)
t0 = time.perf_counter()
pr = cProfile.Profile()
pr.enable()
f = compute_70d_frame(df, news_frame=None)
pr.disable()
dt = time.perf_counter() - t0
s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
ps.print_stats(25)
print(f"TOTAL {dt:.2f}s rows={f.height}")
print(s.getvalue())