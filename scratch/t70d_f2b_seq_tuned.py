# -*- coding: utf-8 -*-
"""T70D-F2b: Tuned sequence training — lower LR, label smoothing, stronger regularization."""
import sys, os, time, json, math, hashlib
sys.path.insert(0, "src")

import numpy as np
import polars as pl
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from nexus_scalp.model_generation.architectures import TCNAttentionV1
from nexus_scalp.model_generation.sequence import SequenceBuilder

SEED = 7
SEQ_LEN = 32
EPOCHS = 20
BATCH = 256
LR = 3e-4
WEIGHT_DECAY = 5e-4
DROPOUT = 0.25
LABEL_SMOOTHING = 0.08
OUT_DIR = "artifacts/model_generation/models/t70d_seq_v2_tuned"
LABEL_MAP = {"NO_TRADE": 0, "BUY_MARKET": 1, "SELL_MARKET": 2}

os.makedirs(OUT_DIR, exist_ok=True)
torch.manual_seed(SEED); np.random.seed(SEED)

df = pl.read_parquet("artifacts/model_generation/datasets/t70d_f1_full_m1/dataset.parquet")
df = df.filter(pl.col("label_evaluated") & ~pl.col("is_purged"))
labels_int = df["label"].replace_strict(LABEL_MAP, default=None, return_dtype=pl.Int64)
df = df.with_columns(labels_int.alias("y")).drop_nulls("y")
builder = SequenceBuilder(seq_len=SEQ_LEN, max_gap_us=10*60*1_000_000)
seq = builder.build(df.with_columns(pl.col("y").alias("label")), news_enabled=False)
X, y, valid = seq["X"], seq["y"], seq["valid"]
X, y = X[valid], y[valid]
n = X.shape[0]
n_train=int(n*0.70); n_val=int(n*0.15)
idx_train=np.arange(0, n_train)
idx_val=np.arange(n_train, n_train+n_val)
idx_test=np.arange(n_train+n_val, n)
flat_train = X[idx_train].reshape(-1, X.shape[-1])
mean = flat_train.mean(axis=0).astype(np.float32)
std = flat_train.std(axis=0).astype(np.float32)
std = np.where(std<1e-6,1.0,std)
def _scale(a): return np.clip((a-mean)/std,-5,5).astype(np.float32)
X_train, X_val, X_test = _scale(X[idx_train]), _scale(X[idx_val]), _scale(X[n_train+n_val:])
y_train, y_val, y_test = y[idx_train], y[idx_val], y[n_train+n_val:]

counts = np.bincount(y_train, minlength=3).astype(np.float64)
w = counts.sum() / (3.0 * np.maximum(counts, 1.0))
w = np.clip(w, 0.5, 4.0); w = w / w.mean()
w_t = torch.tensor(w, dtype=torch.float32)
print(f"[F2b] split train={len(idx_train)} val={len(idx_val)} test={len(idx_test)} weights={np.round(w,3).tolist()}", flush=True)

input_dim = X.shape[-1]
model = TCNAttentionV1(input_dim=input_dim, num_classes=3, max_seq_len=SEQ_LEN, dropout=DROPOUT)
param_count = sum(p.numel() for p in model.parameters())
print(f"[F2b] params={param_count:,} dropout={DROPOUT} lr={LR} wd={WEIGHT_DECAY} ls={LABEL_SMOOTHING}", flush=True)

train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
loader = DataLoader(train_ds, batch_size=BATCH, shuffle=True, generator=torch.Generator().manual_seed(SEED))
opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=EPOCHS)
crit = nn.CrossEntropyLoss(weight=w_t, label_smoothing=LABEL_SMOOTHING)
crit_nosmooth = nn.CrossEntropyLoss(weight=w_t)

best_val_loss, best_state, patience, bad = math.inf, None, 8, 0
hist = []
for ep in range(EPOCHS):
    model.train()
    ep_loss, nb = 0.0, 0
    for xb, yb in loader:
        opt.zero_grad(); out = model(xb); loss = crit(out, yb); loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 2.0); opt.step()
        ep_loss += float(loss.item()); nb += 1
    sched.step()
    with torch.no_grad():
        vl = crit_nosmooth(model(torch.from_numpy(X_val)), torch.from_numpy(y_val)).item()
    hist.append({"epoch": ep+1, "train_loss": round(ep_loss/max(nb,1),5), "val_loss": round(vl,5)})
    print(f"[F2b] ep {ep+1}/{EPOCHS} train={ep_loss/max(nb,1):.4f} val={vl:.4f}", flush=True)
    if vl < best_val_loss - 5e-5:
        best_val_loss, bad = vl, 0
        best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    else:
        bad += 1
        if bad >= patience:
            print(f"[F2b] early stop at {ep+1}", flush=True); break
