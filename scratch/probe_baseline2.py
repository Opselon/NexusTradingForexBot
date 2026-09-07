"""Probe 2: champion vs DATA - evaluate over the full dataset in temporal chunks
and inspect whether prob distribution is degenerate (collapsed model check)."""
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
sc = np.load(SCAL); mean, std = sc['mean'], sc['std']

feat_cols = [f"feat_{i}" for i in range(70)]
df = pl.scan_parquet(DS).select(feat_cols + ['label']).collect()
n = df.height
X = df.select(feat_cols).to_numpy().astype(np.float32)
y = df['label'].to_numpy()
Xs = np.clip((X - mean) / std, -5, 5)
Xs = np.nan_to_num(Xs, nan=0.0, posinf=5.0, neginf=-5.0)

with torch.inference_mode():
    probs = model(torch.from_numpy(Xs)).numpy()

pred = probs.argmax(axis=1)
print("pred dist:", np.bincount(pred, minlength=3))
print("conf quantiles:", np.quantile(probs.max(axis=1), [0.05,0.25,0.5,0.75,0.95]).round(3))
print("buy prob quantiles:", np.quantile(probs[:,1], [0.05,0.5,0.95]).round(4))
print("sell prob quantiles:", np.quantile(probs[:,2], [0.05,0.5,0.95]).round(4))
acc = (pred == y).mean()
print("overall accuracy:", round(float(acc),4))
# accuracy on directional rows only
dir_mask = y != 0
print("acc on directional rows:", round(float((pred[dir_mask]==y[dir_mask]).mean()),4), "n=", dir_mask.sum())
# temporal halves
half = n//2
for name, sl in [("first", slice(0,half)), ("second", slice(half,n))]:
    p_, y_ = pred[sl], y[sl]
    print(name, "acc:", round(float((p_==y_).mean()),4), "pred dist:", np.bincount(p_, minlength=3))
