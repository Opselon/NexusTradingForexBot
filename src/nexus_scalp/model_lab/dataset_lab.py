"""MODEL LAB — research dataset construction (CHG-0047).

Builds the lab research dataset from the canonical M1 bars through the
REPOSITORY-OWN causal producers (never a lab reimplementation):

    features : compute_70d_frame_fast (the byte-identical fast path to
               compute_70d_frame — the same 70D contract as production)
    labels   : TripleBarrierLabeler (the repository's causal 3-class labeler,
               TP=1.1xATR / SL=1.0xATR / 15 bars / friction=$0.35 / embargo 3)

For the TEMPORAL experiments the same frame is wrapped into strict causal
windows: sample at t carries features from t-window+1..t ONLY.

Persisted through the repository's ArtifactStore with the standard
fingerprinting, plus lab-specific integrity facts (chronology, duplicates,
per-split label distribution, regime availability).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.model_lab.registry import LAB_ROOT

LABEL_TP_ATR = 1.1
LABEL_SL_ATR = 1.0
LABEL_MAX_BARS = 15
LABEL_FRICTION_USD = 0.35
LABEL_EMBARGO_BARS = 3


def build_research_frame(
    bars: pl.DataFrame,
    *,
    news_frame: pl.DataFrame | None = None,
    variant: str = "70d_liquidity",
) -> pl.DataFrame:
    """Causal 70D feature frame + causal 3-class labels (repo producers)."""
    from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
    from nexus_scalp.model_generation.three_model import build_feature_frame

    feat = build_feature_frame(variant, bars, news_frame)
    labeler = TripleBarrierLabeler(
        take_profit_atr_mult=LABEL_TP_ATR,
        stop_loss_atr_mult=LABEL_SL_ATR,
        max_holding_bars=LABEL_MAX_BARS,
        friction_usd=LABEL_FRICTION_USD,
        embargo_bars=LABEL_EMBARGO_BARS,
    )
    labeled = labeler.label_dataframe(feat)
    if "label_evaluated" in labeled.columns:
        labeled = labeled.filter(pl.col("label_evaluated"))
    if "is_purged" in labeled.columns:
        labeled = labeled.filter(~pl.col("is_purged"))
    return labeled.sort("timestamp")


def integrity_report(frame: pl.DataFrame, feature_cols: list[str]) -> dict[str, Any]:
    """Chronology / duplicate / dimension / finiteness / label facts."""
    ts = frame["timestamp"]
    ordered = bool(ts.is_sorted())
    dupes = int(frame.height - ts.n_unique())
    X = frame.select(feature_cols).to_numpy().astype(np.float64)
    labels = frame["label"].to_numpy() if "label" in frame.columns else np.array([])
    counts = (
        {int(k): int(v) for k, v in zip(*np.unique(labels, return_counts=True), strict=False)}
        if labels.size
        else {}
    )
    n = frame.height
    return {
        "rows": n,
        "feature_dimension": len(feature_cols),
        "chronologically_ordered": ordered,
        "duplicate_timestamps": dupes,
        "non_finite_cells": int((~np.isfinite(X)).sum()),
        "label_distribution": {str(k): v for k, v in sorted(counts.items())},
        "label_balance_buy_sell": round(counts.get(1, 0) / max(1, counts.get(2, 0)), 3),
        "label_coverage_pct": round(100 * n / max(1, n), 2),
    }


def temporal_split_bounds(
    n: int, train_ratio: float = 0.70, val_ratio: float = 0.15
) -> dict[str, int]:
    """Chronological split bounds (train | val | oos) — no shuffling, ever."""
    t = int(n * train_ratio)
    v = int(n * val_ratio)
    return {"train_end": t, "val_end": t + v, "oos_end": n}


def apply_split(frame: pl.DataFrame, bounds: dict[str, int]) -> pl.DataFrame:
    n = frame.height
    lab = pl.Series(
        "_split",
        ["train"] * bounds["train_end"]
        + ["val"] * (bounds["val_end"] - bounds["train_end"])
        + ["oos"] * (n - bounds["val_end"]),
    )
    return frame.with_columns(lab)


def fingerprint_frame(frame: pl.DataFrame, feature_cols: list[str]) -> str:
    """Deterministic dataset fingerprint over (timestamp, label, features)."""
    h = hashlib.sha256()
    cols = ["timestamp", "label", *feature_cols]
    sub = frame.select(cols)
    for batch in sub.iter_slices(n_rows=4096):
        h.update(str(batch.to_numpy().tobytes()).encode("utf-8", "ignore"))
        h.update(np.ascontiguousarray(batch.select(cols[2:]).to_numpy()).tobytes())
        h.update(str(batch["timestamp"].to_list()[:1]).encode())
    # Deterministic content hash: columns are fixed order, float32 bytes.
    h = hashlib.sha256()
    h.update(str(cols).encode())
    X = frame.select(feature_cols).to_numpy().astype(np.float32)
    h.update(np.ascontiguousarray(X).tobytes())
    h.update(frame["label"].to_numpy().astype(np.int64).tobytes())
    h.update(frame["timestamp"].cast(pl.Int64).to_numpy().tobytes())
    return h.hexdigest()[:32]


def save_dataset_report(report: dict[str, Any]) -> str:
    out_dir = LAB_ROOT / "datasets"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dataset_report_{report['dataset_id'][:16]}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return str(path)


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()
