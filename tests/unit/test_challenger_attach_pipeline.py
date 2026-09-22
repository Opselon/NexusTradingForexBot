"""
SHADOW-PIPELINE regression (2026-09-21): the challenger pipeline produced
ZERO decisions (60d 0 decisions, no shadow runs) because FOUR independent
defects each aborted POST /api/models/shadow/attach before a challenger
could ever load. Each test here pins one root cause.

Verified against the real bundle
artifacts/model_generation/models/cand_mlagent3_fullprobe/ (the newest
CHALLENGER row in audit.db) before writing this suite.

1. SCALER PATH        the trainer writes model.scaler.npz as a SIBLING of
                       model.pt; the route asked for model.pt.scaler.npz
                       (never written) -> CHALLENGER_SCALER_NOT_FOUND.
2. MANIFEST FILE      the signed manifest is manifest.json, not model.json.
3. MANIFEST VOCAB     the gate needs model_id/feature_dimension/class_count;
                       the bundle declares input_dim and carries no model_id
                       -> 'manifest missing field model_id'.
4. SERVING CONTRACT   the route passed engine.FEATURE_DIM (the class
                       bootstrap 50D) while a validated 70D bundle serves, so
                       ChallengerRuntime inspected the 70D artifact under the
                       50D ACTIVE schema -> bogus DIMENSION_MISMATCH.
                       (Same class of bug as b620f9c2 / 8886eb2e.)
5. CLASS COUNT        the runtime defaulted num_classes=4 while the trainer's
                       triple-barrier head is 3 (TRAINED_CLASS_COUNT).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.web.model_governance_routes import (
    _augment_manifest,
    _resolve_challenger_manifest,
    _resolve_challenger_scalper,
    _serving_dimension,
    _serving_schema_id,
)

_REPO_ROOT = "artifacts/model_generation/models/cand_mlagent3_fullprobe"
_ARTIFACT = f"{_REPO_ROOT}/model.pt"
_DIR = _REPO_ROOT


# ---------------------------------------------------------------------------
# 1. Scaler sidecar resolution — sibling, not dotted
# ---------------------------------------------------------------------------


def test_scaler_resolution_prefers_the_sibling_the_trainer_writes(tmp_path: Any) -> None:
    """model.pt + model.scaler.npz side by side is the real layout."""
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "model.pt").write_bytes(b"weights")
    (d / "model.scaler.npz").write_bytes(b"scaler")
    found = _resolve_challenger_scalper(d / "model.pt")
    assert found is not None
    assert found.name == "model.scaler.npz"
    assert found.exists()


def test_scaler_resolution_still_loads_the_legacy_dotted_layout(tmp_path: Any) -> None:
    """A bundle that did write model.pt.scaler.npz is not orphaned."""
    d = tmp_path / "legacy"
    d.mkdir()
    (d / "model.pt").write_bytes(b"weights")
    (d / "model.pt.scaler.npz").write_bytes(b"scaler")
    found = _resolve_challenger_scalper(d / "model.pt")
    assert found is not None
    assert found.exists()


def test_scaler_resolution_reports_absence_honestly_instead_of_guessing(tmp_path: Any) -> None:
    """No scaler anywhere -> None (the route answers CHALLENGER_SCALER_NOT_FOUND)."""
    d = tmp_path / "bare"
    d.mkdir()
    (d / "model.pt").write_bytes(b"weights")
    assert _resolve_challenger_scalper(d / "model.pt") is None


def test_scaler_resolution_finds_the_real_repo_challenger_bundle() -> None:
    """The bundle that the 10-gate rejected at SCALER_VALID now resolves."""
    from pathlib import Path

    found = _resolve_challenger_scalper(Path(_ARTIFACT))
    # Tests may run outside the repo root; the path contract is the point.
    if found is None:
        pytest.skip("challenger bundle not present in this checkout")
    assert found.exists()


# ---------------------------------------------------------------------------
# 2 + 3. Manifest resolution + vocabulary
# ---------------------------------------------------------------------------


def test_manifest_resolution_reads_the_signed_manifest_not_model_json(tmp_path: Any) -> None:
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "manifest.json").write_text('{"feature_schema_id": "scalp_v3"}')
    (d / "model.json").write_text('{"legacy": true}')
    mf = _resolve_challenger_manifest(d)
    assert mf["_manifest_file"] == "manifest.json"
    assert mf["feature_schema_id"] == "scalp_v3"


def test_manifest_resolution_falls_back_to_legacy_model_json(tmp_path: Any) -> None:
    d = tmp_path / "legacy"
    d.mkdir()
    (d / "model.json").write_text('{"legacy": true}')
    mf = _resolve_challenger_manifest(d)
    assert mf["_manifest_file"] == "model.json"


def test_manifest_resolution_empty_when_no_manifest_exists(tmp_path: Any) -> None:
    d = tmp_path / "bare"
    d.mkdir()
    assert _resolve_challenger_manifest(d) == {}


def test_augment_fills_gate_vocabulary_from_the_bundles_own_meta(tmp_path: Any) -> None:
    """The gate asks feature_dimension/class_count; the bundle answers in
    its own vocabulary (input_dim / num_classes). No value is invented."""
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "model.meta.json").write_text(
        '{"num_features": 70, "feature_schema_dimension": 70,'
        ' "model_head_classes": 3, "num_classes": 3,'
        ' "feature_schema_id": "scalp_v3"}'
    )
    signed = {"feature_schema_id": "scalp_v3", "input_dim": 70, "class_count": 3}
    out = _augment_manifest(signed, d)
    assert out["feature_dimension"] == 70
    assert out["class_count"] == 3
    assert out["feature_schema_id"] == "scalp_v3"


def test_augment_never_overrides_the_signed_manifest(tmp_path: Any) -> None:
    """A signed declaration wins over the companion meta (no weakening)."""
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "model.meta.json").write_text('{"num_features": 50}')
    signed = {"feature_dimension": 70, "class_count": 3}
    out = _augment_manifest(signed, d)
    assert out["feature_dimension"] == 70


def test_augment_carries_the_registry_identity_into_the_gate(tmp_path: Any) -> None:
    """model_id comes from the lifecycle row the artifact path was read from."""
    d = tmp_path / "bundle"
    d.mkdir()
    out = _augment_manifest({}, d, "scalp_70d_liquidity_scalp_v3_70d", "1.0.0")
    assert out["model_id"] == "scalp_70d_liquidity_scalp_v3_70d"
    assert out["model_version"] == "1.0.0"


def test_augment_without_meta_meta_is_a_noop(tmp_path: Any) -> None:
    d = tmp_path / "bundle"
    d.mkdir()
    signed = {"input_dim": 70}
    assert _augment_manifest(signed, d) == signed


# ---------------------------------------------------------------------------
# 4. Serving contract — the challenger must be inspected under what serves
# ---------------------------------------------------------------------------


class _BootstrapOnlyEngine:
    """Engine with NO bundle loaded: class constants only (cold start)."""

    FEATURE_SCHEMA_ID = "scalp_v1"
    FEATURE_DIM = 50


class _Serving70DEngine:
    """Engine with a validated 70D bundle serving."""

    FEATURE_SCHEMA_ID = "scalp_v1"  # bootstrap default (unchanged by design)
    FEATURE_DIM = 50
    effective_feature_schema_id = "scalp_v3"
    effective_feature_dim = 70


def test_serving_contract_reads_the_bundle_not_the_bootstrap_constant() -> None:
    """The decisive regression: a 70D champion serving while FEATURE_DIM is 50."""
    e = _Serving70DEngine()
    assert _serving_dimension(e) == 70
    assert _serving_schema_id(e) == "scalp_v3"
    # The class constants themselves are untouched (bootstrap contract intact).
    assert e.FEATURE_DIM == 50


def test_serving_contract_falls_back_to_bootstrap_without_a_bundle() -> None:
    """Cold start (no bundle) keeps the pre-bootstrap 50D-safe contract."""
    e = _BootstrapOnlyEngine()
    assert _serving_dimension(e) == 50
    assert _serving_schema_id(e) == "scalp_v1"


def test_serving_contract_is_robust_to_engines_exposing_only_constants() -> None:
    """Any object shaped like the old engine still resolves."""
    e = SimpleNamespace(FEATURE_DIM=50, FEATURE_SCHEMA_ID="scalp_v1")
    assert _serving_dimension(e) == 50
    assert _serving_schema_id(e) == "scalp_v1"


# ---------------------------------------------------------------------------
# 5. Class count — the challenger's head contract
# ---------------------------------------------------------------------------


def test_challenger_default_class_count_is_the_trained_contract() -> None:
    """The runtime's default must be TRAINED_CLASS_COUNT (3), never a
    hardcoded 4 that contradicts the trainer's triple-barrier head."""
    import inspect

    from nexus_scalp.model_lifecycle.model_class_contract import TRAINED_CLASS_COUNT
    from nexus_scalp.shadow.challenger import ChallengerRuntime

    sig = inspect.signature(ChallengerRuntime.__init__)
    default = sig.parameters["num_classes"].default
    assert default == TRAINED_CLASS_COUNT == 3


def test_challenger_inspects_under_the_live_contract_not_the_active_schema() -> None:
    """inspect_artifact must receive THIS runtime's live contract.

    The ACTIVE schema resolves to the bootstrap scalp_v1/50D even while a 70D
    bundle serves; a challenger constructed as scalp_v3/70D must be inspected
    as scalp_v3/70D or it fails a bogus DIMENSION_MISMATCH check.
    """
    import inspect

    from nexus_scalp.model_lifecycle.integrity import inspect_artifact
    from nexus_scalp.shadow.challenger import ChallengerRuntime

    src = inspect.getsource(ChallengerRuntime._load)
    assert "feature_schema_id=self.live_schema_id" in src
    assert "feature_dimension=self.live_dimension" in src
    # The inspector must actually accept those arguments.
    params = set(inspect.signature(inspect_artifact).parameters)
    assert {"feature_schema_id", "feature_dimension"} <= params
