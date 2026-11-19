"""A2 data lineage test — bounded dataset/build path (fast, deterministic).

Verifies the FULL lineage RAW -> CLEAN -> FEATURES -> LABELS -> DATASET ->
MODEL INPUT via the SAME canonical builder the authoritative
ds_70d_clean_m1_20260904 artifact uses, on a small 5k-bar M1 frame so the
suite finishes in seconds, not minutes.

The original shape read ``data/raw/XAUUSD_M1.csv`` — a gitignored,
download-only machine file — behind a skipif, so in CI the module collected
and skipped forever: a green-looking suite that exercised nothing (the same
silent-zero-coverage defect class ML-QA-019 remediated for the parity
suite). The bars are now produced by the deterministic synthetic generator
``generate_synthetic_bars`` (M1, tz-aware UTC, canonical OHLCV — the exact
shape ``build_70d_dataset`` consumes from a broker fetch; the same importer
and seed pattern as ``test_70d_bug106_incremental_phase19.py``), and the
artifact store is redirected into the test's tmp dir, so the contract runs
in EVERY checkout instead of skipping, and never touches the real
artifacts/ tree.

Pins that describe the DATA's market character (BUG-234's almost-all-nonzero
HTF features, label-population) are asserted against the synthetic frame
itself — the builder machinery under test is identical; only the bar source
changed. Schema-hash pins are data-independent and unchanged.

Reuse (from repo root):
    .venv/Scripts/python.exe -m pytest tests/unit/test_a2_data_lineage_bounded.py -q
"""

from __future__ import annotations

import hashlib

import numpy as np
import polars as pl
import pytest

from nexus_scalp.features.schema_contract import canonical_feature_names, feature_schema_hash
from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.schema_v2 import build_70d_dataset, verify_70d_artifact
from nexus_scalp.model_generation.sequence import SequenceBuilder
from nexus_scalp.model_generation.temporal_contract import (
    CANONICAL_MAX_GAP_US,
    CANONICAL_SEQ_LEN,
)
from nexus_scalp.model_lifecycle.model_class_contract import MODEL_CLASS_CONTRACT_ID
from scripts.data.ingest_historical_candles import generate_synthetic_bars

_BAR_COUNT = 5000
_SEED = 42


@pytest.fixture(scope="module")
def slice_df() -> pl.DataFrame:
    """Deterministic 5k-bar synthetic M1 tail (replaces the git-absent CSV)."""
    return generate_synthetic_bars(symbol="XAUUSD", count=_BAR_COUNT, seed=_SEED)


def test_a2_bounded_dataset_build_and_lineage(slice_df, tmp_path) -> None:
    """RAW -> CLEAN -> FEATURES -> LABELS -> DATASET -> MODEL INPUT on 5k M1 bars."""

    store = ArtifactStore(root=tmp_path / "artifacts")
    dataset_id = "a2_lineage_probe_5k"
    # Deterministic rerun: blow away the prior probe artifact (store has no
    # delete helper; the path is the single source of writability).
    for p_ in (store.dataset_path(dataset_id), store.dataset_manifest_path(dataset_id)):
        try:
            p_.unlink(missing_ok=True)
        except Exception:
            pass

    assert int(slice_df.height) == _BAR_COUNT

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
    # Same verifier contract as test_70d_parity_task3.py: a news_frame=None
    # build is rejected ONLY by the news gate (dims 50..59 are contract
    # zeros -> FEATURE_DISABLED); every structural check must still pass.
    assert vf.get("ok") is False, f"verify_70d_artifact unexpected verdict: {vf}"
    assert int(vf.get("feature_count", 0)) == 70
    assert vf["dimension_ok"] is True
    assert vf["schema_id_ok"] is True
    assert vf["schema_hash_ok"] is True
    assert vf["all_finite"] is True
    assert vf["all_in_range"] is True
    assert vf["timestamp_sane"] is True
    assert vf["news_coverage"]["ok"] is False
    assert vf["news_coverage"]["news_status"] == "FEATURE_DISABLED"

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
    # (mean/std, clip [-5,+5]) is fit at train time per-fold. On this frame
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
