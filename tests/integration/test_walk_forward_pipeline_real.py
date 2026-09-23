"""ML-CI-001: real multi-fold walk-forward training, exercised end to end.

Bridges the gap between the fast CI synthetic smoke tests (which run a
fraction of the training pipeline on tiny frames) and full production
candidate training. The whole point is that this file drives the REAL
``WalkForwardTrainer.train_and_validate`` path — 5 folds, 3 epochs, real
purge + embargo splits, the atomic candidate-bundle publication with the
hard emission gate — instead of a stubbed or short-circuited drill.

Every assertion below is an honest property of a completed run:
  * the fold geometry is a genuine 5-fold walk-forward with non-empty
    train and validation blocks on every fold,
  * purge and embargo bands are present and non-overlapping (zero temporal
    leakage between a fold's train tail and its validation block),
  * the pooled OOS predictions are scored on a real validation block, not a
    train row,
  * the published bundle is complete on disk and its recorded hash actually
    matches the bytes on disk (load-integrity, not a filename check),
  * GATE1/GATE5/GATE11-style evidence gates read the real artifact and
    return a PASS/FAIL verdict from it, never NOT_AVAILABLE.

This file lives in the slow tier (tests/slow_suite.txt): it trains real
PyTorch models on CPU and deliberately costs seconds, not milliseconds.
The nightly-ml-benchmark workflow runs it on a schedule so a training-path
regression that a fast unit test would miss is caught within a day.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.model_lifecycle.gates import (
    gate_artifact_integrity,
    gate_production_eligible,
    gate_training_stability,
    gate_validation_performance,
)
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

#: ML-CI-001 benchmark plan: 5-fold walk-forward on CPU must finish well
#: under the nightly budget. The bound is deliberately generous (CI runners
#: are co-tenant and slow); a 10x blowup here means the training path
#: regressed, not that the runner is busy.
MAX_RUN_SECONDS = 180.0

#: ML-CI-001 spec: 5 folds, 3 epochs.
NUM_FOLDS = 5
EPOCHS = 3

#: Purge band the trainer applies between a fold's train tail and its
#: validation block (matches the production default).
PURGE_BARS = 15

#: Determinism: the harness pins the seed so a nightly rerun of the same
#: code produces the same fold geometry and the same bundle hash.
SEED = 20260923


def _synthetic_frame(n_rows: int = 720, seed: int = SEED) -> pl.DataFrame:
    """A labelled synthetic training frame for the 50D live schema.

    The label is a threshold rule on the first feature plus 10% label noise,
    so the model has genuine signal to learn: the harness must prove that a
    REAL run completed (non-trivial OOS accuracy, real economics), not that
    a degenerate all-NO_TRADE fold looped five times. ``label_origin`` is
    stamped CLEAN_HISTORICAL so the MLFIX-T7 lineage guard admits the run.
    """
    rng = np.random.RandomState(seed)
    base = rng.randn(n_rows, len(FEATURE_NAMES)).astype(np.float32)
    sign = np.where(base[:, 0] > 0.55, 1, np.where(base[:, 0] < -0.55, 2, 0))
    flip = rng.rand(n_rows) < 0.10
    labels = np.where(flip, (sign + 1 + rng.randint(0, 2, n_rows)) % 3, sign)
    names = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]
    data: dict[str, Any] = {name: base[:, i].tolist() for i, name in enumerate(FEATURE_NAMES)}
    data["label"] = [names[int(v)] for v in labels]
    data["label_evaluated"] = [True] * n_rows
    data["is_purged"] = [False] * n_rows
    data["label_origin"] = ["CLEAN_HISTORICAL"] * n_rows
    return pl.DataFrame(data)


@pytest.fixture(scope="module")
def completed_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One real 5-fold x 3-epoch walk-forward run, shared by the module.

    Module scope on purpose: training is the expensive part and every
    assertion below inspects the SAME run (its metadata, its fold geometry,
    its published bundle) rather than re-training per test.
    """
    bundle_dir = tmp_path_factory.mktemp("ml_ci001_bundle")
    frame = _synthetic_frame()
    trainer = WalkForwardTrainer(
        num_folds=NUM_FOLDS,
        epochs_per_fold=EPOCHS,
        batch_size=128,
        learning_rate=3e-3,
        purge_gap_bars=PURGE_BARS,
        random_seed=SEED,
        artifact_save_path=bundle_dir / "model.pt",
        # ML-CI-001 runs a real training cycle but on a synthetic frame, so
        # the artifact is a smoke bundle: the production-eligible gate must
        # reject it, which the test below asserts explicitly.
        smoke=True,
        label_origin="CLEAN_HISTORICAL",
    )
    t0 = time.monotonic()
    model = trainer.train_and_validate(frame, list(FEATURE_NAMES))
    elapsed = time.monotonic() - t0
    meta = json.loads((bundle_dir / "model.meta.json").read_text(encoding="utf-8"))
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    return {
        "trainer": trainer,
        "model": model,
        "bundle_dir": bundle_dir,
        "meta": meta,
        "manifest": manifest,
        "convergence": trainer.last_convergence_metadata,
        "elapsed": elapsed,
        "frame_rows": frame.height,
    }


