"""Scale probe: measure compute_60d_frame / compute_70d_frame / train cost on a slice."""

import time

import numpy as np
import polars as pl

from nexus_scalp.model_generation.schema_v2 import compute_60d_frame, compute_70d_frame

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(6000)

t0 = time.time()
f60 = compute_60d_frame(df)
t60 = time.time() - t0
print(f"compute_60d_frame: rows={f60.height} cols={f60.width} time={t60:.1f}s")

t0 = time.time()
f70 = compute_70d_frame(df, news_frame=None)
t70 = time.time() - t0
print(f"compute_70d_frame: rows={f70.height} cols={f70.width} time={t70:.1f}s")

feat60 = [c for c in f60.columns if c.startswith("feat_")]
feat70 = [c for c in f70.columns if c.startswith("feat_")]
print("60D feat cols:", len(feat60), "70D feat cols:", len(feat70))
# spot-check finite/bounds on 70D

arr = f70.select(feat70).to_numpy()
print(
    "70D nonfinite:",
    int((~np.isfinite(arr)).sum()),
    "out-of-range:",
    int(((arr < -3) | (arr > 3)).sum()),
)
