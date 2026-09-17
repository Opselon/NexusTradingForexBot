"""
ML-FEAT-001 — 50D Feature Normalization End-to-End Parity Verification
======================================================================
Verifies and mathematically proves numerical parity for 50D feature normalization
between offline training (WalkForwardTrainer) and live serving (ScalerBundle /
InferenceService).

Test Scenarios:
  Scenario #1: End-to-End 50D Normalization Parity across 10,000 synthetic rows
               verifying max_abs_error <= 1e-7 (atol=1e-7, rtol=1e-7).
  Scenario #2: Zero-variance feature clamping behavior (constant columns, std floor).
  Scenario #3: Extreme finite values / flash-crash input numerical stability ([-5, +5] clipping).
  Scenario #4: Fail-loud validation for dimension mismatch and non-finite feature handling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from nexus_scalp.application.live.inference import InferenceService
from nexus_scalp.application.live_engine import LiveEngine, ScalerBundle
from nexus_scalp.features.schema import active_dimension, active_schema
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


class _MockEngineSurface:
    """Minimal LiveEngine surface mock providing the required class attributes and methods."""

    FEATURE_DIM: int = 50
    FEATURE_SCHEMA_ID: str = "scalp_v1"
    effective_feature_dim: int = 50
    effective_feature_schema_id: str = "scalp_v1"

    @classmethod
    def _validate_50d_tensor(cls, features: list[float], context: str) -> list[float]:
        return LiveEngine._validate_50d_tensor.__func__(cls, features, context)


@pytest.fixture
def deterministic_50d_matrix() -> np.ndarray:
    """
    Generates a deterministic (10,000, 50) float32 synthetic matrix (seed=42).

    Contains a realistic mixture of:
    - Normal / Gaussian distributed signals (cols 0..19)
    - Uniform distributed indicators / bounded oscillators (cols 20..34)
    - Exponential / price-jump / heavy-tailed features (cols 35..49)
    """
    rng = np.random.default_rng(seed=42)
    x = np.empty((10000, 50), dtype=np.float32)

    # Columns 0..19: Gaussian distributed signals (e.g. z-scores, standardized returns)
    x[:, 0:20] = rng.normal(loc=0.0, scale=1.5, size=(10000, 20)).astype(np.float32)

    # Columns 20..34: Uniform distributed features (e.g. bounded oscillators, RSI/Stochastics)
    x[:, 20:35] = rng.uniform(low=-10.0, high=10.0, size=(10000, 15)).astype(np.float32)

    # Columns 35..49: Exponential / jump / volatility-like features
    x[:, 35:50] = rng.exponential(scale=2.5, size=(10000, 15)).astype(np.float32)

    return x


# =============================================================================
# SCENARIO #1 — END-TO-END 50D PARITY
# =============================================================================


def test_scenario1_end_to_end_50d_normalization_parity(
    deterministic_50d_matrix: np.ndarray, tmp_path: Path
) -> None:
    """
    Scenario #1: Mathematical parity between offline trainer and live serving scaler.

    Validates:
      1. Scaler fitted via WalkForwardTrainer._fit_scaler on 10,000x50 float32 matrix.
      2. Scaler serialized to .scaler.npz and loaded into ScalerBundle.
      3. Matrix transformed via offline trainer._transform_features.
      4. Identical matrix transformed via live ScalerBundle.transform.
      5. Bitwise parity: np.allclose at atol=1e-7, rtol=1e-7, and max_abs_error <= 1e-7.
      6. Row-by-row streaming parity matching live inference ingestion cadence.
    """
    x = deterministic_50d_matrix
    assert x.shape == (10000, 50)
    assert x.dtype == np.float32

    trainer = WalkForwardTrainer()
    assert trainer.num_features == 50
    assert active_dimension() == 50

    # 1. Fit offline scaler
    scaler_offline = trainer._fit_scaler(x)
    assert scaler_offline.mean is not None
    assert scaler_offline.std is not None

    # 2. Persist scaler artifact atomically
    scaler_path = tmp_path / "test_50d.scaler.npz"
    mean_1d = np.asarray(scaler_offline.mean, dtype=np.float32).reshape(-1)
    std_1d = np.asarray(scaler_offline.std, dtype=np.float32).reshape(-1)
    assert mean_1d.shape == (50,)
    assert std_1d.shape == (50,)

    np.savez(scaler_path, mean=mean_1d, std=std_1d)
    assert scaler_path.exists()

    # 3. Load scaler into live ScalerBundle
    loaded_data = np.load(scaler_path)
    loaded_mean = np.asarray(loaded_data["mean"], dtype=np.float32).reshape(-1)
    loaded_std = np.asarray(loaded_data["std"], dtype=np.float32).reshape(-1)
    live_scaler = ScalerBundle(mean=loaded_mean, std=loaded_std)
    assert live_scaler.is_ready() is True
    assert live_scaler.dimension() == 50

    # 4. Transform offline
    batch_transformed = trainer._transform_features(x, scaler_offline)
    assert batch_transformed.shape == (10000, 50)
    assert batch_transformed.dtype == np.float32

    # 5. Transform live (full matrix)
    live_transformed = live_scaler.transform(x)
    assert live_transformed.shape == (10000, 50)

    # 6. Verify numerical parity invariant
    max_abs_error = float(np.max(np.abs(batch_transformed - live_transformed)))
    assert max_abs_error <= 1e-7, (
        f"Normalization parity violation: max_abs_error={max_abs_error:.9e} > 1e-7"
    )
    assert np.allclose(batch_transformed, live_transformed, atol=1e-7, rtol=1e-7), (
        f"np.allclose failed with max delta={max_abs_error:.9e}"
    )

    # 7. Row-by-row live streaming transform parity (simulate per-tick serving)
    for row_idx in (0, 1, 42, 100, 999, 5000, 9999):
        single_row = x[row_idx : row_idx + 1]
        single_live = live_scaler.transform(single_row)
        row_diff = float(np.max(np.abs(batch_transformed[row_idx : row_idx + 1] - single_live)))
        assert row_diff <= 1e-7, (
            f"Row-level streaming parity mismatch at row {row_idx}: delta={row_diff:.9e}"
        )


# =============================================================================
# SCENARIO #2 — ZERO-VARIANCE FEATURES & STD CLAMPING
# =============================================================================


def test_scenario2_zero_variance_std_clamping() -> None:
    """
    Scenario #2: Zero-variance feature clamping and numerical safety.

    Validates:
      1. Constant columns (e.g. index 5 = 2.5, index 20 = 0.0) have sample std = 0.0.
      2. Production WalkForwardTrainer._fit_scaler clamps std to maximum(std, 1e-3).
      3. Clamping threshold satisfies the invariant std >= 1e-8.
      4. Constant columns normalize to 0.0 without division-by-zero, NaN, or Inf.
      5. Full numerical parity holds between offline trainer and live ScalerBundle.
    """
    rng = np.random.default_rng(seed=42)
    x = rng.normal(loc=1.0, scale=2.0, size=(1000, 50)).astype(np.float32)

    # Mandated constant columns
    x[:, 5] = 2.5
    x[:, 20] = 0.0

    trainer = WalkForwardTrainer()
    scaler = trainer._fit_scaler(x)

    # Verify production clamping to 1e-3 (which is strictly >= 1e-8)
    assert float(scaler.std[0, 5]) == pytest.approx(1e-3, rel=1e-6)
    assert float(scaler.std[0, 20]) == pytest.approx(1e-3, rel=1e-6)
    assert float(scaler.std[0, 5]) >= 1e-8
    assert float(scaler.std[0, 20]) >= 1e-8

    # Transform offline & live
    batch_transformed = trainer._transform_features(x, scaler)
    live_scaler = ScalerBundle(
        mean=scaler.mean.reshape(-1),
        std=scaler.std.reshape(-1),  # type: ignore[union-attr]
    )
    live_transformed = live_scaler.transform(x)

    # Constant columns must normalize cleanly to 0.0
    assert np.all(batch_transformed[:, 5] == 0.0), "Constant column 5 must normalize to 0.0"
    assert np.all(batch_transformed[:, 20] == 0.0), "Constant column 20 must normalize to 0.0"
    assert np.all(live_transformed[:, 5] == 0.0), "Live constant column 5 must normalize to 0.0"
    assert np.all(live_transformed[:, 20] == 0.0), "Live constant column 20 must normalize to 0.0"

    # Invariant checks: finite, no NaN, no Inf
    assert np.all(np.isfinite(batch_transformed)), "Offline transform produced non-finite values"
    assert np.all(np.isfinite(live_transformed)), "Live transform produced non-finite values"
    assert not np.isnan(batch_transformed).any(), "Offline transform produced NaNs"
    assert not np.isnan(live_transformed).any(), "Live transform produced NaNs"
    assert not np.isinf(batch_transformed).any(), "Offline transform produced Infs"
    assert not np.isinf(live_transformed).any(), "Live transform produced Infs"

    # Parity check
    max_error = float(np.max(np.abs(batch_transformed - live_transformed)))
    assert max_error <= 1e-7, f"Zero-variance parity error: {max_error:.9e}"


# =============================================================================
# SCENARIO #3 — EXTREME VALUES / FLASH-CRASH INPUTS
# =============================================================================


def test_scenario3_extreme_values_stability() -> None:
    """
    Scenario #3: Flash-crash and extreme finite input stability.

    Validates:
      1. Normal scaler fitted on realistic standard data.
      2. Evaluated on extreme shock values (+1e6, -1e6, and mixtures).
      3. Both offline trainer and live ScalerBundle cleanly clip to [-5.0, 5.0].
      4. Outputs remain strictly finite with no overflow, NaN, or Inf.
      5. Bitwise parity is preserved even under extreme regime shifts.
    """
    rng = np.random.default_rng(seed=42)
    x_base = rng.normal(loc=0.0, scale=1.0, size=(1000, 50)).astype(np.float32)

    trainer = WalkForwardTrainer()
    scaler = trainer._fit_scaler(x_base)
    live_scaler = ScalerBundle(
        mean=scaler.mean.reshape(-1),
        std=scaler.std.reshape(-1),  # type: ignore[union-attr]
    )

    # Construct extreme inputs
    x_extreme = x_base.copy()
    x_extreme[0, 0] = 1e6  # Positive extreme single cell
    x_extreme[1, 1] = -1e6  # Negative extreme single cell
    x_extreme[2, :] = 1e6  # Entire row positive shock
    x_extreme[3, :] = -1e6  # Entire row negative shock
    x_extreme[4, 0:25] = 1e6  # Half-row positive shock
    x_extreme[4, 25:50] = -1e6  # Half-row negative shock

    batch_transformed = trainer._transform_features(x_extreme, scaler)
    live_transformed = live_scaler.transform(x_extreme)

    # Invariants: finite, no overflow
    assert np.all(np.isfinite(batch_transformed)), "Offline transform overflowed on extreme inputs"
    assert np.all(np.isfinite(live_transformed)), "Live transform overflowed on extreme inputs"
    assert not np.isnan(batch_transformed).any(), "NaN found in offline extreme transform"
    assert not np.isnan(live_transformed).any(), "NaN found in live extreme transform"
    assert not np.isinf(batch_transformed).any(), "Inf found in offline extreme transform"
    assert not np.isinf(live_transformed).any(), "Inf found in live extreme transform"

    # Bounds: strictly clipped to [-5.0, 5.0]
    assert float(np.min(batch_transformed)) >= -5.0
    assert float(np.max(batch_transformed)) <= 5.0
    assert float(np.min(live_transformed)) >= -5.0
    assert float(np.max(live_transformed)) <= 5.0

    # Bitwise parity on extreme inputs
    max_error = float(np.max(np.abs(batch_transformed - live_transformed)))
    assert max_error <= 1e-7, f"Extreme values parity error: {max_error:.9e}"


# =============================================================================
# SCENARIO #4 — FAIL-LOUD VALIDATION & CONTRACT DEFENSE
# =============================================================================


def test_scenario4_dimension_mismatch_49d_rejected() -> None:
    """
    Scenario #4.1: Vector of length 49 must be rejected fail-loud.

    Validates:
      - FeatureSchema.validate_vector (SSOT) raises ValueError.
      - InferenceService.validate_feature_vector raises RuntimeError/ValueError.
    """
    invalid_49 = [0.0] * 49

    # Schema layer (SSOT)
    schema = active_schema()
    with pytest.raises(ValueError, match="expected 50 features, got 49"):
        schema.validate_vector(invalid_49, context="test_49d")

    # Live inference layer
    engine: Any = _MockEngineSurface()
    with pytest.raises((ValueError, RuntimeError), match="expected 50, got 49"):
        InferenceService.validate_feature_vector(engine, invalid_49, context="test_49d")


def test_scenario4_dimension_mismatch_51d_rejected() -> None:
    """
    Scenario #4.2: Vector of length 51 must be rejected fail-loud.

    Validates:
      - FeatureSchema.validate_vector (SSOT) raises ValueError.
      - InferenceService.validate_feature_vector raises RuntimeError/ValueError.
    """
    invalid_51 = [0.0] * 51

    # Schema layer (SSOT)
    schema = active_schema()
    with pytest.raises(ValueError, match="expected 50 features, got 51"):
        schema.validate_vector(invalid_51, context="test_51d")

    # Live inference layer
    engine: Any = _MockEngineSurface()
    with pytest.raises((ValueError, RuntimeError), match="expected 50, got 51"):
        InferenceService.validate_feature_vector(engine, invalid_51, context="test_51d")


def test_scenario4_nonfinite_inputs_handled() -> None:
    """
    Scenario #4.3 - #4.6: Handling of NaN, +Inf, -Inf, and None values across layers.

    Validates:
      - Offline training path (WalkForwardTrainer._assert_features_finite) strictly
        refuses non-finite feature cells with ValueError (ecosystem-clean contract).
      - Live serving path (InferenceService._validate_50d_tensor) sanitizes non-finite
        cells to 0.0 with warning and clip[-3, 3] (fail-open availability contract
        documented in GAP L11-5).
      - ScalerBundle.is_ready() rejects corrupt/degenerate scaler parameters (std <= 0
        or non-finite), passing features through unchanged to avoid NaN propagation.
    """
    # 1. Offline Trainer strictly rejects NaN, +Inf, -Inf
    for label, val in [("NaN", np.nan), ("+Inf", np.inf), ("-Inf", -np.inf)]:
        x_corrupt = np.zeros((10, 50), dtype=np.float32)
        x_corrupt[0, 5] = val
        with pytest.raises(ValueError, match="Non-finite feature cell in training frame"):
            WalkForwardTrainer._assert_features_finite(x_corrupt, context=f"test_{label}")

    # 2. Live InferenceService sanitizes non-finite features to 0.0 (GAP L11-5)
    engine: Any = _MockEngineSurface()
    for label, val in [("NaN", np.nan), ("+Inf", np.inf), ("-Inf", -np.inf), ("None", None)]:
        test_vec = [1.0] * 50
        test_vec[12] = val  # type: ignore[arg-type]
        sanitized = InferenceService.validate_feature_vector(
            engine, test_vec, context=f"test_live_{label}"
        )
        assert sanitized[12] == 0.0, f"Live sanitization failed for {label}: got {sanitized[12]}"
        assert len(sanitized) == 50

    # 3. ScalerBundle defense: degenerate std is marked not-ready
    degenerate_scaler = ScalerBundle(
        mean=np.zeros(50, dtype=np.float32), std=np.zeros(50, dtype=np.float32)
    )
    assert degenerate_scaler.is_ready() is False
    # When not ready, transform returns features unchanged rather than dividing by zero
    raw_sample = np.ones((1, 50), dtype=np.float32) * 2.0
    passthrough = degenerate_scaler.transform(raw_sample)
    assert np.array_equal(passthrough, raw_sample)
