# -*- coding: utf-8 -*-
"""T70D-2D baseline on the SAME dataset windows (last-vector mode, ScalpNet V3).

Trains ScalpNet V3 (2D MLP path) on the last vector of each F1 sequence,
so it's an apples-to-apples comparison with the sequence model F2/F2b.
"""
import sys, os, time, json, math, hashlib
sys.path.insert(0, "src")

import numpy as np, polars as pl, torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.model_generation.sequence import SequenceBuilder

SEED=42; SEQ_LEN=32; EPOCHS=20; BATCH=256; LR=3e-4; WD=5e-4; DROPOUT=0.25; LS=0.08
LABEL_MAP={"NO_TRADE":0,"BUY_MARKET":1,"SELL_MARKET":2}
OUT_DIR="artifacts/model_generation/models/t70d_2d_baseline_same_windows"
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
n = X.shape[0]
n_train=int(n*0.70); n_val=int(n*0.15)
idx_train=np.arange(0,n_train); idx_val=np.arange(n_train, n_train+n_val); idx_test=np.arange(n_train+n_val, n)
X_last = X[:, -1, :]
flat_train = X_last[idx_train]
mean = flat_train.mean(axis=0).astype(np.float32)
std = flat_train.std(axis=0).astype(np.float32)
std = np.where(std<1e-6,1.0,std)
def _scale(a): return np.clip((a-mean)/std,-5,5).astype(np.float32)
Xtr, Xva, Xte = _scale(X_last[idx_train]), _scale(X_last[idx_val]), _scale(X_last[idx_test])
ytr, yva, yte = y[idx_train], y[idx_val], y[idx_test]
counts = np.bincount(ytr, minlength=3).astype(np.float64)
w = counts.sum()/(3*np.maximum(counts,1)); w=np.clip(w,0.5,4.0); w=w/w.mean()
w_t=torch.tensor(w,dtype=torch.float32)
print(f"[2D] split train={len(idx_train)} val={len(idx_val)} test={len(idx_test)} weights={np.round(w,3).tolist()}",flush=True)
model = ScalpNet(num_features=70, num_classes=4, dropout_rate=DROPOUT)
param_count=sum(p.numel() for p in model.parameters())
print(f"[2D] ScalpNet V3 params={param_count:,} lr={LR} wd={WD} ls={LS}",flush=True)
train_ds = TensorDataset(torch.from_numpy(Xtr), torch.from_numpy(ytr))
loader = DataLoader(train_ds, batch_size=BATCH, shuffle=True, generator=torch.Generator().manual_seed(SEED))
opt=torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WD)
sched=torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=EPOCHS)
crit=nn.CrossEntropyLoss(weight=torch.tensor([w[0],w[1],w[2],0.0],dtype=torch.float32), label_smoothing=LS)
crit_no=nn.CrossEntropyLoss(weight=torch.tensor([w[0],w[1],w[2],0.0],dtype=torch.float32))
best, best_sd, pat, bad = math.inf, None, 8, 0
hist=[]
for ep in range(EPOCHS):
    model.train(); ep_loss,nb=0.0,0
    for xb,yb in loader:
        opt.zero_grad(); out=model(xb, return_logits=True); loss=crit(out, yb); loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(),2.0); opt.step()
        ep_loss+=float(loss.item()); nb+=1
    sched.step()
    with torch.no_grad(): vl=crit_no(model(torch.from_numpy(Xva), return_logits=True), torch.from_numpy(yva)).item()
    hist.append({"epoch":ep+1,"train_loss":round(ep_loss/max(nb,1),5),"val_loss":round(vl,5)})
    print(f"[2D] ep {ep+1}/{EPOCHS} train={ep_loss/max(nb,1):.4f} val={vl:.4f}",flush=True)
    if vl < best - 5e-5: best, bad = vl, 0; best_sd={k:v.detach().clone() for k,v in model.state_dict().items()}
    else:
        bad+=1
        if bad>=pat: print(f"[2D] early stop at {ep+1}",flush=True); break
