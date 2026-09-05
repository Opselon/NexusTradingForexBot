"""AGENT-4 identity probe (rewritten after workspace file loss).

Read-only forensics: trained -> served coherence of the live 70D bundle.
For each bundle: model.pt sha16 + shapes, scaler sha16 + shapes, meta contract
fields. Cross-compares live vs trainer bundles to expose desyncs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import torch

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")


def sha16(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def probe_pt(p: Path) -> dict:
    sd = torch.load(p, map_location="cpu")
    return {
        "sha16": sha16(p),
        "n_tensors": len(sd),
        "input": list(sd["input_projection.weight"].shape),
        "head": list(sd["classifier.weight"].shape),
        "head_classes": int(sd["classifier.weight"].shape[0]),
    }


def probe_scaler(p: Path) -> dict:
    if not p.exists():
        return {"missing": True}
    d = np.load(p)
    mean = np.asarray(d["mean"], dtype=np.float32)
    std = np.asarray(d["std"], dtype=np.float32)
    return {
        "sha16": sha16(p),
        "mean_shape": list(mean.shape),
        "std_shape": list(std.shape),
        "std_min": float(std.min()),
        "std_min_idx": int(std.argmin()),
        "mean[40]": float(mean[40]),
        "std[40]": float(std[40]),
        "mean[41]": float(mean[41]),
        "std[41]": float(std[41]),
        "mean[42]": float(mean[42]),
        "std[42]": float(std[42]),
    }


def probe_meta(p: Path) -> dict:
    if not p.exists():
        return {"missing": True}
    m = json.loads(p.read_text(encoding="utf-8"))
    keys = [
        "num_features",
        "num_classes",
        "model_head_classes",
        "feature_schema_id",
        "feature_schema_hash",
        "seq_len",
        "trained_mode",
        "smoke",
        "production_eligible",
        "label_origin",
        "num_folds",
        "epochs_per_fold",
        "dataset_id",
        "dataset_sha256",
        "model_sha256",
        "model_class_contract_id",
    ]
    return {k: m.get(k) for k in keys if k in m}


def main() -> None:
    live = REPO / "artifacts/models/scalp/XAUUSD/70d_liquidity"
    t70d = REPO / "artifacts/model_generation/models/t70d_full_retrain"
    pilot = REPO / "artifacts/model_generation/models/pilot_70d_3class_20260904_232534"

    print("=" * 72)
    print("LIVE BUNDLE:", live)
    print("  model.pt         ", json.dumps(probe_pt(live / "model.pt")))
    print("  model.scaler.npz ", json.dumps(probe_scaler(live / "model.scaler.npz")))
    print("  model.meta.json  ", json.dumps(probe_meta(live / "model.meta.json"), default=str))

    print("=" * 72)
    print("TRAINER BUNDLE t70d_full_retrain:", t70d)
    print("  model.pt         ", json.dumps(probe_pt(t70d / "model.pt")))
    print("  model.scaler.npz ", json.dumps(probe_scaler(t70d / "model.scaler.npz")))
    print("  model.meta.json  ", json.dumps(probe_meta(t70d / "model.meta.json"), default=str))

    print("=" * 72)
    print("PILOT BUNDLE (c9982ddde175):", pilot)
    print("  model.pt         ", json.dumps(probe_pt(pilot / "model.pt")))
    print("  model.scaler.npz ", json.dumps(probe_scaler(pilot / "model.scaler.npz")))
    print("  model.meta.json  ", json.dumps(probe_meta(pilot / "model.meta.json"), default=str))

    print("=" * 72)
    print("LIVE DIR BACKUPS")
    for bak in sorted(live.glob("model.pt.bak*")) + sorted(live.glob("model.pt.pre*")):
        try:
            info = probe_pt(bak)
            print(f"  {bak.name:38s} sha16={info['sha16']} head={info['head_classes']} in={info['input']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {bak.name:38s} UNREADABLE {exc}")

    print("=" * 72)
    print("SCALER DELTAS: live vs t70d_full_retrain (weights' own trainer scaler)")
    L = np.load(live / "model.scaler.npz")
    T = np.load(t70d / "model.scaler.npz")
    lm, ls = np.asarray(L["mean"], dtype=np.float32), np.asarray(L["std"], dtype=np.float32)
    tm, ts = np.asarray(T["mean"], dtype=np.float32), np.asarray(T["std"], dtype=np.float32)
    dm, ds = np.abs(lm - tm), np.abs(ls - ts)
    print("  mean diff max", float(dm.max()), "at", int(dm.argmax()), "| nonzero cols", int((dm > 1e-6).sum()))
    print("  std  diff max", float(ds.max()), "at", int(ds.argmax()), "| nonzero cols", int((ds > 1e-6).sum()))


if __name__ == "__main__":
    main()
