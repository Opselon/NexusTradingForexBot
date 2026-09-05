"""A2 data lineage test — bounded dataset/build path (fast, deterministic).

Verifies the FULL lineage RAW -> CLEAN -> FEATURES -> LABELS -> DATASET ->
MODEL INPUT via the SAME canonical builder the authoritative
ds_70d_clean_m1_20260904 artifact uses, on a small 5k-bar M1 tail so the
suite finishes in seconds, not minutes.

Reuse (from repo root, never concurrent with other pytest that writes to the
same scratch dataset id):
    .venv/Scripts/python.exe -m pytest tests/unit/test_a2_data_lineage_bounded.py -q -p no:cacheprovider
"""

from __future__ import annotations

import hashlib

import numpy as np
import polars as pl

from nexus_scalp.features.schema_contract import canonical_feature_names, feature_schema_hash
from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.schema_v2 import build_70d_dataset, verify_70d_artifact
from nexus_scalp.model_generation.sequence import SequenceBuilder
from nexus_scalp.model_generation.temporal_contract import (
    CANONICAL_MAX_GAP_US,
    CANONICAL_SEQ_LEN,
)
from nexus_scalp.model_lifecycle.model_class_contract import MODEL_CLASS_CONTRACT_ID


def _load_slice(n: int = 5000) -> pl.DataFrame:
    raw = (
        pl.read_csv("data/raw/XAUUSD_M1.csv")
        .with_columns(pl.col("time_utc").str.to_datetime(strict=True).alias("time_utc"))
        .sort("time")
    )
    return raw.tail(n)


def test_a2_bounded_dataset_build_and_lineage() -> None:
    """RAW -> CLEAN -> FEATURES -> LABELS -> DATASET -> MODEL INPUT on 5k M1 tail."""

    store = ArtifactStore()
    dataset_id = "a2_lineage_probe_5k"
    # Deterministic rerun: blow away the prior probe artifact (store has no
    # delete helper; the path is the single source of writability).
    for p_ in (store.dataset_path(dataset_id), store.dataset_manifest_path(dataset_id)):
        try:
            p_.unlink(missing_ok=True)
        except Exception:
            pass

    slice_df = _load_slice(5000)
    assert int(slice_df.height) == 5000

    # ---- RAW DATA -----------------------------------------------------
    assert slice_df["time"].is_sorted()
    assert int(slice_df.height - slice_df["time"].unique().len()) == 0
    assert (slice_df["high"] >= slice_df[["open", "close"]].max_horizontal()).all()
    assert (slice_df["low"] <= slice_df[["open", "close"]].min_horizontal()).all()

    # ---- CLEAN DATA (single store-backed build) -----------------------
    build_70d_dataset(
        slice_df,
        timeframe="M1",
        news_frame=None,
        strategy_id="a2_lineage_probe",
        strategy_version="0.1.0",
        store=store,
        seed=42,
        dataset_id=dataset_id,
        incremental=True,
        verify_parity=False,
    )
    frame = store.read_dataset(dataset_id)
    assert frame is not None and not frame.is_empty()
    man: dict = store.read_dataset_manifest(dataset_id) or {}

    # ---- FEATURES (70D canonical contract) -----------------------------
    canon = list(canonical_feature_names())
    assert len(canon) == 70
    assert feature_schema_hash() == "235b8fccc96b7e0e"
    assert man.get("feature_schema_hash") == "235b8fccc96b7e0e"
    assert man.get("feature_schema_id") == "scalp_v3"
    for i in range(5):
        assert f"feat_{i}" in frame.columns
    vf = verify_70d_artifact(dataset_id, store=store)
    assert vf.get("ok") is True, f"verify_70d_artifact failed: {vf}"
    assert int(vf.get("feature_count", 0)) == 70

    # Clipping contract: features clipped at |3.0|; no NaN/Inf downstream.
    feats = [f"feat_{i}" for i in range(70)]
    X = frame.select(feats).to_numpy()
    assert int((~np.isfinite(X)).sum()) == 0
    assert float(np.abs(X).max()) <= 3.000001

    # BUG-234 inversion pin: feat_41/42 structurally 0.0 under a 55-bar HTF
    # window; under HTF_HISTORY_BARS=4000 they are almost-all nonzero.
    for c in ("feat_41", "feat_42"):
        assert float((frame[c] != 0).mean()) > 0.95, f"{c} stuck at 0.0 - BUG-234 regression"

    # Timezone handling: UTC-aware, sorted timestamps.
    assert str(frame["timestamp"].dtype).find("UTC") != -1
    assert frame["timestamp"].is_sorted()

    # ---- LABELS (Triple-Barrier 3-class contract) ---------------------
    eval_frame = frame.filter(pl.col("is_eval_sample") & ~pl.col("is_purged"))
    assert int(eval_frame.height) > 0
    assert set(eval_frame["label"].unique().to_list()) == {0, 1, 2}
    assert int((frame["is_eval_sample"] & frame["is_purged"]).sum()) == 0
    assert int(frame.filter(pl.col("is_purged")).height) > 0
    assert MODEL_CLASS_CONTRACT_ID == "triple_barrier_3class_v1"

    # Duplicate candles / sample ids: none (dataset never injects dups).
    assert int(frame.height - frame["timestamp"].unique().len()) == 0
    assert int(frame.height - frame["sample_id"].unique().len()) == 0

    # Class imbalance present but every class populated on this slice.
    for row in eval_frame["label"].value_counts().sort("label").to_dicts():
        assert int(row["count"]) > 0

    # ---- DATASET (lineage + trainability) ------------------------------
    assert man.get("label_origin") == "CLEAN_HISTORICAL"
    assert man.get("feature_schema_hash") == "235b8fccc96b7e0e"

    # ---- MODEL INPUT (gap-safe windows with the SSoT constant) ---------
    seq = SequenceBuilder(seq_len=CANONICAL_SEQ_LEN, max_gap_us=CANONICAL_MAX_GAP_US).build(
        eval_frame, news_enabled=False
    )
    assert int(seq["valid"].shape[0]) > 50
    assert int(seq["valid"].sum()) > 20
    assert bool(np.isfinite(seq["X"]).all())
    assert sorted(np.unique(seq["y"]).tolist()) == [0, 1, 2]

    # Scaler semantics: dataset stores RAW clipped values; the model scaler
    # (mean/std, clip [-5,+5]) is fit at train time per-fold. On this slice
    # the news block is FEATURE_DISABLED (all-zero) => near-constant column.
    sd = X.std(axis=0)
    assert float(sd.min()) < 0.01

    # ---- Leakage/contamination pins ------------------------------------
    purge_params = man.get("purge_parameters", {})
    assert int(purge_params.get("purge_gap_bars", 0)) >= 1
    assert int(purge_params.get("embargo_bars", 0)) >= 1
    assert int((frame["is_eval_sample"] & frame["is_purged"]).sum()) == 0

    # ---- Integrity -------------------------------------------------------
    sha = hashlib.sha256(store.dataset_path(dataset_id).read_bytes()).hexdigest()
    assert sha and len(sha) == 64
