"""Oversampling determinism tests (research/training-parity P1).

Contract:
  * _balance_oversample_dataset uses a LOCAL np.random.default_rng(seed)
    Generator — the global numpy RNG state is never read or mutated.
  * Same (dataset, seed) => identical oversampled indices + class balance.
  * Different seed => a different valid sample (may differ, still balanced).
  * The trainer's fine-tune path derives the seed from the canonical
    training provenance (self.seed).
"""

from __future__ import annotations

import numpy as np
import pytest

from nexus_scalp.training.walk_forward_trainer import _balance_oversample_dataset


def _buffer(n: int = 300, seed: int = 5) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 6)).astype(np.float32)
    # Heavily imbalanced: ~90% NO_TRADE (0), ~5% BUY (1), ~5% SELL (2).
    y = rng.choice([0, 1, 2], size=n, p=[0.90, 0.05, 0.05]).astype(np.int64)
    return X, y


def test_same_seed_yields_identical_output() -> None:
    X, y = _buffer()
    a = _balance_oversample_dataset(X, y, seed=42)
    b = _balance_oversample_dataset(X, y, seed=42)
    assert np.array_equal(a[0], b[0])
    assert np.array_equal(a[1], b[1])
    assert np.bincount(a[1], minlength=3).tolist() == np.bincount(b[1], minlength=3).tolist()


def test_different_seed_yields_different_valid_sample() -> None:
    X, y = _buffer()
    a = _balance_oversample_dataset(X, y, seed=1)
    b = _balance_oversample_dataset(X, y, seed=2)
    # Same class balance policy (same boost ratio) but permuted/sampled rows.
    assert np.bincount(a[1], minlength=3).tolist() == np.bincount(b[1], minlength=3).tolist()
    # A different seed must be able to produce a different ordering/content;
    # with this imbalanced buffer the remainder sampling makes identical
    # output across seeds astronomically unlikely but not impossible, so the
    # assertion is on the determinism of EACH seed (reproducible per seed),
    # plus at least the shuffle order differing across a 6-run poll.
    outputs = [
        _balance_oversample_dataset(X, y, seed=s)[1].tobytes() for s in range(6)
    ]
    assert len(set(outputs)) >= 2, "different seeds produced byte-identical buffers"


def test_global_numpy_rng_state_not_mutated() -> None:
    X, y = _buffer()
    np.random.seed(123)
    key_before, pos_before = np.random.get_state()[1], np.random.get_state()[2]
    _balance_oversample_dataset(X, y, seed=7)
    key_after, pos_after = np.random.get_state()[1], np.random.get_state()[2]
    assert np.array_equal(key_before, key_after)  # legacy MT19937 key stream untouched
    assert pos_before == pos_after  # position untouched
    # An independent consumer of the global stream is unaffected: after the
    # oversample call, a fresh seed(999) draw equals the seeded expectation.
    np.random.seed(999)
    expected = np.random.randint(0, 1000)
    np.random.seed(999)
    _balance_oversample_dataset(X, y, seed=3)
    assert np.random.randint(0, 1000) == expected


def test_active_class_boost_balance_semantics_preserved() -> None:
    """Minority BUY/SELL are grown toward 85% of the majority count."""
    X, y = _buffer()
    res_x, res_y = _balance_oversample_dataset(X, y, seed=11)
    counts = np.bincount(res_y, minlength=3)
    majority = int(np.bincount(y, minlength=3)[0])
    # BUY/SELL now reach the boosted target; NO_TRADE unchanged in count.
    assert counts[0] == majority
    boosted = int(majority * 0.85)
    assert counts[1] == boosted and counts[2] == boosted
    # Every resampled row must be a real row of the source buffer (row-grown,
    # never synthetic features).
    src_rows = {r.tobytes() for r in X}
    assert all(r.tobytes() in src_rows for r in res_x)


@pytest.mark.parametrize("seed", [0, 7, 42])
def test_degenerate_single_class_buffer_passthrough(seed: int) -> None:
    X = np.zeros((10, 4), dtype=np.float32)
    y = np.zeros(10, dtype=np.int64)
    res_x, res_y = _balance_oversample_dataset(X, y, seed=seed)
    assert res_x.shape == X.shape and np.array_equal(res_y, y)