if best_state is not None:
    model.load_state_dict(best_state)

model.eval()
with torch.no_grad():
    val_logits = model(torch.from_numpy(X_val))
    val_np = val_logits.numpy()
class TempScaler(nn.Module):
    def __init__(self): super().__init__(); self.log_T = nn.Parameter(torch.zeros(()))
    def forward(self, x): return x / torch.exp(self.log_T)
ts_mod = TempScaler()
ts_opt = torch.optim.LBFGS(ts_mod.parameters(), lr=0.1, max_iter=60)
yv_t = torch.from_numpy(y_val)
def _cl():
    ts_opt.zero_grad(); l = nn.CrossEntropyLoss()(ts_mod(val_logits.detach().clone()), yv_t); l.backward(); return l
ts_opt.step(_cl)
temperature = float(math.exp(ts_mod.log_T.item()))
print(f"[F2b] temperature={temperature:.4f}", flush=True)

def _metrics(logits_np, ye, tag):
    scaled = torch.from_numpy(logits_np) / temperature
    probs = torch.softmax(scaled, dim=-1).numpy()
    preds = probs.argmax(axis=1); acc=float((preds==ye).mean())
    out={"tag":tag,"n":int(len(ye)),"accuracy":round(acc,4),"pred_dist":np.bincount(preds,minlength=3).tolist(),"true_dist":np.bincount(ye,minlength=3).tolist(),"mean_max_prob":round(float(probs.max(1).mean()),4)}
    dm=np.isin(preds,[1,2])
    if dm.sum()>0: out["directional_calls"]=int(dm.sum()); out["directional_precision"]=round(float((preds[dm]==ye[dm]).mean()),4)
    conf=probs.max(1); ok=(preds==ye).astype(float); ece=0.0
    for b in range(15):
        lo,hi=b/15,(b+1)/15; m=(conf>lo)&(conf<=hi)
        if m.sum()>0: ece+=m.mean()*abs(ok[m].mean()-conf[m].mean())
    out["ece"]=round(float(ece),4); out["log_loss"]=round(float(-np.log(np.clip(probs[np.arange(len(ye)), ye],1e-9,1)).mean()),4)
    bal=float(np.mean([np.sum((preds==c)&(ye==c))/max((ye==c).sum(),1) for c in range(3)]))
    out["balanced_acc"]=round(bal,4)
    return out

with torch.no_grad():
    train_logits = model(torch.from_numpy(X_train[:20000])).numpy()
    test_logits = model(torch.from_numpy(X_test)).numpy()
m_train=_metrics(train_logits, y_train[:20000], "train20k")
m_val=_metrics(val_np, y_val, "validation")
m_test=_metrics(test_logits, y_test, "TEST_OOS")
print("[F2b] train:", json.dumps(m_train), flush=True)
print("[F2b] val  :", json.dumps(m_val), flush=True)
print("[F2b] OOS  :", json.dumps(m_test), flush=True)
sd=model.state_dict(); torch.save(sd, f"{OUT_DIR}/model.pt")
np.savez(f"{OUT_DIR}/model.scaler.npz", mean=mean, std=std)
def sha(p):
    import hashlib as h
    hh=h.sha256()
    with open(p,"rb") as f:
        for c in iter(lambda:f.read(1<<20),b""): hh.update(c)
    return hh.hexdigest()
meta={"model_id":"t70d_seq_v2_tuned","role":"CANDIDATE","trained_mode":"sequence","architecture":"TCN_ATTENTION_V1","feature_schema_id":"scalp_v3","feature_dimension":int(input_dim),"num_classes":3,"label_mapping":LABEL_MAP,"seq_len":SEQ_LEN,"max_seq_len":SEQ_LEN,"parameters":param_count,"seed":SEED,"epochs":EPOCHS,"batch_size":BATCH,"learning_rate":LR,"weight_decay":WEIGHT_DECAY,"dropout":DROPOUT,"label_smoothing":LABEL_SMOOTHING,"optimizer":"adamw","temperature":temperature,"split":{"train_rows":len(idx_train),"val_rows":len(idx_val),"oos_rows":len(idx_test),"rule":"chronological 70/15/15"},"dataset_id":"t70d_f1_full_m1","dataset_sha256":"9ea84e40beb8ff175ac5d3dc7446577246fe6bb36928387e8ed3e27e1846b221","scaler":{"type":"zscore_clip5","fit_scope":"TRAIN_ONLY"},"class_weights":w.tolist(),"metrics":{"train":m_train,"validation":m_val,"oos":m_test},"loss_history":hist,"model_sha256":sha(f"{OUT_DIR}/model.pt"),"trained_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
with open(f"{OUT_DIR}/model.meta.json","w",encoding="utf-8") as f: json.dump(meta,f,indent=2)
print("[F2b] saved",OUT_DIR,flush=True)
