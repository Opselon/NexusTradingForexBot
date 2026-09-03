"""cProfile the FULL compute_70d_frame on a small slice."""
import cProfile
import io
import pstats

import polars as pl

from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(300)

pr = cProfile.Profile()
pr.enable()
compute_70d_frame(df, news_frame=None)
pr.disable()
s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
ps.print_stats(30)
print(s.getvalue())