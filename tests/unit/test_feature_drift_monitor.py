"""Feature-drift monitor tests (money-path PHASE 4B)."""

from __future__ import annotations

import random

import pytest

from nexus_scalp.model_lifecycle.feature_drift import feature_drift_check


def test_insufficient_data_forbids_action() -> None:
    ref = [[0.0] * 50 for _ in range(10)]
    cur = [[0.0] * 50 for _ in range(10)]
    result = feature_drift_check(ref, cur)
    assert result["verdict"] == "INSUFFICIENT_DATA"


def test_identical_windows_are_normal() -> None:
    rng = random.Random(3)
    ref = [[rng.gauss(0, 1) for _ in range(50)] for _ in range(200)]
    cur = [list(v) for v in ref]
    result = feature_drift_check(ref, cur)
    assert result["verdict"] == "NORMAL"
    assert result["mean_psi"] < 0.01


def test_shifted_window_is_critical() -> None:
    rng = random.Random(4)
    ref = [[rng.gauss(0, 1) for _ in range(50)] for _ in range(300)]
    cur = [[rng.gauss(3.0, 1) for _ in range(50)] for _ in range(300)]
    result = feature_drift_check(ref, cur)
    assert result["verdict"] == "CRITICAL"
    assert result["mean_psi"] > 0.25


def test_mild_shift_warns() -> None:
    rng = random.Random(5)
    ref = [[rng.gauss(0, 1) for _ in range(50)] for _ in range(400)]
    cur = [[rng.gauss(0.35, 1) for _ in range(50)] for _ in range(400)]
    result = feature_drift_check(ref, cur)
    assert result["verdict"] in ("WARNING", "CRITICAL")
    assert result["mean_psi"] >= 0.10


def test_degenerate_constant_reference_handled() -> None:
    ref = [[0.0] * 50 for _ in range(100)]
    cur = [[1.0] * 50 for _ in range(100)]
    result = feature_drift_check(ref, cur)
    # constant reference with full mass departure -> honest CRITICAL
    assert result["verdict"] == "CRITICAL"
