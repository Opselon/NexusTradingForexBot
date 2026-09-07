import polars as pl, time
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast
df = pl.read_parquet('data/raw/XAUUSD_M1.parquet').head(20000)
t0=time.time()
out = compute_70d_frame_fast(df)
t1=time.time()
with open('scratch/probe3_result.txt','w') as f:
    f.write(f"rows={out.height} elapsed={t1-t0:.1f}s\n")
