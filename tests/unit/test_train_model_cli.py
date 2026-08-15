"""
Unit Tests for Legacy CLI Training Script (50D Contract Alignment)
===================================================================
Verifies that `src/cli/train_model.py` generates and selects the full 50-dimensional
feature matrix matching `WalkForwardTrainer.NUM_FEATURES` and `ScalpNet`, without
any 18D truncation or dimension mismatch.
"""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from cli.train_model import reconstruct_features_and_bars
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


def create_synthetic_ticks(num_bars: int = 60, ticks_per_bar: int = 10) -> pl.DataFrame:
    """Generates synthetic tick data spanning completed M1 bars."""
    start_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=UTC)
    rows = []
    price = 2600.0

    for bar_idx in range(num_bars):
        bar_start = start_time + timedelta(minutes=bar_idx)
        for t_idx in range(ticks_per_bar):
            timestamp = bar_start + timedelta(seconds=t_idx * 5)
            price += (t_idx % 3 - 1) * 0.10
            rows.append(
                {
                    "symbol": "XAUUSD",
                    "timestamp": timestamp,
                    "bid": price,
                    "ask": price + 0.30,
                    "last": price + 0.15,
                    "volume": 5.0,
                    "flags": 0,
                }
            )

    return pl.DataFrame(rows)


def test_reconstruct_features_and_bars_50d_contract() -> None:
    """
    Verifies that `reconstruct_features_and_bars` generates feature records containing
    all 50 feature columns (`feat_0` .. `feat_49`) and OHLC/ATR columns.
    """
    df_ticks = create_synthetic_ticks(num_bars=60, ticks_per_bar=10)
    df_features = reconstruct_features_and_bars(df_ticks=df_ticks, symbol="XAUUSD")

    assert len(df_features) > 0, "Feature engineering should produce feature snapshots"

    feature_cols = [col for col in df_features.columns if col.startswith("feat_")]
    assert len(feature_cols) == 50, f"Expected exactly 50 feature columns, got {len(feature_cols)}"

    # Check that all feat_0 through feat_49 are present
    expected_cols = [f"feat_{idx}" for idx in range(50)]
    for col in expected_cols:
        assert col in df_features.columns, f"Missing expected feature column: {col}"

    # Specifically verify features beyond the old 18D boundary (feat_18 .. feat_49) exist
    for idx in range(18, 50):
        assert f"feat_{idx}" in df_features.columns, f"Feature feat_{idx} missing (truncated)"

    # Check OHLC and metadata columns
    assert "close" in df_features.columns
    assert "high" in df_features.columns
    assert "low" in df_features.columns
    assert "open" in df_features.columns
    assert "spread" in df_features.columns
    assert "atr_m1" in df_features.columns


def test_cli_feature_cols_compatibility_with_walk_forward_trainer() -> None:
    """
    Verifies that the 50D feature columns constructed by `src/cli/train_model.py`
    pass `WalkForwardTrainer._validate_training_frame` validation without contract error.
    """
    df_ticks = create_synthetic_ticks(num_bars=60, ticks_per_bar=10)
    df_features = reconstruct_features_and_bars(df_ticks=df_ticks, symbol="XAUUSD")

    # Add dummy label column for WalkForwardTrainer validation
    df_labeled = df_features.with_columns(pl.lit("NO_TRADE").alias("label"))

    feature_cols_50d = [f"feat_{idx}" for idx in range(WalkForwardTrainer.NUM_FEATURES)]

    trainer = WalkForwardTrainer(num_folds=2, artifact_save_path="artifacts/test_model.pt")

    # Validation should succeed cleanly with 50D feature columns
    trainer._validate_training_frame(df_labeled, feature_cols_50d)

    # Verify that an 18D column list explicitly raises ValueError contract violation.
    # The message is schema-driven now ("schema=scalp_v1 expected 50 feature columns,
    # got 18"), so the assertion checks the BEHAVIOUR (raise + reports both widths)
    # rather than the old hard-coded "50D" prefix.
    feature_cols_18d = [f"feat_{idx}" for idx in range(18)]
    with pytest.raises(ValueError, match=r"[Ff]eature contract violation") as exc_info:
        trainer._validate_training_frame(df_labeled, feature_cols_18d)

    message = str(exc_info.value)
    assert str(WalkForwardTrainer.NUM_FEATURES) in message
    assert "18" in message
