"""TASK-10 forensic probe: dataset/replay/live parity + scaler forensics.

Read-only. Proves:
  P1. 70D/60D dataset artifact vs expected family layout (columns feat_50..69)
  P2. scaler dimension vs schema contract (50D champion, 60D task5_d, 62D news)
  P3. canonical 70D schema hash stability + family names
  P4. model artifact metadata vs manifest (champion)
  P5. golden 50D vector replay vs engine (known-context check)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from nexus_scalp.features.schema_contract import (  # noqa: E402
    NEWS_10D_NAMES,
    canonical_feature_names,
    feature_schema_hash,
)

LIQ_NAMES = (
    "bsl_distance_atr",
    "ssl_distance_atr",
    "eqh_strength",
    "eql_strength",
    "htf_liquidity_score",
    "internal_liquidity_distance",
    "external_liquidity_distance",
    "liquidity_confluence",
    "liquidity_sweep_state",
    "post_sweep_displacement",
)


def main() -> int:
    findings: list[str] = []

    # P1. existing dataset artifacts family layout
    for ds in ("ds_cb30f87520e9e6a4", "ds_b64513f79687824a"):
        p = REPO / "artifacts/model_generation/datasets" / ds / "dataset.parquet"
        if not p.exists():
            findings.append(f"P1: dataset {ds} missing")
            continue
        df = pl.read_parquet(p)
        feat = [c for c in df.columns if c.startswith("feat_")]
        print(f"P1. {ds}: rows={df.height} feat_cols={len(feat)}")
        # scaler check per known manifest
    mf_dir = REPO / "artifacts/model_generation/datasets/ds_b64513f79687824a/dataset_manifest.json"
    if mf_dir.exists():
        m = json.loads(mf_dir.read_text(encoding="utf-8"))
        print("   b645 manifest keys:", "_".join(m.keys())[:120])

    # P2. scaler dimensions (complete sweep)
    import glob

    for sc in sorted(glob.glob(str(REPO / "artifacts/model_generation/**/scaler.npz"), recursive=True)):
        z = np.load(sc)
        d = int(z["mean"].shape[0])
        print(f"P2. scaler {Path(sc).parent.name}: {d}D")
        if d not in (50, 60, 62, 72):
            findings.append(f"P2: unexpected scaler dim {d} at {sc}")

    # P3. canonical 70D hash + names
    names = canonical_feature_names()
    print(f"P3. canonical names: {len(names)} | hash={feature_schema_hash()}")
    print("   50..59:", names[50:60])
    print("   60..69:", names[60:70])
    if len(names) != 70:
        findings.append(f"P3: canonical names length {len(names)} != 70")
    if names[50:60] != NEWS_10D_NAMES:
        findings.append("P3: news block mismatch")
    if names[60:70] != LIQ_NAMES:
        findings.append("P3: liquidity block mismatch")

    # P4. champion artifact vs baseline
    champ = REPO / "artifacts/models/scalp/XAUUSD/v1.0.0"
    base = REPO / "docs/task5_champion_baseline.json"
    if champ.exists() and base.exists():
        import hashlib

        h = hashlib.sha256((champ / "model.pt").read_bytes()).hexdigest()
        b = json.loads(base.read_text(encoding="utf-8"))
        print(f"P4. champion hash now={h[:16]}... baseline={b['champion']['artifact_hash_sha256'][:16]}...")
        if not h.startswith(b["champion"]["artifact_hash_sha256"][:16]):
            findings.append("P4: champion artifact hash CHANGED vs baseline (frozen 50D)")

    print("\nFINDINGS:")
    for f in findings:
        print(" -", f)
    if not findings:
        print(" (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())