# -*- coding: utf-8 -*-
"""T70D-F4: Full-data lossless retrain of the WINNER (2D baseline) on TRAIN+VAL.

Why a new train: the F2b/2D OOS numbers are experiment holdouts (test = last 15%).
The artifact we promote must have seen all labeled rows (train+val+oos) — else
we throw away the freshest ~5k sequences (mid-Aug). This is the standard
"select on OOS, fit on everything" protocol.

Winner chosen: ScalpNet V3 2D (marginally best balanced_acc / directional
precision, simpler runtime path, same input contract = 70). Same hyperparams,
slightly more epochs; scaler refit on the FULL frame (TRAIN+VAL+OOS equivalent).

Also does a second candidate RETRAIN per the master-task "next-history" note:
the live engine's online fine-tune must start from a near-balanced base model,
so we export with the same bundle semantics.
"""
import sys, os, time, json, math, hashlib
sys.path.insert(0, "src")

import numpy as np, polars as pl, torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.model_generation.sequence import SequenceBuilder

SEED=42; SEQ_LEN=32; EPOCHS=16; BATCH=256; LR=3e-4; WD=5e-4; DROPOUT=0.25; LS=0.08
LABEL_MAP={"NO_TRADE":0,"BUY_MARKET":1,"SELL_MARKET":2}
OUT_DIR="artifacts/model_generation/models/t70d_full_retrain"
os.makedirs(OUT_DIR, exist_ok=True)
torch.manual_seed(SEED); np.random.seed(SEED)

df = pl.read_parquet("artifacts/model_generation/datasets/t70d_f1_full_m1/dataset.parquet")
df = df.filter(pl.col("label_evaluated") & ~pl.col("is_purged"))
li = df["label"].replace_strict(LABEL_MAP, default=None, return_dtype=pl.Int64)
df = df.with_columns(li.alias("y")).drop_nulls("y")
builder = SequenceBuilder(seq_len=SEQ_LEN, max_gap_us=10*60*1_000_000)
seq = builder.build(df.with_columns(pl.col("y").alias("label")), news_enabled=False)
X, y, valid = seq["X"], seq["y"], seq["valid"]
X, y = X[valid], y[valid]
print(f"[F4] full frame: sequences={X.shape} label_dist={np.bincount(y,minlength=3).tolist()}", flush=True)
# Use 70D dataset for ScalpNet 2D path => last vector only
X_last = X[:, -1, :]
flat = X_last.reshape(-1, X_last.shape[-1])
mean = flat.mean(axis=0).astype(np.float32)
std = flat.std(axis=0).astype(np.float32)
std = np.where(std<1e-6,1.0,std)
def _scale(a): return np.clip((a-mean)/std,-5,5).astype(np.float32)
Xn = _scale(X_last)
# train/val split for inner validation during retrain (still needed to
# pick early-stop on the full frame); val = last 15%
n = len(y)
n_val = int(n * 0.15)
idx_tr = np.arange(0, n - n_val)
idx_va = np.arange(n - n_val, n)
print(f"[F4] inner split tr={len(idx_tr)} va={len(idx_va)} (full {n})", flush=True)
counts = np.bincount(y[idx_tr], minlength=3).astype(np.float64)
w = counts.sum()/(3*np.maximum(counts,1))
w=np.clip(w,0.5,4.0); w=w/w.mean()
print(f"[F4] train counts {counts.tolist()} weights {np.round(w,3).tolist()}", flush=True)
model = ScalpNet(num_features=70, num_classes=4, dropout_rate=DROPOUT)
param_count=sum(p.numel() for p in model.parameters())
print(f"[F4] params {param_count:,}", flush=True)
train_ds=TensorDataset(torch.from_numpy(Xn[idx_tr]), torch.from_numpy(y[idx_tr]))
loader=DataLoader(train_ds, batch_size=BATCH, shuffle=True, generator=torch.Generator().manual_seed(SEED))
opt=torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WD)
sched=torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=EPOCHS)
crit=nn.CrossEntropyLoss(weight=torch.tensor([w[0],w[1],w[2],0.0],dtype=torch.float32), label_smoothing=LS)
crit_no=nn.CrossEntropyLoss(weight=torch.tensor([w[0],w[1],w[2],0.0],dtype=torch.float32))
best, best_sd, pat, bad = math.inf, None, 6, 0
for ep in range(EPOCHS):
    model.train(); ep_loss,nb=0.0,0
    for xb,yb in loader:
        opt.zero_grad(); out=model(xb, return_logits=True); loss=crit(out, yb); loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(),2.0); opt.step()
        ep_loss+=float(loss.item()); nb+=1
    sched.step()
    with torch.no_grad(): vl=crit_no(model(torch.from_numpy(Xn[idx_va]), return_logits=True), torch.from_numpy(y[idx_va])).item()
    print(f"[F4] ep {ep+1}/{EPOCHS} train={ep_loss/max(nb,1):.4f} val={vl:.4f}", flush=True)
    if vl < best - 5e-5: best, bad = vl, 0; best_sd={k:v.detach().clone() for k,v in model.state_dict().items()}
    else:
        bad+=1
        if bad>=pat: print(f"[F4] early stop at {ep+1}",flush=True); break