if best_sd is not None: model.load_state_dict(best_sd)
model.eval()
with torch.no_grad():
    train_logits = model(torch.from_numpy(Xtr[:20000]), return_logits=True).numpy()
    val_logits = model(torch.from_numpy(Xva), return_logits=True).numpy()
    test_logits = model(torch.from_numpy(Xte), return_logits=True).numpy()
# temperature scaling on val
class TempScaler(nn.Module):
    def __init__(self): super().__init__(); self.log_T = nn.Parameter(torch.zeros(()))
    def forward(self,x): return x / torch.exp(self.log_T)
ts_mod=TempScaler()
yv_t=torch.from_numpy(yva); val_t=torch.from_numpy(val_logits)
def _cl():
    ts_mod.zero_grad = None; pass
import copy
ts_opt=torch.optim.LBFGS(ts_mod.parameters(), lr=0.1, max_iter=60)
def cl():
    ts_opt.zero_grad(); l=nn.CrossEntropyLoss()(ts_mod(val_t), yv_t); l.backward(); return l
ts_opt.step(cl)
T=float(torch.exp(ts_mod.log_T).item())
print(f"[2D] temperature={T:.4f}",flush=True)
def _metrics(logits_np, ye, tag):
    scaled=logits_np/T; scaled_t=torch.from_numpy(scaled)
    probs=torch.softmax(scaled_t,dim=-1).numpy()
    preds=probs.argmax(1); acc=float((preds==ye).mean())
    out={"tag":tag,"n":int(len(ye)),"accuracy":round(acc,4),"pred_dist":np.bincount(preds,minlength=4).tolist(),"true_dist":np.bincount(ye,minlength=4).tolist(),"mean_max_prob":round(float(probs.max(1).mean()),4)}
    dm=np.isin(preds,[1,2])
    if dm.sum()>0: out["directional_calls"]=int(dm.sum()); out["directional_precision"]=round(float((preds[dm]==ye[dm]).mean()),4)
    bal=float(np.mean([float(np.sum((preds==c)&(ye==c)))/max((ye==c).sum(),1) for c in range(3)]))
    out["balanced_acc"]=round(bal,4); return out
m_train=_metrics(train_logits[:len(ytr[:20000])], ytr[:20000], "train20k")
m_val=_metrics(val_logits, yva, "validation")
m_test=_metrics(test_logits, yte, "TEST_OOS")
print("[2D] train:",json.dumps(m_train),flush=True)
print("[2D] val  :",json.dumps(m_val),flush=True)
print("[2D] OOS  :",json.dumps(m_test),flush=True)
sd=model.state_dict(); torch.save(sd, f"{OUT_DIR}/model.pt")
np.savez(f"{OUT_DIR}/model.scaler.npz", mean=mean, std=std)
def sha(p):
    import hashlib as h; hh=h.sha256()
    with open(p,"rb") as f:
        for c in iter(lambda:f.read(1<<20),b""): hh.update(c)
    return hh.hexdigest()
meta={"model_id":"t70d_2d_baseline_same_windows","role":"CANDIDATE","trained_mode":"single","architecture":"SCALPNET_V3_2D","feature_schema_id":"scalp_v3","feature_dimension":70,"num_classes":3,"label_mapping":LABEL_MAP,"seq_len":1,"parameters":param_count,"seed":SEED,"epochs":EPOCHS,"batch_size":BATCH,"learning_rate":LR,"weight_decay":WD,"dropout":DROPOUT,"label_smoothing":LS,"temperature":T,"split":{"train_rows":len(idx_train),"val_rows":len(idx_val),"oos_rows":len(idx_test),"rule":"chronological 70/15/15 on same windows as seq"},"dataset_id":"t70d_f1_full_m1","dataset_sha256":"9ea84e40beb8ff175ac5d3dc7446577246fe6bb36928387e8ed3e27e1846b221","scaler":{"type":"zscore_clip5","fit_scope":"TRAIN_ONLY"},"class_weights":w.tolist(),"metrics":{"train":m_train,"validation":m_val,"oos":m_test},"loss_history":hist,"model_sha256":sha(f"{OUT_DIR}/model.pt"),"trained_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
with open(f"{OUT_DIR}/model.meta.json","w",encoding="utf-8") as f: json.dump(meta,f,indent=2)
print("[2D] saved",OUT_DIR,flush=True)
