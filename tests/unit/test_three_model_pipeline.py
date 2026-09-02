"""Tests for the 3-model training pipeline (three_model.py)."""

import polars as pl
import pytest

from nexus_scalp.model_generation.three_model import (
    DEFAULT_MIN_ROWS,
    SMOKE_MIN_ROWS,
    build_feature_frame,
    train_variant,
    variant_artifact_path,
    variant_feature_columns,
    variant_schema_id,
)


def _bars(n: int = 200, seed: int = 7) -> pl.DataFrame:
    import numpy as np

    np.random.seed(seed)
    ts = np.arange(n, dtype="int64").astype("datetime64[us]")
    return pl.DataFrame(
        {
            "time": np.arange(n, dtype="int64"),
            "time_utc": ts,
            "open": 3300 + np.cumsum(np.random.randn(n) * 0.1),
            "high": 3300 + np.cumsum(np.random.randn(n) * 0.1) + 0.2,
            "low": 3300 + np.cumsum(np.random.randn(n) * 0.1) - 0.2,
            "close": 3300 + np.cumsum(np.random.randn(n) * 0.1),
            "tick_volume": np.random.randint(50, 200, n),
            "spread": np.full(n, 0.20),
            "real_volume": np.zeros(n, dtype="int64"),
        }
    )


def test_variant_artifact_path_and_columns() -> None:
    assert variant_artifact_path("50d_main").name == "model.pt"
    assert variant_artifact_path("70d_news").parent.name == "70d_news"
    assert len(variant_feature_columns("50d_main")) == 50
    assert len(variant_feature_columns("70d_liquidity")) == 70
    assert variant_feature_columns("70d_news")[60] == "feat_60"
    assert variant_schema_id("50d_main") == "scalp_v1"
    assert variant_schema_id("70d_liquidity") == "scalp_v3"


def test_unknown_variant_rejected() -> None:
    with pytest.raises(ValueError):
        variant_artifact_path("bogus")


def test_build_feature_frame_50d_has_atr_and_feats() -> None:
    frame = build_feature_frame("50d_main", _bars(), None)
    assert "atr_m1" in frame.columns
    assert "feat_0" in frame.columns and "feat_49" in frame.columns
    assert "feat_50" not in frame.columns
    assert frame.height > 100


def test_build_feature_frame_70d_has_60_69() -> None:
    frame = build_feature_frame("70d_liquidity", _bars(), None)
    assert "feat_60" in frame.columns and "feat_69" in frame.columns
    assert frame.height > 100


def test_smoke_min_rows_guard() -> None:
    small = _bars(n=100)
    with pytest.raises(ValueError):
        train_variant("50d_main", small, smoke=True)
    assert SMOKE_MIN_ROWS >= 3_000
    assert DEFAULT_MIN_ROWS >= 1_000