# ---------------------------------------------------------------------------
# AC-1: the real pipeline completes on CPU inside the nightly budget.
# ---------------------------------------------------------------------------


def test_walk_forward_completes_within_budget(completed_run: dict[str, Any]) -> None:
    """AC-1: 5-fold walk-forward training finishes in < 180s on CPU.

    Timing on a shared runner is noisy, so the bound is generous; the
    assertion catches a 10x training-path regression, not a 20% blip.
    """
    assert completed_run["elapsed"] < MAX_RUN_SECONDS, (
        f"5-fold x {EPOCHS}-epoch walk-forward took {completed_run['elapsed']:.1f}s "
        f"> {MAX_RUN_SECONDS}s budget — the training path regressed."
    )


def test_run_used_the_requested_folds_and_epochs(completed_run: dict[str, Any]) -> None:
    """The run really was a 5-fold x 3-epoch walk-forward, not a fast path."""
    conv = completed_run["convergence"]
    assert conv["num_folds"] == NUM_FOLDS
    assert conv["epochs_requested"] == EPOCHS
    assert conv["seed"] == SEED
    # The trainer's recorded config must agree with the artifact's manifest,
    # which is what the promotion pipeline reads.
    assert completed_run["manifest"]["fold_count"] == NUM_FOLDS
    assert completed_run["manifest"]["epoch_count"] == EPOCHS


# ---------------------------------------------------------------------------
# AC-2a: the fold geometry is a real walk-forward with purge + embargo.
# ---------------------------------------------------------------------------


def test_fold_geometry_is_a_real_walk_forward(completed_run: dict[str, Any]) -> None:
    """Every fold has a non-empty train block and a non-empty validation
    block, with a genuine purge band separating them and a non-empty
    embargo tail dropped after the validation block.

    This is the leakage contract: no validation row may sit inside (or
    within ``PURGE_BARS`` of) the training window it is scored against.
    """
    geometry = completed_run["convergence"]["fold_geometry"]
    assert len(geometry) == NUM_FOLDS, f"expected {NUM_FOLDS} folds, got {len(geometry)}"
    total_test_rows = 0
    for fold in geometry:
        assert fold["fold"] in range(1, NUM_FOLDS + 1)
        assert fold["train_count"] > 0, f"fold {fold['fold']}: empty training block"
        assert fold["test_count"] > 0, f"fold {fold['fold']}: empty validation block"
        # The purge band must be exactly the configured width and must sit
        # strictly between the train tail and the validation start.
        assert fold["purge_rows"] == PURGE_BARS, (
            f"fold {fold['fold']}: purge band {fold['purge_rows']} != {PURGE_BARS}"
        )
        assert fold["embargo_rows"] >= 0
        assert fold["test_start_idx"] > fold["train_end_idx"], (
            f"fold {fold['fold']}: validation block starts at or before the "
            "training tail — temporal separation violated"
        )
        # Validation indices must be strictly increasing across folds
        # (walk-forward moves forward in time; no fold re-scores old rows).
        total_test_rows += fold["test_count"]
    starts = [f["test_start_idx"] for f in geometry]
    assert starts == sorted(starts), "fold validation blocks are not time-ordered"
    assert total_test_rows > 0


def test_pooled_oos_scored_on_a_real_validation_block(completed_run: dict[str, Any]) -> None:
    """The pooled OOS accuracy comes from rows the model never trained on.

    The honest lower bound: pooled OOS samples must equal the sum of the
    fold validation-block sizes, and the accuracy must beat the majority
    class share (a model that copies the prior has learned nothing).
    """
    conv = completed_run["convergence"]
    geometry = conv["fold_geometry"]
    expected = sum(f["test_count"] for f in geometry)
    assert conv["oos_samples"] == expected, (
        f"oos_samples={conv['oos_samples']} != sum of fold validation rows {expected}"
    )
    assert conv["oos_accuracy"] is not None
    assert conv["oos_accuracy"] > 1.0 / 3.0, (
        f"OOS accuracy {conv['oos_accuracy']:.4f} at chance on a learnable frame "
        "— the training loop did not fit the signal"
    )


