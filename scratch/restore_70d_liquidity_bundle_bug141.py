"""restore_70d_liquidity_bundle_bug141.py

BUG-141 recovery: regenerate artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt
through the CANONICAL three_model trainer (purged walk-forward, same gate as the
original variant generation), from data/raw/XAUUSD_M1.csv (100k M1 bars).

Restore-at-parity: the clobbered artifact's own provenance (model_variants.json)
was a 2-fold smoke-grade run (trainable_rows=675), so this restore trains the
same smoke grade over a 3,000-bar causal window. A production-grade retrain
(34 folds x 10 epochs over the full history) is the documented follow-up.

Read-only inputs; writes ONLY the three bundle files at the canonical variant
paths (+ benchmark evidence json + lifecycle registration rows).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from nexus_scalp.model_generation.three_model import train_variant  # noqa: E402

CSV = REPO / "data" / "raw" / "XAUUSD_M1.csv"
OUT_DIR = REPO / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity"


def load_bars() -> pl.DataFrame:
    df = pl.read_csv(CSV)
    # 'time' is epoch SECONDS (server-local capture, epoch is what we have);
    # convert to UTC Datetime so compute_70d_frame's causal windows are real.
    df = df.with_columns(
        pl.from_epoch(pl.col("time"), time_unit="s")
        .dt.replace_time_zone("UTC")
        .alias("time")
    ).drop("time_utc")
    cols = ["time", "open", "high", "low", "close", "tick_volume"]
    return df.select(cols).sort("time")


def main() -> int:
    bars = load_bars()
    print(f"bars loaded: {bars.height} rows, {bars['time'].min()} .. {bars['time'].max()}")
    smoke_bars = bars.tail(3000)
    print(f"smoke window: {smoke_bars.height} rows, {smoke_bars['time'].min()} .. {smoke_bars['time'].max()}")

    pre = OUT_DIR / "model.pt"
    print(f"pre-restore sha256: {__import__('hashlib').sha256(pre.read_bytes()).hexdigest()[:16]}")

    report = train_variant("70d_liquidity", smoke_bars, smoke=True)

    print("== report ==")
    print(json.dumps({k: v for k, v in report.items() if k != "walk_forward"}, default=str, indent=2))
    print("walk_forward:", json.dumps(report.get("walk_forward", {}), default=str))

    import torch

    sd = torch.load(OUT_DIR / "model.pt", map_location="cpu", weights_only=True)
    w = tuple(sd["input_projection.weight"].shape)
    import numpy as np

    z = np.load(OUT_DIR / "model.scaler.npz")
    meta = json.loads((OUT_DIR / "model.meta.json").read_text(encoding="utf-8"))
    print("post head:", w, "scaler:", {k: z[k].shape for k in z.files},
          "meta dim:", meta.get("feature_schema_dimension"))
    assert w == (128, 70), "restored artifact is NOT 70D"
    assert z["mean"].shape == (70,), "restored scaler is NOT 70D"
    print(f"post-restore sha256: {__import__('hashlib').sha256(pre.read_bytes()).hexdigest()[:16]}")
    print("RESTORE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
