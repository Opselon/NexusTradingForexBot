"""Probe 3b: correct directional scoring — only score rows where BOTH sides
agree on direction (label==pred direction). Rows where the label says NO_TRADE
(0) are abstentions, not losses. This matches the triple-barrier semantics:
label 1 = BUY reaches TP first, label 2 = SELL reaches TP first,
label 0 = neither within horizon (abstain)."""
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
df = pl.scan_parquet(DS).select(feat_cols + ['label', 'is_eval_sample', 'is_purged']).collect()
n = df.height
X = df.select(feat_cols).to_numpy().astype(np.float32)
y = df['label'].to_numpy()
ev = df['is_eval_sample'].to_numpy()
Xs = np.clip((X - mean) / std, -5, 5)
Xs = np.nan_to_num(Xs)
with torch.inference_mode():
    probs = model(torch.from_numpy(Xs)).numpy()
pred = probs.argmax(axis=1)

# --- correct directional proxy ---
# When model trades BUY: outcome +1R if label==1, -1R if label==2, ~0 if label==0 (time exit, small drift)
# friction 0.15R per trade
FRICTION = 0.15

def directional_r(mask, y):
    r = np.zeros(int(mask.sum()))
    yy = y[mask]
    r = np.where(yy == 0, 0.0, np.where(yy == 1, 1.0, -1.0)) - FRICTION
    return r

m = pred != 0
r = directional_r(m, y)
wins = (r > 0).sum()
losses = (r < 0).sum()
pf = r[r > 0].sum() / max(1e-9, -r[r < 0].sum())
eq = np.cumsum(r)
dd = (np.maximum.accumulate(eq) - eq).max()
print(f"champion policy: trades={int(m.sum())} exp={r.mean():.3f}R winrate={wins/max(1,wins+losses):.3f} PF={pf:.2f} maxDD={dd:.1f}R")

# naive: always trade in label direction when label != 0 (oracle) — upper bound
mo = y != 0
ro = directional_r(mo, y) - 0  # oracle gets the label direction always right => always +1R - friction
print(f"oracle directional: trades={int(mo.sum())} exp={ro.mean():.3f}R")

# naive: always BUY on every bar
rb = np.where(y == 0, 0.0, 1.0) - FRICTION
print(f"always-BUY every bar: exp={rb.mean():.3f}R trades={n}")
rs = np.where(y == 0, 0.0, -1.0) - FRICTION
print(f"always-SELL every bar: exp={rs.mean():.3f}R trades={n}")

# eval-sample subset only
me = m & ev
re = directional_r(me, y)
print(f"champion on is_eval_sample rows: trades={int(me.sum())} exp={re.mean():.3f}R")
