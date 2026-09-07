import numpy as np
z = np.load('artifacts/models/scalp/XAUUSD/70d_liquidity/model.scaler.npz')
print("keys:", list(z.keys()))
for k in z.keys():
    a = z[k]
    print(k, a.shape, a[:3])
import polars as pl
lf = pl.scan_parquet('artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet')
cols = lf.collect_schema().names()
nonfeat = [c for c in cols if not c.startswith('feat_') and not c.startswith('news_')]
print("nonfeat/nonnews cols:", nonfeat)