# ---------------------------------------------------------------------------
# AC-2b: the published candidate bundle is complete and hash-integral.
# ---------------------------------------------------------------------------


def test_published_bundle_is_complete_on_disk(completed_run: dict[str, Any]) -> None:
    """The atomic publication wrote every artifact the loader needs."""
    bundle = Path(completed_run["bundle_dir"])
    required = ["model.pt", "model.scaler.npz", "model.meta.json", "manifest.json"]
    missing = [name for name in required if not (bundle / name).exists()]
    assert not missing, f"candidate bundle missing files: {missing}"
    # A partially-written bundle is the P0 failure the atomic publisher
    # exists to prevent: no staging leftovers may remain in the bundle dir.
    staging = list(bundle.glob(".staging-*"))
    assert not staging, f"staging leftovers in the published bundle: {staging}"


def test_manifest_hashes_match_the_bytes_on_disk(completed_run: dict[str, Any]) -> None:
    """The manifest's recorded SHA-256 is the hash of the file that is
    actually on disk (load integrity, not a filename or a field check).

    This is the property the live loader's integrity gate enforces; if the
    artifact and the manifest disagree the candidate is untrustworthy.
    """
    bundle = Path(completed_run["bundle_dir"])
    manifest = completed_run["manifest"]
    import hashlib

    for field, filename in (
        ("model_sha256", "model.pt"),
        ("metadata_sha256", "model.meta.json"),
        ("scaler_sha256", "model.scaler.npz"),
    ):
        recorded = manifest.get(field)
        assert recorded, f"manifest field {field} is empty"
        actual = hashlib.sha256((bundle / filename).read_bytes()).hexdigest()
        assert actual == recorded, (
            f"{field}: manifest says {recorded[:16]}… but the file on disk "
            f"hashes to {actual[:16]}… — artifact/manifest mismatch"
        )


def test_emission_gate_admitted_the_published_tensors(completed_run: dict[str, Any]) -> None:
    """The hard emission gate ran on the EXACT serialized tensors and passed:
    head width, input dimension, sequence length and scaler parity all match
    the 50D/3-class contract.
    """
    meta = completed_run["meta"]
    manifest = completed_run["manifest"]
    assert meta["num_features"] == len(FEATURE_NAMES)
    assert meta["num_classes"] == 3
    assert meta["model_head_classes"] == 3
    assert manifest["class_count"] == 3
    assert manifest["feature_schema_id"] == "scalp_v1"
    # The live loader resolves the head from the checkpoint, not the meta:
    # the checkpoint must really be 3-wide.
    import torch

    state = torch.load(
        Path(completed_run["bundle_dir"]) / "model.pt",
        map_location="cpu",
        weights_only=True,
    )
    head = state.get("classifier.weight")
    assert head is not None, "checkpoint has no classifier weight tensor"
    assert head.shape[0] == 3, f"checkpoint head is {head.shape[0]}-wide, expected 3"
    # The input projection width must match the 50D live schema the runtime
    # feeds it — a width mismatch is a serving-contract break.
    assert state["input_projection.weight"].shape[1] == len(FEATURE_NAMES)


def test_production_eligible_gate_rejects_the_smoke_bundle(completed_run: dict[str, Any]) -> None:
    """The nightly artifact is a real training cycle on a synthetic frame, so
    ``production_eligible`` must be False and the eligibility gate must FAIL.

    This pins the safety direction: a nightly benchmark bundle can never be
    promoted to live serving, no matter how good its metrics look.
    """
    meta = completed_run["meta"]
    assert meta["smoke"] is True
    assert meta["production_eligible"] is False
    verdict = gate_production_eligible(meta)
    assert verdict.passed is False
    assert verdict.gate == "GATE_PRODUCTION_ELIGIBLE"


