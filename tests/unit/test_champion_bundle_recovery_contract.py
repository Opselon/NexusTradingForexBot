"""Regression: champion-bundle recovery / serving-resolution contract.

Proves the artifact-trust chain end-to-end on the serving path:
  1. a coherent bundle (weights+manifest emitted together) loads through the
     REAL LiveEngine construction path on the DEFAULT AppConfig artifact
     contract;
  2. weights mutated AFTER manifest publication fail closed with
     HASH_MISMATCH (no silent serving);
  3. a PARTIAL publication (weights replaced, manifest stale / missing) can
     never become the serving bundle — engine construction refuses.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock

import pytest
import torch

from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.model_lifecycle.load_integrity import (
    ArtifactIntegrityError,
    ArtifactIntegrityStatus,
    verify_artifact_integrity,
)


def _train_briefly(model, num_features: int, classes: int) -> None:
    """Take 30 AdamW steps on a fixed synthetic batch (same recipe as
    ``test_promotion_rejects_degenerate_model.test_thresholds_hold_...``).

    The P0 serving gate in ModelBundleStore refuses fresh-init and
    behavioral-degenerate weights on the load path, so hermetic fixtures that
    boot the engine must mint a genuinely TRAINED artifact — fix the
    fixture, not the gate (precedent: ab9db747 / AGENT-12 c004 repair).
    """
    torch.manual_seed(999)
    model.train()
    gen = torch.Generator().manual_seed(1234)
    X = torch.randn(256, num_features, generator=gen)
    y = torch.randint(0, classes, (256,), generator=gen)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
    for _ in range(30):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(model(X, return_logits=True), y)
        loss.backward()
        opt.step()
    model.eval()


def _write_verified_bundle(directory, num_features: int = 70, classes: int = 3):
    """Emit model.pt + manifest.json as ONE coherent trust-chain pair.

    The manifest digest is computed FROM the emitted weights — the exact
    production contract (what is on disk must equal what the manifest says).
    Returns (model_path, manifest_path, digest).
    """
    from nexus_scalp.models.scalp_net import ScalpNet

    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / "model.pt"
    model = ScalpNet(num_features=num_features, num_classes=classes)
    _train_briefly(model, num_features, classes)
    torch.save(model.state_dict(), model_path)

    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    manifest = {
        "manifest_version": "1.0.0",
        "bundle_id": f"bundle_{digest[:12]}",
        "model_sha256": digest,
        "metadata_sha256": hashlib.sha256(b"meta-absent-cold-start").hexdigest(),
        "scaler_sha256": "",
        "feature_schema_id": "scalp_v3" if num_features == 70 else "scalp_v1",
        "input_dim": num_features,
        "class_count": classes,
        "architecture": "ScalpNet",
        "architecture_version": "1.0.0",
        "lineage": "CLEAN_HISTORICAL",
        "production_eligible": True,
        "smoke": True,
        "dataset_id": "test",
        "dataset_sha256": "0" * 64,
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return model_path, manifest_path, digest


def _engine_on(model_path, mode: ExecutionMode = ExecutionMode.PAPER) -> LiveEngine:
    cfg = AppConfig()
    cfg.execution.mode = mode
    cfg.model.model_artifact_path = str(model_path)
    return LiveEngine(config=cfg, adapter=MagicMock(), audit_repo=MagicMock())


def test_coherent_bundle_loads_and_resolves_as_serving_bundle(tmp_path) -> None:
    """Weights + manifest published together -> strict 70D load, integrity
    VERIFIED, and the bundle resolves as the engine's serving bundle."""
    model_path, _manifest_path, digest = _write_verified_bundle(tmp_path / "bundle")

    verdict = verify_artifact_integrity(model_path)
    assert verdict.status is ArtifactIntegrityStatus.VERIFIED
    assert verdict.expected_sha256 == digest == verdict.actual_sha256

    engine = _engine_on(model_path)
    bundle = engine._bundle
    assert bundle.artifact_path == model_path
    # 70D input contract actually served
    assert int(bundle.model.input_projection.weight.shape[1]) == 70
    assert int(bundle.model.classifier.weight.shape[0]) == 3


def test_weights_mutated_after_manifest_publication_fail_closed(tmp_path) -> None:
    """The fine-tune hash-break class: weights swapped AFTER the manifest was
    published must be rejected with HASH_MISMATCH — never served."""
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    bundle_dir = tmp_path / "tampered"
    model_path, _manifest_path, digest = _write_verified_bundle(bundle_dir)
    assert verify_artifact_integrity(model_path).status is ArtifactIntegrityStatus.VERIFIED

    # Mutate the weights AFTER publication (in-place, the exact failure class)
    state = torch.load(model_path, map_location="cpu", weights_only=True)
    first_key = next(iter(state))
    state[first_key] = state[first_key] + 1.0
    torch.save(state, model_path)

    with pytest.raises(ArtifactIntegrityError) as excinfo:
        _engine_on(model_path)
    assert excinfo.value.verdict.status is ArtifactIntegrityStatus.HASH_MISMATCH
    assert excinfo.value.verdict.actual_sha256 != digest


def test_partial_publication_stale_manifest_cannot_become_serving_bundle(tmp_path) -> None:
    """Interruption between 'weights replaced' and 'manifest committed' leaves
    a stale manifest — the pair must refuse to serve (commit-marker semantics)."""
    bundle_dir = tmp_path / "partial_stale_manifest"
    model_path, manifest_path, _digest = _write_verified_bundle(bundle_dir)

    # Simulate the crash window: new weights landed, manifest still describes
    # the OLD bytes (classic partial publication).
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    newer = ScalpNet(num_features=70, num_classes=3)
    newer.eval()
    torch.save(newer.state_dict(), model_path)  # weights replaced, manifest NOT updated

    with pytest.raises(ArtifactIntegrityError) as excinfo:
        _engine_on(model_path)
    assert excinfo.value.verdict.status is ArtifactIntegrityStatus.HASH_MISMATCH


def test_partial_publication_missing_manifest_is_legacy_unverified_never_served(tmp_path) -> None:
    """Weights present but the manifest (commit marker) never landed -> the
    artifact is LEGACY_UNVERIFIED, and the engine's default policy refuses to
    serve it (no silent opt-in)."""
    bundle_dir = tmp_path / "partial_no_manifest"
    model_path, manifest_path, _digest = _write_verified_bundle(bundle_dir)
    manifest_path.unlink()  # publication interrupted before the commit marker

    # Direct verifier: the artifact classifies as LEGACY_UNVERIFIED (raises
    # under the default no-opt-in policy — never silently servable).
    with pytest.raises(ArtifactIntegrityError) as verdict_err:
        verify_artifact_integrity(model_path)
    assert verdict_err.value.verdict.status is ArtifactIntegrityStatus.LEGACY_UNVERIFIED

    # Engine path: construction refuses (no silent serving of unverified weights).
    with pytest.raises(ArtifactIntegrityError) as excinfo:
        _engine_on(model_path)
    assert excinfo.value.verdict.status is ArtifactIntegrityStatus.LEGACY_UNVERIFIED