if best_sd is not None: model.load_state_dict(best_sd)
# Now CONTINUED fit: 3 more epochs on the FULL set (all rows) with val-mixed-in
# at a 10x lower LR — deterministic "polish", not a reinit. This is the
# lossless step: the weights that ship have seen the last 5k holdout.
print("[F4] polish phase on FULL data (3 epochs, lr/10)...", flush=True)
polish_lr = LR/10.0
for g in opt.param_groups: g["lr"] = polish_lr
full_ds=TensorDataset(torch.from_numpy(Xn), torch.from_numpy(y))
full_loader=DataLoader(full_ds, batch_size=BATCH, shuffle=True, generator=torch.Generator().manual_seed(SEED+1))
crit_full=nn.CrossEntropyLoss(weight=torch.tensor([w[0],w[1],w[2],0.0],dtype=torch.float32), label_smoothing=LS/2)
for pe in range(3):
    model.train(); ep_loss,nb=0.0,0
    for xb,yb in full_loader:
        opt.zero_grad(); loss=crit_full(model(xb, return_logits=True), yb); loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(),2.0); opt.step()
        ep_loss+=float(loss.item()); nb+=1
    print(f"[F4] polish {pe+1}/3 loss={ep_loss/max(nb,1):.4f}",flush=True)

# final validation is now the last 15% reevaluated (diagnostic only — no longer holdout)
model.eval()
with torch.no_grad():
    logits_all = model(torch.from_numpy(Xn), return_logits=True).numpy()
# temperature on the old val holdout logits for calibration
with torch.no_grad():
    val_logits = logits_all[idx_va]
class TempScaler(nn.Module):
    def __init__(self): super().__init__(); self.log_T = nn.Parameter(torch.zeros(()))
    def forward(self,x): return x / torch.exp(self.log_T)
from torch import nn as _nn
ts_mod=TempScaler(); yva_t=torch.from_numpy(y[idx_va]); val_t=torch.from_numpy(val_logits)
ts_opt=torch.optim.LBFGS(ts_mod.parameters(), lr=0.1, max_iter=60)
def cl():
    ts_opt.zero_grad(); l=_nn.CrossEntropyLoss()(ts_mod(val_t), yva_t); l.backward(); return l
ts_opt.step(cl)
T=float(torch.exp(ts_mod.log_T).item())
print(f"[F4] temperature {T:.4f}",flush=True)

def _metrics(logits_np, ye):
    scaled=logits_np/T; probs=torch.softmax(torch.from_numpy(scaled),dim=-1).numpy()
    preds=probs.argmax(1)
    return {"n":int(len(ye)),"accuracy":round(float((preds==ye).mean()),4),
            "pred_dist":np.bincount(preds,minlength=4).tolist(),"true_dist":np.bincount(ye,minlength=4).tolist(),
            "balanced_acc":round(float(np.mean([float(((preds==c)&(ye==c)).sum())/max((ye==c).sum(),1) for c in range(3)])),4)}

m_all=_metrics(logits_all, y)
m_va=_metrics(logits_all[idx_va], y[idx_va])
m_tr=_metrics(logits_all[idx_tr], y[idx_tr])
print(f"[F4] train {json.dumps(m_tr)}",flush=True)
print(f"[F4] val(historic) {json.dumps(m_va)}",flush=True)
print(f"[F4] all {json.dumps(m_all)}",flush=True)
# save
sd=model.state_dict(); torch.save(sd, f"{OUT_DIR}/model.pt")
np.savez(f"{OUT_DIR}/model.scaler.npz", mean=mean, std=std)
def sha(p):
    import hashlib as h; hh=h.sha256()
    with open(p,"rb") as f:
        for c in iter(lambda:f.read(1<<20),b""): hh.update(c)
    return hh.hexdigest()
meta={"model_id":"t70d_v1_full","role":"CANDIDATE","trained_mode":"single","architecture":"SCALPNET_V3_2D","feature_schema_id":"scalp_v3","feature_dimension":70,"num_classes":3,"label_mapping":LABEL_MAP,"seq_len":1,"max_seq_len":SEQ_LEN,"parameters":param_count,"seed":SEED,"epochs_trained":EPOCHS,"polish_epochs":3,"batch_size":BATCH,"learning_rate":LR,"weight_decay":WD,"dropout":DROPOUT,"label_smoothing":LS,"temperature":T,"split":{"note":"full-frame lossless: val was historic holdout for early-stop only; polish saw all rows","val_rows":len(idx_va),"total_rows":int(n)},"dataset_id":"t70d_f1_full_m1","dataset_sha256":"9ea84e40beb8ff175ac5d3dc7446577246fe6bb36928387e8ed3e27e1846b221","scaler":{"type":"zscore_clip5","fit_scope":"FULL_FRAME"},"class_weights":w.tolist(),"metrics":{"train":m_tr,"val_historic":m_va,"all":m_all},"model_sha256":sha(f"{OUT_DIR}/model.pt"),"trained_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
with open(f"{OUT_DIR}/model.meta.json","w",encoding="utf-8") as f: json.dump(meta,f,indent=2)
print("[F4] saved",OUT_DIR,flush=True)
