"""ML-LABEL-001: Unit tests for Triple Barrier parameters, calibration, and diagnostics.

Verifies:
  1. TripleBarrierConfig parameter validation, defaults, and propagation.
  2. TripleBarrierLabeler accepts configuration object and propagates settings.
  3. Diagnostic telemetry (exit_reason, holding_bars, realized_r).
  4. Metric aggregation via compute_triple_barrier_metrics.
  5. Pathology detection (zero BUY or zero SELL abort conditions).
  6. 3-class label contract preservation across parameter sweeps.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.labeling.triple_barrier import (
    TripleBarrierConfig,
    TripleBarrierLabeler,
    TripleBarrierMetrics,
    compute_triple_barrier_metrics,
)


def _make_synth_frame(
    n: int = 50,
    *,
    spread: float = 0.35,
    atr: float = 1.5,
    seed: int = 42,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0, 0.0008, size=n)
    closes = 2400.0 * np.exp(np.cumsum(returns))
    opens = np.empty(n, dtype=np.float64)
    opens[0] = 2400.0
    opens[1:] = closes[:-1]
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0.0, 0.5, size=n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.0, 0.5, size=n))
    lows = np.maximum(lows, 0.01)
    a = np.full(n, atr, dtype=np.float64)
    sp = np.full(n, spread, dtype=np.float64)
    times = [datetime(2026, 9, 1, 12, 0, tzinfo=UTC).isoformat()] * n
    return pl.DataFrame(
        {
            "close": closes,
            "open": opens,
            "high": highs,
            "low": lows,
            "atr_m1": a,
            "atr": a,
            "spread": sp,
            "time": times,
        }
    )


def test_triple_barrier_config_defaults() -> None:
    """Verifies default values match canonical production contract."""
    cfg = TripleBarrierConfig()
    assert cfg.take_profit_atr_mult == 1.1
    assert cfg.stop_loss_atr_mult == 1.0
    assert cfg.max_holding_bars == 15
    assert cfg.friction_usd == 0.35
    assert cfg.embargo_bars == 3
    assert cfg.no_trade_stride_bars == 3
    assert cfg.max_allowed_mae_ratio == 0.75
    assert cfg.min_valid_atr == 0.20
    assert not cfg.include_diagnostics
    cfg.validate()


def test_triple_barrier_config_validation_failures() -> None:
    """Verifies bounds checking in TripleBarrierConfig."""
    with pytest.raises(ValueError, match="take_profit_atr_mult must be positive"):
        TripleBarrierConfig(take_profit_atr_mult=0.0).validate()

    with pytest.raises(ValueError, match="stop_loss_atr_mult must be positive"):
        TripleBarrierConfig(stop_loss_atr_mult=-0.5).validate()

    with pytest.raises(ValueError, match="max_holding_bars must be >= 1"):
        TripleBarrierConfig(max_holding_bars=0).validate()

    with pytest.raises(ValueError, match="friction_usd cannot be negative"):
        TripleBarrierConfig(friction_usd=-0.1).validate()

    with pytest.raises(ValueError, match="embargo_bars cannot be negative"):
        TripleBarrierConfig(embargo_bars=-1).validate()

    with pytest.raises(ValueError, match="no_trade_stride_bars must be >= 1"):
        TripleBarrierConfig(no_trade_stride_bars=0).validate()

    with pytest.raises(ValueError, match="max_allowed_mae_ratio must be in"):
        TripleBarrierConfig(max_allowed_mae_ratio=0.0).validate()

    with pytest.raises(ValueError, match="max_allowed_mae_ratio must be in"):
        TripleBarrierConfig(max_allowed_mae_ratio=1.5).validate()

    with pytest.raises(ValueError, match="min_valid_atr cannot be negative"):
        TripleBarrierConfig(min_valid_atr=-0.01).validate()


def test_labeler_parameter_propagation() -> None:
    """Verifies parameter propagation from TripleBarrierConfig into TripleBarrierLabeler."""
    cfg = TripleBarrierConfig(
        take_profit_atr_mult=1.5,
        stop_loss_atr_mult=1.2,
        max_holding_bars=30,
        friction_usd=0.50,
        embargo_bars=5,
        no_trade_stride_bars=4,
        max_allowed_mae_ratio=0.80,
        min_valid_atr=0.25,
        include_diagnostics=True,
    )
    labeler = TripleBarrierLabeler(config=cfg)
    assert labeler.tp_mult == 1.5
    assert labeler.sl_mult == 1.2
    assert labeler.max_holding == 30
    assert labeler.friction_usd == 0.50
    assert labeler.embargo_bars == 5
    assert labeler.no_trade_stride_bars == 4
    assert labeler.max_allowed_mae_ratio == 0.80
    assert labeler.min_valid_atr == 0.25
    assert labeler.include_diagnostics is True


def test_diagnostics_columns_present_when_requested() -> None:
    """Verifies diagnostic columns are added only when include_diagnostics=True."""
    df = _make_synth_frame(40)

    # 1. Default: diagnostics False
    labeler_standard = TripleBarrierLabeler()
    out_standard = labeler_standard.label_dataframe(df)
    assert "label" in out_standard.columns
    assert "is_eval_sample" in out_standard.columns
    assert "is_purged" in out_standard.columns
    assert "exit_reason" not in out_standard.columns
    assert "holding_bars" not in out_standard.columns
    assert "realized_r" not in out_standard.columns

    # 2. Diagnostics True via config
    cfg_diag = TripleBarrierConfig(include_diagnostics=True)
    labeler_diag = TripleBarrierLabeler(config=cfg_diag)
    out_diag = labeler_diag.label_dataframe(df)
    assert "exit_reason" in out_diag.columns
    assert "holding_bars" in out_diag.columns
    assert "realized_r" in out_diag.columns

    # 3. Diagnostics overridden dynamically in call
    out_dyn = labeler_standard.label_dataframe(df, include_diagnostics=True)
    assert "exit_reason" in out_dyn.columns


def test_diagnostics_content_correctness() -> None:
    """Verifies values populated in exit_reason, holding_bars, and realized_r."""
    df = _make_synth_frame(60, seed=123)
    labeler = TripleBarrierLabeler(include_diagnostics=True)
    out = labeler.label_dataframe(df)

    eval_rows = out.filter(pl.col("is_eval_sample"))
    assert len(eval_rows) > 0

    reasons = eval_rows["exit_reason"].to_list()
    valid_reasons = {
        "BUY_TP_HIT",
        "SELL_TP_HIT",
        "SL_HIT",
        "DUAL_HIT_NEUTRALIZED",
        "TIME_EXPIRY_BUY",
        "TIME_EXPIRY_SELL",
        "TIME_EXPIRY_NO_TRADE",
    }
    for r in reasons:
        assert r in valid_reasons

    holding = eval_rows["holding_bars"].to_list()
    for h in holding:
        assert 1 <= h <= labeler.max_holding

    r_vals = eval_rows["realized_r"].to_list()
    for rv in r_vals:
        assert isinstance(rv, float)


def test_compute_metrics_calculation() -> None:
    """Verifies metrics computation from labeled DataFrame."""
    df = _make_synth_frame(80, seed=99)
    labeler = TripleBarrierLabeler(include_diagnostics=True)
    out = labeler.label_dataframe(df)

    metrics: TripleBarrierMetrics = labeler.compute_metrics(out)
    assert metrics.total_bars == 80
    assert metrics.evaluated_samples > 0
    assert (
        metrics.buy_count + metrics.sell_count + metrics.no_trade_count == metrics.evaluated_samples
    )
    assert 0.0 <= metrics.win_rate <= 1.0
    assert 0.0 <= metrics.balance_ratio <= 1.0
    assert (
        metrics.tp_hit_count
        + metrics.sl_hit_count
        + metrics.time_expiry_count
        + metrics.dual_hit_count
        == metrics.evaluated_samples
    )
    assert isinstance(metrics.r_expectancy, float)

    m_dict = metrics.to_dict()
    assert "r_expectancy" in m_dict
    assert "profit_factor" in m_dict


def test_pathology_detection_on_empty_and_zero_labels() -> None:
    """Verifies pathology abort conditions (e.g. 0 BUY or 0 SELL)."""
    # 1. Empty DataFrame
    empty_df = pl.DataFrame()
    m_empty = compute_triple_barrier_metrics(empty_df)
    assert m_empty.pathology_detected is True
    assert "Empty DataFrame" in m_empty.pathology_reason

    # 2. DataFrame where no samples are evaluated
    df = _make_synth_frame(20, atr=0.0)  # atr=0 -> all skipped
    labeler = TripleBarrierLabeler()
    out = labeler.label_dataframe(df)
    m_zero = compute_triple_barrier_metrics(out)
    assert m_zero.pathology_detected is True


def test_parameter_sweep_integrity() -> None:
    """Verifies varying TP, SL, and holding parameters preserves 3-class contract."""
    df = _make_synth_frame(50, seed=7)
    test_params = [
        (1.0, 0.8, 10),
        (1.2, 1.0, 15),
        (1.5, 1.2, 20),
        (2.0, 1.0, 30),
    ]

    valid_labels = {
        ActionType.NO_TRADE.value,
        ActionType.BUY_MARKET.value,
        ActionType.SELL_MARKET.value,
    }

    for tp, sl, h in test_params:
        lbl = TripleBarrierLabeler(
            take_profit_atr_mult=tp,
            stop_loss_atr_mult=sl,
            max_holding_bars=h,
            include_diagnostics=True,
        )
        out = lbl.label_dataframe(df)
        labels = set(out["label"].unique().to_list())
        assert labels.issubset(valid_labels)
        metrics = lbl.compute_metrics(out)
        assert metrics.total_bars == 50
