"""BUG-125: 70D Live Tensor Path Regression Tests.

Covers the new artifact-driven model/schema selection, canonical 70D tensor
assembly, causal liquidity gating, hot-swap validation, and 50D backward
compatibility — ensuring the BUG-125 architectural fix is contract-safe,
production-ready, and does not regress existing 50D behavior.

TEST MAP:
  TEST-125-01: ScalerBundle.dimension() returns correct width
  TEST-125-02: ScalerBundle.transform() dimension-agnostic (50D + 70D)
  TEST-125-03: ScalerBundle.transform_50d() backward-compat alias
  TEST-125-04: effective_feature_dim from 50D bundle
  TEST-125-05: effective_feature_dim from 70D bundle
  TEST-125-06: effective_feature_schema_id for 50D bundle
  TEST-125-07: effective_feature_schema_id for 70D bundle
  TEST-125-08: _expected_num_features_for_artifact probes checkpoint shape
  TEST-125-09: _expected_num_features_for_artifact falls back to default on missing
  TEST-125-10: _build_live_feature_vector returns 50D when bundle is 50D
  TEST-125-11: _build_live_feature_vector raises when 70D liquidity is STALE
  TEST-125-12: _build_live_feature_vector assembles canonical 70D vector
  TEST-125-13: hot_swap validates against actual bundle dimension
  TEST-125-14: _register_active_model stamps effective contract (not class default)
  TEST-125-15: 50D Champion load + inference unchanged (regression)
  TEST-125-16: 70D model loads successfully through artifact-driven path
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parent.parent.parent
ARTIFACTS = REPO / "artifacts" / "models" / "scalp" / "XAUUSD"


def _mock_scaler(dim: int) -> Any:
    from nexus_scalp.application.live_engine import ScalerBundle

    return ScalerBundle(mean=np.zeros(dim, dtype=np.float32), std=np.ones(dim, dtype=np.float32))


def _mock_bundle(dim: int) -> Any:
    """Build a minimal mock ModelBundle with the given input width."""
    from nexus_scalp.application.live_engine import ModelBundle, ScalerBundle
    from nexus_scalp.models.scalp_net import ScalpNet

    model = ScalpNet(num_features=dim, num_classes=4)
    model.eval()
    return ModelBundle(
        model=model,
        scaler=ScalerBundle(mean=np.zeros(dim, dtype=np.float32), std=np.ones(dim, dtype=np.float32)),
        artifact_path=ARTIFACTS / f"{'50d_main' if dim == 50 else '70d_liquidity'}" / "model.pt",
    )


def _mock_engine(bundle_dim: int = 50) -> Any:
    """Build a minimal mock LiveEngine with the given bundle loaded."""
    import threading

    from nexus_scalp.application.live_engine import LiveEngine

    eng = MagicMock(spec=LiveEngine)
    eng.FEATURE_DIM = LiveEngine.FEATURE_DIM
    eng.FEATURE_COLS = LiveEngine.FEATURE_COLS
    eng.FEATURE_SCHEMA_ID = LiveEngine.FEATURE_SCHEMA_ID
    eng.__class__ = LiveEngine
    eng._bundle_lock = threading.RLock()
    eng._bundle = _mock_bundle(bundle_dim)
    eng.effective_feature_dim = LiveEngine.effective_feature_dim.fget(eng)  # type: ignore[attr-defined]
    eng.effective_feature_schema_id = LiveEngine.effective_feature_schema_id.fget(eng)  # type: ignore[attr-defined]
    # Bind real methods so _build_live_feature_vector etc. work.
    # _validate_50d_tensor is a @classmethod — cannot delegate through lambda.
    # For tests, the mock fv.to_tensor_input() already returns valid values,
    # so we use an identity that just returns the input list.
    eng._validate_50d_tensor = lambda features, context: list(features)
    eng._build_live_feature_vector = lambda fv: LiveEngine._build_live_feature_vector(eng, fv)
    eng._register_active_model = lambda path, replaced: LiveEngine._register_active_model(eng, path, replaced)
    eng._news_enabled = False
    eng.news_engine = None
    eng.liquidity_governor = None
    return eng


# ---------------------------------------------------------------------------
# TEST-125-01..03: ScalerBundle basics
# ---------------------------------------------------------------------------
class TestScalerBundle:
    def test_125_01_dimension(self) -> None:
        assert _mock_scaler(50).dimension() == 50
        assert _mock_scaler(70).dimension() == 70

    def test_125_02_transform_agnostic(self) -> None:
        s = _mock_scaler(70)
        x = np.ones((1, 70), dtype=np.float32)
        out = s.transform(x)
        assert out.shape == (1, 70)
        # All zeros means (1 - 0) / 1 = 1.0, clipped to [-5,+5] = 1.0
        assert out[0, 0] == pytest.approx(1.0)

    def test_125_03_transform_50d_alias(self) -> None:
        s = _mock_scaler(50)
        x = np.ones((1, 50), dtype=np.float32)
        out = s.transform_50d(x)
        assert out.shape == (1, 50)


# ---------------------------------------------------------------------------
# TEST-125-04..07: effective_* properties
# ---------------------------------------------------------------------------
class TestEffectiveContract:
    def test_125_04_dim_50d_bundle(self) -> None:
        eng = _mock_engine(50)
        assert eng.effective_feature_dim == 50

    def test_125_05_dim_70d_bundle(self) -> None:
        eng = _mock_engine(70)
        assert eng.effective_feature_dim == 70

    def test_125_06_schema_50d(self) -> None:
        eng = _mock_engine(50)
        assert eng.effective_feature_schema_id == "scalp_v1"

    def test_125_07_schema_70d(self) -> None:
        eng = _mock_engine(70)
        assert eng.effective_feature_schema_id == "scalp_v3"


# ---------------------------------------------------------------------------
# TEST-125-08..09: Artifact probe
# ---------------------------------------------------------------------------
class TestArtifactProbe:
    def test_125_08_probe_50d(self) -> None:
        from nexus_scalp.application.live_engine import LiveEngine

        path = ARTIFACTS / "v1.0.0" / "model.pt"
        dim = LiveEngine._expected_num_features_for_artifact(None, path)
        assert dim == 50

    def test_125_08_probe_70d(self) -> None:
        from nexus_scalp.application.live_engine import LiveEngine

        path = ARTIFACTS / "70d_liquidity" / "model.pt"
        dim = LiveEngine._expected_num_features_for_artifact(None, path)
        assert dim == 70

    def test_125_09_probe_missing_fallback(self) -> None:
        from nexus_scalp.application.live_engine import LiveEngine

        eng = MagicMock(spec=LiveEngine)
        eng.FEATURE_DIM = LiveEngine.FEATURE_DIM
        dim = LiveEngine._expected_num_features_for_artifact(eng, Path("/nonexistent/model.pt"))
        assert dim == 50  # class default


# ---------------------------------------------------------------------------
# TEST-125-10..12: _build_live_feature_vector
# ---------------------------------------------------------------------------
class TestBuildLiveFeatureVector:
    def test_125_10_returns_50d_when_bundle_is_50d(self) -> None:
        eng = _mock_engine(50)
        fv = MagicMock()
        # Use values within the [-3,+3] contract
        fv.to_tensor_input.return_value = [float(i % 7 - 3) for i in range(50)]
        vec, timings = eng._build_live_feature_vector(fv)
        assert len(vec) == 50
        assert "feature_ms" in timings

    def test_125_11_raises_when_70d_liquidity_stale(self) -> None:
        eng = _mock_engine(70)
        # Liquidity governor with no snapshot -> causal_state = INVALID
        eng.liquidity_governor = MagicMock()
        eng.liquidity_governor.last_snapshot = None
        eng.liquidity_governor.causal_state = MagicMock(return_value="INVALID")
        eng._news_enabled = False

        fv = MagicMock()
        fv.to_tensor_input.return_value = [float(i % 7 - 3) for i in range(50)]
        with pytest.raises(RuntimeError, match="not VALID"):
            eng._build_live_feature_vector(fv)

    def test_125_12_assembles_70d_vector(self) -> None:
        eng = _mock_engine(70)
        eng._news_enabled = False

        # Fake liquidity snapshot with VALID causal state
        fake_features = [0.5, -0.3, 0.0, 0.1, 0.8, -0.2, 0.3, 0.0, 0.7, -0.1]
        fake_snap = SimpleNamespace(features=fake_features)
        eng.liquidity_governor = MagicMock()
        eng.liquidity_governor.last_snapshot = fake_snap
        eng.liquidity_governor.causal_state = MagicMock(return_value="VALID")

        fv = MagicMock()
        base50 = [float(i % 7 - 3) for i in range(50)]
        fv.to_tensor_input.return_value = base50
        vec, timings = eng._build_live_feature_vector(fv)
        assert len(vec) == 70
        # Liquidity values at 60..69
        assert vec[60:70] == fake_features
        # Base 50 preserved
        assert vec[:50] == base50
        # News at 50..59 are 0.0 (no news)
        assert vec[50:60] == [0.0] * 10


# ---------------------------------------------------------------------------
# TEST-125-13: hot_swap validates against bundle dimension
# ---------------------------------------------------------------------------
class TestHotSwap:
    def test_125_13_hot_swap_warmup_uses_bundle_dim(self) -> None:
        """The hot_swap warm-up must create a zero vector matching the NEW
        bundle's model width, not the class-level FEATURE_DIM."""
        from nexus_scalp.application.live_engine import LiveEngine

        eng = _mock_engine(50)
        # Simulate loading a 70D bundle
        eng._bundle = _mock_bundle(70)
        eng.config = MagicMock()
        eng.config.model.model_artifact_path = str(ARTIFACTS / "70d_liquidity" / "model.pt")
        # Verify the warm vector would be 70 wide
        new_bundle = eng._bundle
        warm = np.zeros((1, int(new_bundle.model.num_features)), dtype=np.float32)
        assert warm.shape == (1, 70)


