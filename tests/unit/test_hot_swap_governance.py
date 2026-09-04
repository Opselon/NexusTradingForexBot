"""Hot-swap governance tests (P0 security hardening, 2026-09-04).

The web route /api/runtime-config/model-swap forwards an arbitrary
model_artifact_path to LiveEngine.hot_swap_model. The hardening contract:

    * path outside the approved artifact roots / traversal / external file
      -> rejected with reason PATH_REJECTED, serving model untouched
    * bundle manifest hash mismatch (stale sidecar pairing)            -> BUNDLE_HASH_MISMATCH
    * manifest or meta with production_eligible=False                  -> CANDIDATE_NOT_PRODUCTION_ELIGIBLE
    * valid isolated candidate bundle (verified manifest)              -> loads successfully

These tests drive the REAL hot_swap_model on a minimal LiveEngine substitute:
the swap logic under test is the engine method (not a mock of it) — the
engine instance is a lightweight object inheriting from LiveEngine with only
the bundle/config pieces the swap path touches.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from nexus_scalp.training import emission_gate as eg


def _make_valid_bundle(d: Path, head: int = 3, dim: int = 70, eligible: bool = True) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(0)
    state = {
        "input_projection.weight": torch.randn(128, dim),
        "input_projection.bias": torch.randn(128),
        "classifier.weight": torch.randn(head, 32),
        "classifier.bias": torch.randn(head),
    }
    torch.save(state, d / "model.pt")
    meta = {
        "num_features": dim,
        "num_classes": head,
        "model_head_classes": head,
        "feature_schema_dimension": dim,
        "feature_schema_id": "scalp_v3",
        "feature_schema_hash": "235b8fccc96b7e0e",
        "seq_len": 32,
        "temporal_contract": {"seq_len": 32},
        "label_contract": {"schema_id": "triple_barrier_3class_v1", "class_count": head},
        "smoke": False,
        "production_eligible": eligible,
        "dataset_id": "ds_70d_clean_m1_20260904",
        "dataset_sha256": "3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe",
    }
    (d / "model.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    np.savez(
        d / "model.scaler.npz",
        mean=np.zeros(dim, dtype=np.float32),
        std=np.ones(dim, dtype=np.float32),
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
        git_commit="test",
        training_command="pytest",
        seed=42,
        fold_count=2,
        epoch_count=1,
        lineage="CLEAN_HISTORICAL",
        production_eligible=eligible,
    )
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d / "model.pt"


class _SwapProbe:
    """Minimal stand-in exposing the REAL hot_swap_model governance path.

    hot_swap_model only touches: config.model.model_artifact_path,
    _expected_num_features_for_artifact, _load_or_create_bundle,
    _warmup_and_hash internals, _bundle_lock, _rebind_trainer_to_bundle,
    _register_active_model, runtime_config, logger. We subclass LiveEngine
    WITHOUT calling its __init__ and stub the heavy collaborators; the
    governance checks under test (path allow-list, manifest hash,
    production_eligible) run inside the real method body BEFORE any stub is
    reached, so the assertions below prove the hardening itself.
    """

    def __new__(cls):  # bypass LiveEngine.__init__
        from nexus_scalp.application.live_engine import LiveEngine

        obj = object.__new__(LiveEngine)
        return obj


def _prepare_engine(
    tmp_path: Path,
    serving: Path,
) -> object:
    """Build the swap probe (approved roots come from the autouse fixture).

    The real champion_guard resolves roots relative to the repository; tests
    run in tmp_path, so the guard's repo-root helper is redirected to
    tmp_path with an equivalent artifacts/ layout (same enforcement logic,
    isolated filesystem)."""
    obj = _SwapProbe()

    class _Cfg:
        class model:  # noqa: N801
            model_artifact_path = str(serving)

    obj.config = _Cfg()
    obj._expected_num_features_for_artifact = lambda p: 70

    def _reject_before_bundle_load(_m=None, _f=None):
        raise AssertionError("governance must reject before bundle load")

    obj._load_or_create_bundle = _reject_before_bundle_load
    return obj


@pytest.fixture(autouse=True)
def _guard_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import nexus_scalp.training.champion_guard as cg

    monkeypatch.setattr(cg, "repo_root", lambda: tmp_path)
    yield
    # monkeypatch restores automatically


@pytest.mark.asyncio
async def test_hot_swap_rejects_path_outside_artifact_root(tmp_path: Path) -> None:
    serving = tmp_path / "serving" / "model.pt"
    serving.parent.mkdir()
    serving.write_bytes(b"x")
    external = tmp_path / "evil_model.pt"
    torch.save({"input_projection.weight": torch.randn(128, 70)}, external)
    eng = _prepare_engine(tmp_path, serving)
    result = await eng.hot_swap_model(str(external))
    assert result["success"] is False
    assert result["reason"] == "PATH_REJECTED"
    assert result.get("runtime_applied") is False


@pytest.mark.asyncio
async def test_hot_swap_rejects_traversal_path(tmp_path: Path) -> None:
    serving = tmp_path / "serving" / "model.pt"
    serving.parent.mkdir()
    serving.write_bytes(b"x")
    eng = _prepare_engine(tmp_path, serving)
    result = await eng.hot_swap_model(str(tmp_path / "artifacts/models/../../evil/model.pt"))
    assert result["success"] is False
    assert result["reason"] in ("PATH_REJECTED", "ARTIFACT_MISSING")
    assert result.get("runtime_applied") is False


@pytest.mark.asyncio
async def test_hot_swap_rejects_hash_mismatch_bundle(tmp_path: Path) -> None:
    serving = tmp_path / "serving" / "model.pt"
    serving.parent.mkdir()
    serving.write_bytes(b"x")
    cand_dir = tmp_path / "artifacts" / "model_generation" / "models" / "cand_hm"
    artifact = _make_valid_bundle(cand_dir)
    # swap the weights AFTER the manifest (stale sidecar pairing)
    torch.save({"input_projection.weight": torch.randn(128, 70)}, artifact)
    eng = _prepare_engine(tmp_path, serving)
    result = await eng.hot_swap_model(str(artifact))
    assert result["success"] is False
    assert result["reason"] == "BUNDLE_HASH_MISMATCH"


@pytest.mark.asyncio
async def test_hot_swap_rejects_non_eligible_candidate(tmp_path: Path) -> None:
    serving = tmp_path / "serving" / "model.pt"
    serving.parent.mkdir()
    serving.write_bytes(b"x")
    cand_dir = tmp_path / "artifacts" / "model_generation" / "models" / "cand_ne"
    artifact = _make_valid_bundle(cand_dir, eligible=False)
    eng = _prepare_engine(tmp_path, serving)
    result = await eng.hot_swap_model(str(artifact))
    assert result["success"] is False
    assert result["reason"] == "CANDIDATE_NOT_PRODUCTION_ELIGIBLE"


@pytest.mark.asyncio
async def test_hot_swap_accepts_valid_candidate(tmp_path: Path) -> None:
    serving = tmp_path / "serving" / "model.pt"
    serving.parent.mkdir()
    serving.write_bytes(b"x")
    cand_dir = tmp_path / "artifacts" / "model_generation" / "models" / "cand_ok"
    artifact = _make_valid_bundle(cand_dir, eligible=True)
    eng = _prepare_engine(tmp_path, serving)

    class _FakeBundle:
        class _Model:
            num_features = 70

            def __call__(self, t):
                import torch as _t

                return _t.zeros(1, 3)

        model = _Model()

        class scaler:  # noqa: N801
            @staticmethod
            def transform(x):
                return x

    def _fake_load(model_path, force_fresh):
        return _FakeBundle()

    eng._load_or_create_bundle = _fake_load

    class _RC:
        def apply(self, *a, **k):
            return None

        def get_version(self):
            return 1

    eng.runtime_config = _RC()
    eng._bundle_lock = __import__("threading").Lock()
    eng._bundle = None
    eng._rebind_trainer_to_bundle = lambda: None
    eng._register_active_model = lambda model_path, replaced: None
    result = await eng.hot_swap_model(str(artifact))
    assert result["success"] is True, result
    assert result["runtime_applied"] is True
