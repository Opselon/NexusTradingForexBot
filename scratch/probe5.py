import polars as pl, time
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast
import cProfile, pstats, io
df = pl.read_parquet('data/raw/XAUUSD_M1.parquet').head(3000)
pr = cProfile.Profile()
pr.enable()
out = compute_70d_frame_fast(df)
pr.disable()
s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
ps.print_stats(12)
with open('scratch/probe5_result.txt','w') as f:
    f.write(f"rows={out.height}\n" + s.getvalue()[:4000])
