import time
import polars as pl
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

df = pl.read_parquet("data/raw/XAUUSD_M1.parquet")
t0 = time.perf_counter()
out = compute_70d_frame_fast(df, news_frame=None)
dt = time.perf_counter() - t0
print(f"FULL 100k: {dt:.1f}s ({dt/out.height*1000:.2f} ms/row) rows={out.height}")
