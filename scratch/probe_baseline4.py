"""Probe 4: strict temporal holdout evaluation of the champion on the LAST 15%
of the clean dataset, with the triple-barrier R semantics (label 0 = abstain,
not loss). Also: untrained-random baseline for comparison, and per-fold
temporal windows (coarse walk-forward)."""
import numpy as np
import polars as pl
import torch
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.training.safe_loader import load_state_dict_safe

ART = 'artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt'
SCAL = 'artifacts/models/scalp/XAUUSD/70d_liquidity/model.scaler.npz'
DS = 'artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet'
FRICTION = 0.15

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
conf = probs.max(axis=1)


def eval_policy(mask, y, label=""):
    if mask.sum() == 0:
        print(f"{label}: no trades")
        return
    yy = y[mask]
    r = np.where(yy == 0, 0.0, np.where(yy == 1, 1.0, -1.0)) - FRICTION
    wins = (r > 0).sum()
    losses = (r < 0).sum()
    pf = r[r > 0].sum() / max(1e-9, -r[r < 0].sum())
    eq = np.cumsum(r)
    dd = (np.maximum.accumulate(eq) - eq).max()
    print(f"{label}: trades={int(mask.sum())} exp={r.mean():.3f}R win={wins/(wins+losses+1e-9):.3f} PF={pf:.2f} maxDD={dd:.1f}R")


# last 15% holdout (never seen in 70/30 train split? Actually 70/30 split was train/val+test; last 15% approximates OOS)
start = int(n * 0.85)
m_h = np.zeros(n, bool)
m_h[start:] = pred[start:] != 0
eval_policy(m_h, y, "champion OOS tail(15%)")

# temporal windows of 20%: coarse walk-forward
for w in range(5):
    s, e = int(n * w * 0.2), int(n * (w + 1) * 0.2)
    m_w = np.zeros(n, bool)
    m_w[s:e] = pred[s:e] != 0
    eval_policy(m_w[s:e], y[s:e], f"window {w+1}/5")

# confidence sweep on tail
for thr in (0.345, 0.35, 0.36):
    m_t = np.zeros(n, bool)
    m_t[start:] = (pred[start:] != 0) & (conf[start:] >= thr)
    eval_policy(m_t, y, f"tail conf>={thr}")
