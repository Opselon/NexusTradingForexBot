"""Probe 3: does the champion beat naive direction baselines?
Compare: (a) always BUY, (b) always SELL, (c) champion model policy, on the
SAME evaluation convention (directional triple-barrier proxy, 0.15R friction)."""
import numpy as np
import polars as pl
import torch
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.training.safe_loader import load_state_dict_safe

ART = 'artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt'
SCAL = 'artifacts/models/scalp/XAUUSD/70d_liquidity/model.scaler.npz'
DS = 'artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet'

state = load_state_dict_safe(ART, expected_input_dim=70, expected_classes=3, check_approved_root=False)
model = ScalpNet(num_features=70, num_classes=3)
model.load_state_dict(state)
model.eval()
sc = np.load(SCAL)
mean, std = sc['mean'], sc['std']

feat_cols = [f"feat_{i}" for i in range(70)]
df = pl.scan_parquet(DS).select(feat_cols + ['label']).collect()
n = df.height
X = df.select(feat_cols).to_numpy().astype(np.float32)
y = df['label'].to_numpy()
Xs = np.clip((X - mean) / std, -5, 5)
Xs = np.nan_to_num(Xs)
with torch.inference_mode():
    probs = model(torch.from_numpy(Xs)).numpy()
pred = probs.argmax(axis=1)

r_buy = np.where(y == 1, 1.0, -1.0) - 0.15
r_sell = np.where(y == 2, 1.0, -1.0) - 0.15
print(f"always-BUY: exp={r_buy.mean():.3f}R trades={n}")
print(f"always-SELL: exp={r_sell.mean():.3f}R trades={n}")

m = pred != 0
r_m = np.where(y[m] == pred[m], 1.0, -1.0) - 0.15
print(f"champion policy: exp={r_m.mean():.3f}R trades={int(m.sum())} winrate={(r_m > 0).mean():.3f}")

mb = m & (pred == 1)
ms = m & (pred == 2)
print(f"champion BUY calls: exp={(np.where(y[mb] == 1, 1.0, -1.0) - 0.15).mean():.3f}R n={int(mb.sum())}")
print(f"champion SELL calls: exp={(np.where(y[ms] == 2, 1.0, -1.0) - 0.15).mean():.3f}R n={int(ms.sum())}")
print("label prior: BUY", round(float((y == 1).mean()), 3), "SELL", round(float((y == 2).mean()), 3))
