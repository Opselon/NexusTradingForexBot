"""A2 dataset-forensics probe: feature perturbation + label sanity + lineage (read-only).

Runs against the AUTHORITATIVE dataset ds_70d_clean_m1_20260904 and the
serving bundle artifacts/models/scalp/XAUUSD/70d_liquidity. Emits JSON.
Companion evidence for BUG-246 + TASK-AGENT2-DATA-FORENSICS.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, "src")

OUT: dict = {"probe": "ns_a2_perturbation_probe"}
DS = Path("artifacts/model_generation/datasets/ds_70d_clean_m1_20260904")
BUNDLE = Path("artifacts/models/scalp/XAUUSD/70d_liquidity")
FEATS = [f"feat_{i}" for i in range(70)]

df = pl.read_parquet(DS / "dataset.parquet")

from nexus_scalp.features.schema_contract import feature_schema_hash

OUT["schema_hash_runtime"] = feature_schema_hash()
OUT["schema_hash_manifest"] = "235b8fccc96b7e0e"
OUT["schema_hash_match"] = feature_schema_hash() == "235b8fccc96b7e0e"

sha = hashlib.sha256((DS / "dataset.parquet").read_bytes()).hexdigest()
OUT["dataset_sha256"] = sha
OUT["dataset_sha256_match"] = sha.startswith("3ae687eaaa1f32a6")

OUT["dup_sample_ids"] = int(df.height - df["sample_id"].unique().len())
OUT["dup_timestamps"] = int(df.height - df["timestamp"].unique().len())

ev = df.filter(pl.col("is_eval_sample"))
OUT["eval_rows"] = ev.height
OUT["eval_purged_overlap"] = int((df["is_eval_sample"] & df["is_purged"]).sum())
OUT["eval_label_counts"] = ev["label"].value_counts().sort("label").to_dicts()

trainable = df.filter(~pl.col("is_purged"))
OUT["trainable_rows"] = trainable.height
n = trainable.height
c = trainable["label"].value_counts()
OUT["trainable_label_share"] = {str(r["label"]): float(r["count"] / n) for r in c.to_dicts()}

OUT["schema_ids"] = df["feature_schema_id"].unique().to_list()
OUT["symbols"] = df["symbol"].unique().to_list()
OUT["timeframes"] = df["timeframe"].unique().to_list()

X = df.select(FEATS).to_numpy()
news = X[:, 50:60]
liq = X[:, 60:70]
base = X[:, 0:50]
OUT["news_all_zero_share"] = float((np.abs(news).sum(axis=1) == 0).mean())
OUT["news_absmax"] = float(np.abs(news).max())
OUT["liq_absmax"] = float(np.abs(liq).max())
OUT["liq_saturation_share_at3"] = float((np.abs(liq) >= 2.999).mean())
OUT["base_absmax"] = float(np.abs(base).max())
OUT["base_saturation_share_at3"] = float((np.abs(base) >= 2.999).mean())
OUT["nonfinite_total"] = int((~np.isfinite(X)).sum())

# Gap census on the authoritative artifact (BUG-246 impact probe):
ts = df["timestamp"].dt.epoch(time_unit="us").to_numpy()
g = np.diff(ts)
OUT["gap_census"] = {
    "deltas_gt_600s": int((g > 600_000_000).sum()),
    "deltas_gt_900s": int((g > 900_000_000).sum()),
    "deltas_in_600_900_band": int(((g > 600_000_000) & (g <= 900_000_000)).sum()),
    "largest_gap_min": float(g.max() / 60e6),
}

sc = np.load(BUNDLE / "model.scaler.npz")
sm, ss = sc["mean"], sc["std"]
Xe = ev.select(FEATS).to_numpy()
mu_e, sd_e = Xe.mean(axis=0), Xe.std(axis=0)
OUT["scaler_vs_eval_mean_maxabs"] = float(np.abs(mu_e - sm).max())

import torch

sd_t = torch.load(BUNDLE / "model.pt", map_location="cpu", weights_only=True)
OUT["bundle_head_shape"] = list(sd_t["classifier.weight"].shape)
OUT["bundle_input_shape"] = list(sd_t["input_projection.weight"].shape)
meta = json.loads((BUNDLE / "model.meta.json").read_text())
OUT["meta_num_classes"] = meta.get("num_classes")
OUT["meta_head_classes"] = meta.get("model_head_classes")
OUT["meta_declares_dataset_id"] = meta.get("dataset_id") is not None
OUT["meta_declares_git_commit"] = bool(meta.get("git_commit"))
OUT["meta_smoke"] = meta.get("smoke")
OUT["meta_production_eligible"] = meta.get("production_eligible")
OUT["meta_label_origin"] = meta.get("label_origin")
OUT["bundle_sha16"] = hashlib.sha256((BUNDLE / "model.pt").read_bytes()).hexdigest()[:16]
OUT["scaler_std_min"] = float(ss.min())
OUT["scaler_std_max"] = float(ss.max())

from nexus_scalp.model_lifecycle.model_class_contract import (
    is_production_eligible,
    mask_wait_logit,
)

OUT["contract_production_eligible"] = bool(is_production_eligible(meta))
logits = torch.randn(2, 4)
OUT["mask_wait_logit_zeroes_slot3"] = bool(torch.all(mask_wait_logit(logits)[:, 3] == -1e4))

print(json.dumps(OUT, indent=1))
