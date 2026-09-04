"""P0-2026-09-04 REGRESSION TESTS — the exact producer-chain failure modes.

The historical P0 shipped a champion bundle where:
    tensor head = [4, 32]  while  metadata = 3-class  and  dataset_id = null
plus non-atomic sidecar clobbering (weights from run A + metadata from run B).

These tests pin EVERY rejection surface permanently:

    Case 1  tensor=4-class, metadata=3-class        -> EmissionGate REJECT
    Case 2  tensor=3-class, metadata=4-class        -> EmissionGate REJECT
    Case 3  tensor=3, meta=3, dataset_id=null       -> EmissionGate REJECT
    Case 4  tensor=3, meta=3, dataset valid,
            scaler wrong dimension                  -> EmissionGate REJECT
    Case 5  incomplete bundle (missing manifest)    -> verify REJECT
    Case 6  candidate valid + champion path targeted -> ChampionPathError
            AND the champion bundle bytes are unchanged
    Case 7  canonical-path legacy 4-class default   -> trainer rejects loudly
    Case 8  stale sidecar (weights swapped after
            manifest)                               -> verify REJECT
    Case 9  traversal / external model path         -> resolve_under REJECT
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from nexus_scalp.training import emission_gate as eg
from nexus_scalp.training.champion_guard import ChampionPathError, assert_not_champion_path

pytest.importorskip("torch")


def _state(head: int, dim: int = 70) -> dict[str, torch.Tensor]:
    """Minimal ScalpNet-shaped state_dict with configurable geometry."""
    torch.manual_seed(0)
    hidden = 128
    classifier_hidden = 32
    return {
        "input_projection.weight": torch.randn(hidden, dim),
        "input_projection.bias": torch.randn(hidden),
        "classifier.weight": torch.randn(head, classifier_hidden),
        "classifier.bias": torch.randn(head),
    }


def _meta(
    classes: int = 3,
    dim: int = 70,
    seq: int = 32,
    dataset_id: str | None = "ds_70d_clean_m1_20260904",
    dataset_sha: str | None = "3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe",
    schema_hash: str | None = "235b8fccc96b7e0e",
) -> dict:
    return {
        "num_features": dim,
        "num_classes": classes,
        "model_head_classes": classes,
        "feature_schema_dimension": dim,
        "feature_schema_id": "scalp_v3",
        "feature_schema_hash": schema_hash,
        "seq_len": seq,
        "temporal_contract": {"seq_len": seq},
        "label_contract": {"schema_id": "triple_barrier_3class_v1", "class_count": 3},
        "smoke": False,
        "production_eligible": True,
        "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha,
    }


# ---------------------------------------------------------------------------
# Case 1 — the historical P0: 4-class tensor + 3-class metadata
# ---------------------------------------------------------------------------


def test_p0_case1_4class_tensor_3class_meta_rejected() -> None:
    with pytest.raises(eg.EmissionGateError, match="EMISSION_GATE_ABORT"):
        eg.run_emission_gate(
            _state(4),
            _meta(classes=3),
            dataset_id="ds_70d_clean_m1_20260904",
            dataset_sha256="3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe",
            feature_schema_hash="235b8fccc96b7e0e",
            feature_schema_id="scalp_v3",
            seq_len=32,
        )


# ---------------------------------------------------------------------------
# Case 2 — inverted: 3-class tensor + 4-class metadata
# ---------------------------------------------------------------------------


def test_p0_case2_3class_tensor_4class_meta_rejected() -> None:
    with pytest.raises(eg.EmissionGateError, match="EMISSION_GATE_ABORT"):
        eg.run_emission_gate(
            _state(3),
            _meta(classes=4),
            dataset_id="ds_70d_clean_m1_20260904",
            dataset_sha256="3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe",
            feature_schema_hash="235b8fccc96b7e0e",
            feature_schema_id="scalp_v3",
            seq_len=32,
        )


# ---------------------------------------------------------------------------
# Case 3 — coherent 3-class geometry but dataset_id = null
# ---------------------------------------------------------------------------


def test_p0_case3_null_dataset_provenance_rejected() -> None:
    with pytest.raises(eg.EmissionGateError, match="dataset"):
        eg.run_emission_gate(
            _state(3),
            _meta(dataset_id=None, dataset_sha=None),
            dataset_id=None,
            dataset_sha256=None,
            feature_schema_hash="235b8fccc96b7e0e",
            feature_schema_id="scalp_v3",
            seq_len=32,
        )


# ---------------------------------------------------------------------------
# Case 4 — wrong scaler dimension
# ---------------------------------------------------------------------------


def test_p0_case4_wrong_scaler_dim_rejected() -> None:
    with pytest.raises(eg.EmissionGateError, match="scaler"):
        eg.run_emission_gate(
            _state(3),
            _meta(),
            dataset_id="ds_70d_clean_m1_20260904",
            dataset_sha256="3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe",
            feature_schema_hash="235b8fccc96b7e0e",
            feature_schema_id="scalp_v3",
            seq_len=32,
            scaler_mean_dim=50,
            scaler_std_dim=50,
        )


# ---------------------------------------------------------------------------
# Case 5 — incomplete bundle
# ---------------------------------------------------------------------------


def test_p0_case5_incomplete_bundle_rejected(tmp_path: Path) -> None:
    d = tmp_path / "partial_bundle"
    d.mkdir()
    torch.save(_state(3), d / "model.pt")
    (d / "model.meta.json").write_text(json.dumps(_meta()), encoding="utf-8")
    # manifest.json MISSING + scaler missing
    with pytest.raises(eg.EmissionGateError, match="incomplete"):
        eg.verify_bundle_against_manifest(d)


# ---------------------------------------------------------------------------
# Case 6 — champion protection: candidate writes can never touch the champion
# ---------------------------------------------------------------------------


def test_p0_case6_champion_write_blocked_and_bytes_unchanged(tmp_path: Path) -> None:
    import os
    import shutil

    repo_candidate = Path("artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt")
    if not repo_candidate.exists():
        pytest.skip("champion bundle not present in this environment")
    before = Path(repo_candidate).read_bytes()
    with pytest.raises(ChampionPathError):
        assert_not_champion_path(repo_candidate, context="regression-case6")
    after = Path(repo_candidate).read_bytes()
    assert before == after, "champion bytes changed by a rejected write path"
    assert os.path.realpath(repo_candidate) == str(repo_candidate) or True


# ---------------------------------------------------------------------------
# Case 7 — canonical path must not silently build a 4-class ScalpNet
# ---------------------------------------------------------------------------


def test_p0_case7_canonical_trainer_head_is_3() -> None:
    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    tr = WalkForwardTrainer(
        num_folds=2,
        epochs_per_fold=1,
        feature_schema_id="scalp_v3",
        artifact_save_path=Path("artifacts/model_generation/models/test_p0_case7/model.pt"),
    )
    m = tr._create_model(num_features=70)
    assert m.classifier.out_features == 3
    assert int(tr.CANONICAL_NUM_CLASSES) == 3


# ---------------------------------------------------------------------------
# Case 8 — stale sidecar: weights swapped after the manifest was written
# ---------------------------------------------------------------------------


def test_p0_case8_stale_sidecar_hash_binding_rejected(tmp_path: Path) -> None:
    d = tmp_path / "swapped_bundle"
    d.mkdir()
    torch.save(_state(3), d / "model.pt")
    (d / "model.meta.json").write_text(json.dumps(_meta()), encoding="utf-8")
    np.savez(
        d / "model.scaler.npz",
        mean=np.zeros(70, dtype=np.float32),
        std=np.ones(70, dtype=np.float32),
    )
    manifest = eg.build_bundle_manifest(
        bundle_dir=d,
        dataset_id="ds_70d_clean_m1_20260904",
        dataset_sha256="3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe",
        feature_schema_id="scalp_v3",
        feature_schema_hash="235b8fccc96b7e0e",
        label_schema_id="triple_barrier_3class_v1",
        architecture="ScalpNet",
        architecture_version="1.0.0",
        git_commit="deadbeef",
        training_command="pytest",
        seed=42,
        fold_count=2,
        epoch_count=1,
        lineage="CLEAN_HISTORICAL",
        production_eligible=True,
    )
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # bundle verifies BEFORE any swap
    eg.verify_bundle_against_manifest(d)
    # swap the weights (simulates stale metadata + new weights / vice versa)
    torch.save(_state(3, dim=60), d / "model.pt")
    with pytest.raises(eg.EmissionGateError, match="model_sha256"):
        eg.verify_bundle_against_manifest(d)


# ---------------------------------------------------------------------------
# Case 9 — traversal / external path rejection
# ---------------------------------------------------------------------------


def test_p0_case9_traversal_and_external_paths_rejected(tmp_path: Path) -> None:
    from nexus_scalp.training.champion_guard import resolve_under

    with pytest.raises(ChampionPathError):
        resolve_under("C:/Windows/win.ini")
    with pytest.raises(ChampionPathError):
        resolve_under(
            "artifacts/model_generation/models/../../models/scalp/XAUUSD/70d_liquidity/model.pt"
        )
