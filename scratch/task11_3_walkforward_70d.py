"""TASK-11 STEP-09: purged Walk-Forward on the real 70D dataset.

Runs the canonical WalkForwardTrainer (3-class, 4-head, purge+embargo)
on ds_task5_real70d_2500 (2446 real XAUUSD M5 rows, scalp_v3/70D).
Output goes to the SAFE wf_candidate path (BUG-104 guard) — never the
Champion path. Records fold/train/purge/val/embargo/OOS/model hash.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "artifacts/model_generation/models/wf_candidate"


def main() -> int:
    df = pl.read_parquet(
        REPO / "artifacts/model_generation/datasets/ds_task5_real70d_2500/dataset.parquet"
    )
    feat_cols = [f"feat_{i}" for i in range(70)]
    # label: the dataset stores int in 'label' (0/1/2) — map to strings for the
    # trainer contract.
    df = df.with_columns(
        pl.col("label")
        .map_elements(
            lambda x: {0: "NO_TRADE", 1: "BUY_MARKET", 2: "SELL_MARKET"}.get(int(x), "NO_TRADE"),
            return_dtype=pl.Utf8,
        )
        .alias("label")
    )
    print(
        f"rows: {df.height} | feat cols: {len(feat_cols)} | labels: {df['label'].value_counts().to_dict()}"
    )

    tr = WalkForwardTrainer(
        num_folds=4,
        feature_schema_id="scalp_v3",
        purge_gap_bars=15,
        embargo_bars=15,
        epochs_per_fold=2,
        artifact_save_path=OUT / "model.pt",
    )
    t0 = time.perf_counter()
    model = tr.train_and_validate(df, feat_cols)
    dt = time.perf_counter() - t0
    print(
        f"WALK-FORWARD OK in {dt:.1f}s — model params: {sum(p.numel() for p in model.parameters())}"
    )

    # model hash
    h = hashlib.sha256((OUT / "model.pt").read_bytes()).hexdigest()
    scaler_h = hashlib.sha256((OUT / "model.scaler.npz").read_bytes()).hexdigest()
    print(f"model.pt hash: {h[:16]}... (full {h})")
    print(f"scaler hash: {scaler_h[:16]}...")
    result = {
        "artifact_path": str(OUT),
        "model_pt_hash_sha256": h,
        "scaler_hash_sha256": scaler_h,
        "dataset": "ds_task5_real70d_2500",
        "feature_schema_id": "scalp_v3",
        "dimension": 70,
        "fold_count": 4,
        "purge_gap_bars": 15,
        "embargo_bars": 15,
        "elapsed_sec": round(dt, 2),
    }
    (REPO / "scratch/task11_3_walkforward_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
