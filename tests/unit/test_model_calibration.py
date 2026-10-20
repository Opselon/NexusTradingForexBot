"""Calibration tests: an over-confident head must measurably become honest.

The synthetic head below is built the way a real one fails — sharp logits on a
noisy label process — so the fit has a known direction (T > 1) and the ECE has
a known direction (down). Nothing here reads a number out of the source.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from nexus_scalp.models.calibration import (
    apply_temperature,
    expected_calibration_error,
    fit_artifact,
    fit_temperature,
    read_artifact,
    write_artifact,
)


def overconfident_head(seed: int = 7, n: int = 4000, noise: float = 0.25):
    """A 4-class head with sharp logits and a 25%-wrong label process."""
    rng = np.random.default_rng(seed)
    labels = rng.integers(0, 4, size=n)
    logits = np.full((n, 4), -1.0)
    logits[np.arange(n), labels] = 1.0 + rng.normal(2.2, 0.35, size=n)  # very sharp

    # 25% of outcomes ignore the signal (the market's irreducible noise).
    flipped = rng.random(n) < noise
    labels = np.where(flipped, rng.integers(0, 4, size=n), labels)

    logits -= logits.max(axis=1, keepdims=True)
    exp = np.exp(logits)
    probs = exp / exp.sum(axis=1, keepdims=True)
    return probs, labels


def test_a_sharp_head_is_over_confident_before_calibration():
    probs, labels = overconfident_head()
    ece = expected_calibration_error(probs, labels)
    assert ece > 0.10, f"synthetic head should be badly calibrated, ECE={ece}"


def test_temperature_fit_softens_the_head_and_lowers_ece():
    probs, labels = overconfident_head()
    artifact = fit_artifact(probs, labels)
    assert artifact.temperature > 1.0, "an over-confident head needs T > 1"
    assert artifact.ece_after < artifact.ece_before
    assert artifact.samples == probs.shape[0]


def test_temperature_preserves_the_prediction_exactly():
    probs, _labels = overconfident_head(n=200)
    scaled = apply_temperature(probs, 2.7)
    assert (scaled.argmax(axis=1) == probs.argmax(axis=1)).all()
    # ... and the transform still emits distributions.
    assert np.allclose(scaled.sum(axis=1), 1.0, atol=1e-9)


def test_identity_temperature_changes_nothing():
    probs, _ = overconfident_head(n=50)
    assert np.allclose(apply_temperature(probs, 1.0), probs, atol=1e-9)


def test_fit_is_deterministic():
    probs, labels = overconfident_head(n=1500)
    assert fit_temperature(probs, labels) == fit_temperature(probs, labels)


def test_a_honest_head_needs_no_temperature_and_keeps_its_ece():
    """A head whose confidence IS its accuracy must not be moved."""
    rng = np.random.default_rng(3)
    n = 20000
    confidence = rng.uniform(0.4, 0.95, size=n)  # how sure the head claims to be
    right = rng.random(n) < confidence
    probs = np.stack([1.0 - confidence, confidence], axis=1)
    labels = np.where(right, 1, 0)

    assert expected_calibration_error(probs, labels) < 0.02
    temperature = fit_temperature(probs, labels)
    assert 0.8 < temperature < 1.25, temperature
    assert expected_calibration_error(apply_temperature(probs, temperature), labels) < 0.02


def test_artifact_round_trip_and_validation(tmp_path):
    probs, labels = overconfident_head(n=800)
    artifact = fit_artifact(probs, labels)
    path = write_artifact(tmp_path / "calibration.json", artifact)
    loaded = read_artifact(path)
    assert loaded is not None
    assert loaded["temperature"] == pytest.approx(artifact.temperature)


def test_a_malformed_or_absurd_artifact_is_ignored(tmp_path):
    bad = tmp_path / "calibration.json"
    bad.write_text("{not json", encoding="utf-8")
    assert read_artifact(bad) is None

    bad.write_text(json.dumps({"temperature": 99.0}), encoding="utf-8")
    assert read_artifact(bad) is None

    bad.write_text(json.dumps({"temperature": -1}), encoding="utf-8")
    assert read_artifact(bad) is None

    assert read_artifact(tmp_path / "missing.json") is None


def test_empty_input_is_a_no_op_not_a_crash():
    assert expected_calibration_error([], []) == 0.0
    assert fit_temperature([], []) == 1.0
