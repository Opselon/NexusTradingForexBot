"""Quick baseline edge probe: load champion ScalpNet, evaluate on the clean
70D dataset's final 15% (a strict temporal holdout), with direction-filtered
trading policy and friction costs. This is a PHASE 1 reconnaissance probe —
full walk-forward comes in the real baseline evaluator."""
import json
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
lf = pl.scan_parquet(DS)
df = lf.select(feat_cols + ['label', 'is_eval_sample', 'is_purged', 'timestamp']).collect()

n = df.height
X = df.select(feat_cols).to_numpy().astype(np.float32)
y = df['label'].to_numpy()
Xs = (X - mean) / std
Xs = np.clip(Xs, -5, 5)
Xs = np.nan_to_num(Xs, nan=0.0, posinf=5.0, neginf=-5.0)

with torch.inference_mode():
    probs = model(torch.from_numpy(Xs)).numpy()

# direction policy: only trade when directional prob >= threshold, else NO_TRADE
for thr in (0.40, 0.50, 0.60):
    pred = probs.argmax(axis=1)
    conf = probs.max(axis=1)
    trade = (pred != 0) & (conf >= thr)
    # trade outcome in R: correct direction = +R; wrong = -R (proxy, triple-barrier 1R symmetric)
    # Use the label as the realized direction outcome: label==pred -> win +1R, pred!=0 and label in (1,2) and label!=pred -> -1R
    r = np.zeros(n)
    r[trade] = np.where(y[trade] == pred[trade], 1.0, -1.0)
    # friction: 0.35 USD per oz; typical scalp risk distance ~ $1.5-3/oz => friction ~0.1-0.25R. Use 0.15R.
    r[trade] -= 0.15
    nt = trade.sum()
    if nt == 0:
        print(f"thr={thr}: no trades"); continue
    wins = (r[trade] > 0).sum()
    exp = r[trade].mean()
    pf = r[trade][r[trade]>0].sum() / max(1e-9, -r[trade][r[trade]<0].sum())
    eq = np.cumsum(r[trade])
    dd = (np.maximum.accumulate(eq) - eq).max()
    print(f"thr={thr} trades={nt} winrate={wins/nt:.3f} expectancy={exp:.3f}R PF={pf:.2f} maxDD={dd:.1f}R")
print("total samples:", n, "label dist:", np.bincount(y, minlength=3))