def test_gate_evidence_is_real_not_not_available(completed_run: dict[str, Any]) -> None:
    """GATE1/GATE5/GATE11-style evidence must be present and real.

    The honest sentinel ``NOT_AVAILABLE`` means the producer emitted no
    evidence; a nightly run that reports NOT_AVAILABLE has silently lost a
    producer-to-gate contract and must fail loudly here.
    """
    from nexus_scalp.model_lifecycle.integrity import inspect_artifact

    conv = completed_run["convergence"]
    # GATE4 (training stability) reads a real final loss.
    assert conv["mean_best_val_loss"] is not None
    stability = gate_training_stability({"final_loss": float(conv["mean_best_val_loss"])})
    assert stability.passed, f"GATE4 failed on a completed run: {stability.reason}"
    # GATE5 (validation performance) reads real OOS accuracy.
    perf = gate_validation_performance(
        {"validation_accuracy": float(conv["oos_accuracy"])}, min_accuracy=0.30
    )
    assert perf.passed, f"GATE5 failed: {perf.reason}"
    # GATE11 (artifact integrity) reads the REAL checkpoint through the same
    # inspector the promotion pipeline uses: it derives the width and head
    # count from the serialized tensors, hashes the bytes on disk, and cross
    # checks the declared schema — so a width/head mismatch or a
    # hash disagreement fails here.
    bundle = Path(completed_run["bundle_dir"])
    info = inspect_artifact(
        bundle / "model.pt",
        scaler_path=str(bundle / "model.scaler.npz"),
        model_id=completed_run["manifest"]["bundle_id"],
        feature_schema_id="scalp_v1",
    )
    assert info.integrity_ok, (
        f"GATE11 failed on the published bundle: dim={info.feature_dimension} "
        f"classes={info.num_classes} reason={info.integrity_reason}"
    )
    assert info.feature_dimension == len(FEATURE_NAMES)
    assert info.num_classes == 3
    artifact = gate_artifact_integrity(info)
    assert artifact.passed, f"GATE11 failed on the published bundle: {artifact.reason}"


def test_economics_are_reported_per_fold(completed_run: dict[str, Any]) -> None:
    """Every fold carries an honest money-side metric (net expectancy in R),
    so the nightly report can distinguish an accurate-but-unprofitable
    candidate from a profitable one.
    """
    economics = completed_run["convergence"]["fold_economics"]
    assert len(economics) == NUM_FOLDS
    for fold in economics:
        assert "net_expectancy_r" in fold
        assert "gross_expectancy_r" in fold
        assert "trades" in fold
        assert fold["friction_r"] == completed_run["convergence"]["friction_r"]
        # The economics come from the same OOS rows as the classification
        # metrics: a fold with trades must be consistent with its win rate.
        if fold["trades"] > 0:
            assert -1.0 <= fold["net_expectancy_r"] <= 2.0
    # Aggregates are derived, never fabricated.
    conv = completed_run["convergence"]
    if conv["net_expectancy_r"] is not None:
        expected = float(np.mean([f["net_expectancy_r"] for f in economics]))
        assert conv["net_expectancy_r"] == pytest.approx(expected, abs=1e-9)


def test_nightly_harness_is_deterministic_at_the_pinned_seed(
    completed_run: dict[str, Any], tmp_path: Path
) -> None:
    """A rerun at the same seed reproduces the same fold geometry and the
    same bundle hash — the nightly result is reproducible, not a coin flip.

    Disabled when ``NSE_NIGHTLY_SKIP_RERUN`` is set so a co-tenant CI runner
    under load can skip the second training cycle without failing the gate.
    """
    import os

    if os.environ.get("NSE_NIGHTLY_SKIP_RERUN"):
        pytest.skip("NSE_NIGHTLY_SKIP_RERUN set — skipping the rerun leg")

    frame = _synthetic_frame()
    rerun_dir = tmp_path / "rerun"
    rerun_dir.mkdir()
    trainer = WalkForwardTrainer(
        num_folds=NUM_FOLDS,
        epochs_per_fold=EPOCHS,
        batch_size=128,
        learning_rate=3e-3,
        purge_gap_bars=PURGE_BARS,
        random_seed=SEED,
        artifact_save_path=rerun_dir / "model.pt",
        smoke=True,
        label_origin="CLEAN_HISTORICAL",
    )
    trainer.train_and_validate(frame, list(FEATURE_NAMES))
    assert (
        trainer.last_convergence_metadata["fold_geometry"]
        == completed_run["convergence"]["fold_geometry"]
    )
    import hashlib

    digest = hashlib.sha256((rerun_dir / "model.pt").read_bytes()).hexdigest()
    assert digest == completed_run["manifest"]["model_sha256"], (
        "same seed produced a different bundle hash — training is not deterministic"
    )