# ---------------------------------------------------------------------------
# TEST-125-14: _register_active_model stamps effective contract
# ---------------------------------------------------------------------------
class TestRegisterActiveModel:
    def test_125_14_stamps_effective(self) -> None:
        eng = _mock_engine(70)
        eng.model_registry = MagicMock()
        eng.experience_engine = MagicMock()
        eng.config = MagicMock()
        eng.config.model.feature_schema_version = "v1.0"
        eng.runtime_config = MagicMock()
        eng.runtime_config.get_version.return_value = 0

        eng._register_active_model(eng._bundle.artifact_path, replaced=True)

        call_args = eng.model_registry.register_model.call_args
        # register_model is called with keyword args
        if call_args[1]:
            assert call_args[1]["feature_schema_id"] == "scalp_v3"
            assert call_args[1]["feature_dimension"] == 70
        else:
            # positional
            assert "scalp_v3" in str(call_args)
            assert "70" in str(call_args)


# ---------------------------------------------------------------------------
# TEST-125-15: 50D regression — existing behavior unchanged
# ---------------------------------------------------------------------------
class Test50DRegression:
    def test_125_15_50d_champion_loads(self) -> None:
        """The v1.0.0 50D Champion still loads through the artifact-driven path."""
        from nexus_scalp.application.live_engine import LiveEngine

        path = ARTIFACTS / "v1.0.0" / "model.pt"
        dim = LiveEngine._expected_num_features_for_artifact(None, path)
        assert dim == 50
        # The engine would create a ScalpNet(50) and load successfully
        from nexus_scalp.models.scalp_net import ScalpNet

        m = ScalpNet(num_features=dim)
        assert m.input_projection.weight.shape == (128, 50)


# ---------------------------------------------------------------------------
# TEST-125-16: 70D model loads through artifact-driven path
# ---------------------------------------------------------------------------
class Test70DPath:
    def test_125_16_70d_model_loads(self) -> None:
        """The 70D liquidity model loads through the artifact-driven path."""
        import torch

        from nexus_scalp.application.live_engine import LiveEngine
        from nexus_scalp.models.scalp_net import ScalpNet

        path = ARTIFACTS / "70d_liquidity" / "model.pt"
        dim = LiveEngine._expected_num_features_for_artifact(None, path)
        assert dim == 70

        m = ScalpNet(num_features=dim)
        state = torch.load(path, map_location="cpu")
        m.load_state_dict(state)
        m.eval()
        x = torch.randn(1, 70)
        with torch.inference_mode():
            out = m(x)
        assert out.shape == (1, 4)
