"""Unit tests for ML-LABEL-002: Sample Uniqueness Weighting & Anti-Leakage.

Covers:
  - Vectorized Concurrency Counting (c_t)
  - Mathematical Uniqueness Bounds (u_i in (0, 1])
  - Non-Overlapping Zero-Overlap Exactness (u_i = 1.0)
  - Identical Overlap Symmetry (u_i = 1/K)
  - Hand-Calculated Fractional Uniqueness Verification
  - Polars DataFrame Integration & Evaluated-Sample Masking
  - Weight Normalization (Sum to N)
  - Return-Attributed & Time-Decay Weighting (AFML Ch. 4)
  - Comprehensive Uniqueness Report & Kish Effective Sample Size
  - Dataset Artifact Export with SHA-256 Manifest
  - PyTorch DataLoader (Standard & Importance Sampling)
  - PyTorch SampleWeightedCrossEntropyLoss & FocalLoss
  - 50,000-Row Execution Benchmark (< 2.0s SLA)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import torch

from nexus_scalp.labeling.sample_weights import (
    SampleUniquenessReport,
    SampleWeightedCrossEntropyLoss,
    SampleWeightedFocalLoss,
    WeightedTensorDataset,
    add_sample_weights_to_dataframe,
    apply_time_decay,
    compute_concurrency_events,
    compute_return_attributed_weights,
    compute_sample_uniqueness,
    compute_uniqueness_metrics,
    create_weighted_dataloader,
    export_sample_weights_artifact,
    normalize_sample_weights,
)
from nexus_scalp.labeling.triple_barrier import TripleBarrierConfig, TripleBarrierLabeler
from scripts.data.ingest_historical_candles import generate_synthetic_bars
from tests.e2e.chain_clock import budget_cpu_ms

# ML-QA-018: the 50k-row SLA is measured in CPU time (process_time), not wall
# clock. Calibrated against the measured cost on a 2-core CPU-only host
# (~0.87 ms CPU for the 50k-row vectorized path), the budget carries ~570x
# margin: enough for any runner core count, tight enough that a real O(n^2)
# regression (the failure the benchmark exists to catch) blows through it.
_UNIQUENESS_50K_BUDGET_CPU_MS = 500.0

# =============================================================================
# 1. Concurrency Counting (compute_concurrency_events)
# =============================================================================


def test_concurrency_empty() -> None:
    """Empty inputs produce zero-length or zeroed array without error."""
    c = compute_concurrency_events([], [])
    assert len(c) == 0

    c_sized = compute_concurrency_events([], [], total_bars=10)
    assert len(c_sized) == 10
    assert np.all(c_sized == 0)


def test_concurrency_length_mismatch() -> None:
    """Mismatched start and end array lengths raise ValueError."""
    with pytest.raises(ValueError, match="identical length"):
        compute_concurrency_events([0, 1], [5])


def test_concurrency_non_overlapping() -> None:
    """Disjoint intervals yield concurrency = 1 on active bars, 0 elsewhere."""
    starts = [0, 10, 20]
    ends = [4, 14, 24]
    c = compute_concurrency_events(starts, ends, total_bars=30)

    assert len(c) == 30
    assert np.all(c[0:5] == 1)
    assert np.all(c[5:10] == 0)
    assert np.all(c[10:15] == 1)
    assert np.all(c[15:20] == 0)
    assert np.all(c[20:25] == 1)
    assert np.all(c[25:30] == 0)


def test_concurrency_overlapping_stepped() -> None:
    """Staggered overlapping intervals produce correct staircase concurrency."""
    starts = [0, 3, 5]
    ends = [4, 6, 8]
    c = compute_concurrency_events(starts, ends, total_bars=10)

    # Event 0: [0, 4] -> bars 0, 1, 2, 3, 4
    # Event 1: [3, 6] -> bars 3, 4, 5, 6
    # Event 2: [5, 8] -> bars 5, 6, 7, 8
    expected = [1, 1, 1, 2, 2, 2, 2, 1, 1, 0]
    np.testing.assert_array_equal(c, expected)


# =============================================================================
# 2. Mathematical Uniqueness Contract
# =============================================================================


def test_uniqueness_strictly_non_overlapping() -> None:
    """Non-overlapping samples yield u_i = 1.0 exactly (Acceptance Criterion 2)."""
    starts = [0, 10, 20, 30]
    ends = [5, 15, 25, 35]

    u = compute_sample_uniqueness(start_indices=starts, end_indices=ends, normalize=False)
    assert len(u) == 4
    assert u.dtype == np.float32
    np.testing.assert_allclose(u, [1.0, 1.0, 1.0, 1.0], atol=1e-6)


def test_uniqueness_identical_overlap() -> None:
    """Two identical overlapping intervals yield u_1 = u_2 = 0.5 exactly."""
    starts = [0, 0]
    ends = [5, 5]

    u = compute_sample_uniqueness(start_indices=starts, end_indices=ends, normalize=False)
    assert len(u) == 2
    np.testing.assert_allclose(u, [0.5, 0.5], atol=1e-6)

    # Three identical intervals -> 1/3 each
    u3 = compute_sample_uniqueness(
        start_indices=[10, 10, 10], end_indices=[15, 15, 15], normalize=False
    )
    np.testing.assert_allclose(u3, [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], atol=1e-6)


def test_uniqueness_hand_calculated_fractional() -> None:
    """Verifies mathematically exact fractional uniqueness on hand-calculated cases."""
    # Event 0: [0, 5] (len 6)
    # Event 1: [3, 8] (len 6)
    # Concurrency:
    # bars 0, 1, 2: c=1 (1/c = 1.0)
    # bars 3, 4, 5: c=2 (1/c = 0.5)
    # bars 6, 7, 8: c=1 (1/c = 1.0)
    # Event 0: 3*1.0 + 3*0.5 = 4.5 -> 4.5 / 6 = 0.75
    # Event 1: 3*0.5 + 3*1.0 = 4.5 -> 4.5 / 6 = 0.75
    starts = [0, 3]
    ends = [5, 8]
    u = compute_sample_uniqueness(start_indices=starts, end_indices=ends, normalize=False)

    np.testing.assert_allclose(u, [0.75, 0.75], atol=1e-6)


def test_uniqueness_bounds_invariant() -> None:
    """Proves that for any arbitrary overlapping set, u_i in (0.0, 1.0]."""
    rng = np.random.default_rng(42)
    m = 200
    n = 1000
    starts = rng.integers(0, n - 30, size=m)
    durations = rng.integers(1, 20, size=m)
    ends = starts + durations

    u = compute_sample_uniqueness(start_indices=starts, end_indices=ends, normalize=False)
    assert np.all(u > 0.0)
    assert np.all(u <= 1.0)


# =============================================================================
# 3. Polars DataFrame & Masking Integration
# =============================================================================


def test_uniqueness_polars_with_holding_bars() -> None:
    """Computes uniqueness from a Polars DataFrame with explicit holding_bars."""
    df = pl.DataFrame(
        {
            "close": [100.0, 101.0, 102.0, 103.0, 104.0],
            "holding_bars": [2, 1, 3, 1, 2],
        }
    )

    weights = compute_sample_uniqueness(df=df, holding_bars="holding_bars", normalize=False)
    assert len(weights) == 5
    assert weights.dtype == np.float32
    assert np.all(weights > 0.0)
    assert np.all(weights <= 1.0)


def test_uniqueness_polars_evaluated_mask() -> None:
    """Ensures non-evaluated samples (is_eval_sample=False) receive weight 0.0."""
    df = pl.DataFrame(
        {
            "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "holding_bars": [2, 2, 2, 2, 2, 2],
            "is_eval_sample": [True, False, True, False, True, False],
        }
    )

    weights = compute_sample_uniqueness(
        df=df, holding_bars="holding_bars", evaluated_only=True, normalize=False
    )
    assert len(weights) == 6
    assert weights[1] == 0.0
    assert weights[3] == 0.0
    assert weights[5] == 0.0

    assert weights[0] > 0.0
    assert weights[2] > 0.0
    assert weights[4] > 0.0


def test_add_sample_weights_to_dataframe() -> None:
    """Verifies adding sample_weight column directly to Polars DataFrame."""
    df = pl.DataFrame(
        {
            "close": [2000.0, 2001.0, 2002.0, 2003.0],
            "holding_bars": [1, 2, 1, 3],
        }
    )

    res_df = add_sample_weights_to_dataframe(df, holding_bars="holding_bars", normalize=True)
    assert "sample_weight" in res_df.columns
    w = res_df["sample_weight"].to_numpy()
    assert len(w) == 4
    np.testing.assert_allclose(np.sum(w), 4.0, atol=1e-5)


# =============================================================================
# 4. Normalization, Return Attribution & Time Decay
# =============================================================================


def test_normalize_sample_weights() -> None:
    """Normalized sample weights sum to target sample count."""
    raw_u = np.array([0.5, 0.25, 0.25, 0.0], dtype=np.float32)
    norm_w = normalize_sample_weights(raw_u, target_sum=3.0)

    # Positives are 3 samples, sum should equal 3.0
    assert norm_w[3] == 0.0  # zero preserved
    np.testing.assert_allclose(np.sum(norm_w), 3.0, atol=1e-6)
    assert norm_w[0] == 1.5
    assert norm_w[1] == 0.75
    assert norm_w[2] == 0.75


def test_return_attributed_weights() -> None:
    """Return-attributed weights correctly scale uniqueness by return magnitude."""
    u = np.array([1.0, 1.0], dtype=np.float32)
    returns = np.array([1.0, 3.0], dtype=np.float64)

    attr_w = compute_return_attributed_weights(u, returns, normalize=True)
    assert len(attr_w) == 2
    np.testing.assert_allclose(np.sum(attr_w), 2.0, atol=1e-6)
    # The 3.0 return should have 3x the weight of the 1.0 return
    assert attr_w[1] == pytest.approx(attr_w[0] * 3.0, rel=1e-4)


def test_time_decay() -> None:
    """Time decay applies linear ramp from decay_factor to 1.0."""
    w = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32)
    decayed = apply_time_decay(w, decay_factor=0.5)

    assert len(decayed) == 4
    np.testing.assert_allclose(np.sum(decayed), 4.0, atol=1e-5)
    assert decayed[0] < decayed[-1]
    assert decayed[-1] > 1.0


# =============================================================================
# 5. Diagnostic Metrics & Artifact Export
# =============================================================================


def test_uniqueness_metrics_report() -> None:
    """Validates metrics report fields and Kish effective sample size."""
    weights = np.array([1.0, 0.5, 0.5, 1.0], dtype=np.float32)
    starts = np.array([0, 10, 10, 20])
    ends = np.array([5, 15, 15, 25])

    report = compute_uniqueness_metrics(
        weights, start_indices=starts, end_indices=ends, total_bars=30
    )
    assert isinstance(report, SampleUniquenessReport)
    assert report.total_samples == 4
    assert report.evaluated_samples == 4
    assert report.min_uniqueness == 0.5
    assert report.max_uniqueness == 1.0
    assert report.mean_uniqueness == 0.75
    assert report.max_concurrency == 2
    assert report.effective_sample_size > 0.0
    assert 0.0 <= report.redundancy_ratio <= 1.0
    assert isinstance(report.to_dict(), dict)


def test_export_sample_weights_artifact(tmp_path: Path) -> None:
    """Verifies Parquet export and companion .meta.json generation with SHA-256."""
    df = pl.DataFrame(
        {
            "close": [100.0, 101.0, 102.0],
            "holding_bars": [1, 2, 1],
        }
    )
    target_parquet = tmp_path / "artifacts" / "weighted_dataset.parquet"
    out_path = export_sample_weights_artifact(df, target_parquet, holding_bars="holding_bars")

    assert out_path.exists()
    assert out_path.suffix == ".parquet"

    meta_path = out_path.with_suffix(".meta.json")
    assert meta_path.exists()
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    assert meta["row_count"] == 3
    assert len(meta["sha256"]) == 64
    assert "metrics" in meta

    # Re-read Parquet
    read_df = pl.read_parquet(out_path)
    assert "sample_weight" in read_df.columns
    assert len(read_df) == 3


# =============================================================================
# 6. PyTorch Training Integration (DataLoader & Loss Functions)
# =============================================================================


def test_pytorch_weighted_dataset() -> None:
    """Verifies WeightedTensorDataset length, indexing, and types."""
    features = torch.randn(20, 50)
    targets = torch.randint(0, 3, (20,))
    weights = torch.ones(20)

    ds = WeightedTensorDataset(features, targets, weights)
    assert len(ds) == 20

    x, y, w = ds[0]
    assert x.shape == (50,)
    assert y.shape == ()
    assert w.shape == ()
    assert x.dtype == torch.float32
    assert y.dtype == torch.long
    assert w.dtype == torch.float32


def test_pytorch_create_weighted_dataloader() -> None:
    """Verifies dataloader batching in both standard and sampler modes."""
    features = torch.randn(64, 50)
    targets = torch.randint(0, 3, (64,))
    weights = torch.rand(64) + 0.1

    # Standard mode (use_sampler=False)
    loader_std = create_weighted_dataloader(
        features, targets, weights, batch_size=16, use_sampler=False
    )
    batch_x, batch_y, batch_w = next(iter(loader_std))
    assert batch_x.shape == (16, 50)
    assert batch_y.shape == (16,)
    assert batch_w.shape == (16,)

    # Importance sampling mode (use_sampler=True)
    loader_samp = create_weighted_dataloader(
        features, targets, weights, batch_size=16, use_sampler=True
    )
    batch_x2, batch_y2, batch_w2 = next(iter(loader_samp))
    assert batch_x2.shape == (16, 50)
    assert batch_y2.shape == (16,)
    assert batch_w2.shape == (16,)


def test_pytorch_sample_weighted_cross_entropy() -> None:
    """Proves SampleWeightedCrossEntropyLoss matches F.cross_entropy when w=1.0."""
    criterion = SampleWeightedCrossEntropyLoss()

    logits = torch.tensor([[2.0, 1.0, 0.1], [0.2, 3.0, 0.5]], dtype=torch.float32)
    targets = torch.tensor([0, 1], dtype=torch.long)

    # Standard CE
    expected_loss = torch.nn.functional.cross_entropy(logits, targets)

    # Sample weighted CE with unit weights
    unit_weights = torch.tensor([1.0, 1.0], dtype=torch.float32)
    loss = criterion(logits, targets, sample_weights=unit_weights)

    torch.testing.assert_close(loss, expected_loss)

    # Skewed weights should change the loss
    skewed_weights = torch.tensor([10.0, 1.0], dtype=torch.float32)
    skewed_loss = criterion(logits, targets, sample_weights=skewed_weights)
    assert not torch.isclose(skewed_loss, expected_loss)


def test_pytorch_sample_weighted_focal_loss() -> None:
    """Verifies SampleWeightedFocalLoss executes and scales with sample weights."""
    focal_loss = SampleWeightedFocalLoss(gamma=2.0)

    logits = torch.randn(10, 3, requires_grad=True)
    targets = torch.randint(0, 3, (10,))
    weights = torch.rand(10) + 0.5

    loss = focal_loss(logits, targets, sample_weights=weights)
    assert loss.ndim == 0
    assert loss.item() > 0.0

    # Backpropagation check
    loss.backward()
    assert logits.grad is not None
    assert not torch.isnan(logits.grad).any()


# =============================================================================
# 7. Real Triple Barrier Pipeline End-to-End Test
# =============================================================================


def test_triple_barrier_sample_weights_e2e() -> None:
    """Verifies sample uniqueness calculation on real labeled market data."""
    bars = generate_synthetic_bars(symbol="XAUUSD", count=300, seed=99)
    bars = bars.with_columns(
        [
            pl.Series("atr_m1", np.full(len(bars), 1.5, dtype=np.float64)),
            pl.Series("atr", np.full(len(bars), 1.5, dtype=np.float64)),
        ]
    )
    labeler = TripleBarrierLabeler(include_diagnostics=True)
    labeled_df = labeler.label_dataframe(bars)

    assert "holding_bars" in labeled_df.columns
    assert "is_eval_sample" in labeled_df.columns

    weights = compute_sample_uniqueness(df=labeled_df, normalize=True)
    assert len(weights) == len(labeled_df)

    eval_mask = labeled_df["is_eval_sample"].to_numpy().astype(bool)
    n_eval = int(np.sum(eval_mask))

    # All non-evaluated samples must be 0.0
    assert np.all(weights[~eval_mask] == 0.0)

    # All evaluated samples must be positive
    assert np.all(weights[eval_mask] > 0.0)

    # Sum of normalized evaluated weights must equal number of evaluated samples
    np.testing.assert_allclose(np.sum(weights[eval_mask]), float(n_eval), atol=1e-4)


# =============================================================================
# 8. Execution Benchmark (< 2.0s for 50,000 Rows SLA)
# =============================================================================


def test_benchmark_50k_rows_sla() -> None:
    """Enforces the 50,000-sample SLA on CPU time, not wall clock.

    The uniqueness computation is O(M) difference-array updates + an O(N)
    cumsum, so its cost is bounded by the work, not by the scheduler. Wall
    clock on a 2-core shared CI runner measures co-tenant load (a stalled
    runner slows the clock with zero change in the code under test and trips
    the 2.0s bound); ``time.process_time()`` measures the work. Measured on a
    2-core CPU-only host: ~0.87 ms CPU for 50k rows, so the budget carries
    ~570x margin.
    """
    n = 50_000
    rng = np.random.default_rng(12345)
    starts = np.sort(rng.integers(0, n - 20, size=n))
    durations = rng.integers(1, 16, size=n)
    ends = np.minimum(starts + durations - 1, n - 1)

    with budget_cpu_ms(_UNIQUENESS_50K_BUDGET_CPU_MS) as sw:
        weights = compute_sample_uniqueness(
            start_indices=starts,
            end_indices=ends,
            total_bars=n,
            normalize=True,
        )

    assert len(weights) == n
    assert sw.consumed_ms < _UNIQUENESS_50K_BUDGET_CPU_MS, (
        f"Benchmark failed: 50k rows took {sw.consumed_ms:.3f} ms CPU "
        f"(limit: {_UNIQUENESS_50K_BUDGET_CPU_MS} ms)"
    )
    assert np.all(weights > 0.0)
