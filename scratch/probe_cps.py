"""Check champion's calibration: does prob rank order predict win rate?"""
import numpy as np, polars as pl, torch
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.training.safe_loader import load_state_dict_safe

ART='artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt'
SCAL='artifacts/models/scalp/XAUUSD/70d_liquidity/model.scaler.npz'
DS='artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet'
state=load_state_dict_safe(ART, expected_input_dim=70, expected_classes=3, check_approved_root=False)
m=ScalpNet(num_features=70,num_classes=3); m.load_state_dict(state); m.eval()
sc=np.load(SCAL); mean,std=sc['mean'],sc['std']
cols=[f"feat_{i}" for i in range(70)]
df=pl.scan_parquet(DS).select(cols+['label']).collect()
X=df.select(cols).to_numpy().astype(np.float32); y=df['label'].to_numpy()
Xs=np.clip((X-mean)/std,-5,5); Xs=np.nan_to_num(Xs)
with torch.inference_mode(): probs=m(torch.from_numpy(Xs)).numpy()

# decile of buy-prob among rows where pred==BUY; then outcome distribution
pb = probs[:,1]; ps = probs[:,2]
for name, p in [("BUY", pb), ("SELL", ps)]:
    q = np.quantile(p, [0.5, 0.9, 0.99])
    print(name, "prob q50/q90/q99:", q.round(4))
# top-decile conditional outcome
for name, p, lab in [("BUY", pb, 1), ("SELL", ps, 2)]:
    thr = np.quantile(p, 0.99)
    mask = p >= thr
    yy = y[mask]
    # P(label==lab | p in top 1%) vs base rate
    print(f"{name} top-1%: n={mask.sum()} P(win)={(yy==lab).mean():.4f} P(loss)={(yy==(3-lab)).mean():.4f} P(abstain)={(yy==0).mean():.4f}")
    base = (y==lab).mean()
    print(f"   base rate P({name} wins)={base:.4f}  lift={((yy==lab).mean()/max(base,1e-9)):.2f}x")
