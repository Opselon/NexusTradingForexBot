import time
import polars as pl
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

t0 = time.time()
df = pl.read_parquet('data/raw/XAUUSD_M1.parquet')
t1 = time.time()
out = compute_70d_frame_fast(df)
t2 = time.time()
print(f"load={t1-t0:.1f}s features={t2-t1:.1f}s rows={out.height}")
