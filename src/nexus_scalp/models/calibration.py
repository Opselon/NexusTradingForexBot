"""Probability calibration for the trading head (temperature scaling).

Why the model needed this
-------------------------
The decision gates compare a *probability* against a fixed threshold
(``model.confidence_threshold``). A softmax head trained with cross-entropy on
a few million ticks is systematically over-confident: its 0.60 does not mean
"60% of these trades win", it means "this looks like the easy 60% of the
training set". Gating on an over-confident score admits trades the model
cannot actually rank, and rejecting with the same score throws away trades
that were fine — the gate is calibrated against the wrong quantity.

What is here
------------
* ``expected_calibration_error`` — the metric the gate is judged by.
* ``fit_temperature`` — one scalar fitted on held-out logits, the smallest
  change that makes probabilities mean what they say (no architecture change,
  no re-training, no new failure surface).
* ``apply_temperature`` — the runtime transform (works on probabilities or
  logits; preserves the ranking and the argmax exactly).
* ``CalibrationArtifact`` — the reproducible JSON the runtime loads, with the
  ECE before/after and the sample size it was fitted on.

Design rules
------------
1. Monotone by construction: temperature scaling divides logits by T > 0, so
   the predicted class never changes — only the confidence attached to it.
2. Fail-open: a missing, stale or malformed artifact means "use raw
   probabilities", never a blocked trade or a crash.
3. Deterministic: golden-section search over a bounded grid, no RNG anywhere.
"""

from __future__ import annotations

import itertools
import json
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

#: Search range for the temperature. T > 1 softens an over-confident head,
#: T < 1 sharpens an under-confident one; both extremes are bounded so a
#: degenerate fit cannot turn the gate into a constant.
T_MIN = 0.25
T_MAX = 4.0
DEFAULT_BINS = 15


def _as_2d(probabilities: Sequence[Sequence[float]] | Sequence[float] | np.ndarray) -> np.ndarray:
    array = np.asarray(probabilities, dtype=float)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    return array


def _normalize(array: np.ndarray) -> np.ndarray:
    clipped = np.clip(array, 1e-12, None)
    return clipped / clipped.sum(axis=1, keepdims=True)


def expected_calibration_error(
    probabilities: Sequence[Sequence[float]] | np.ndarray,
    labels: Sequence[int] | np.ndarray,
    bins: int = DEFAULT_BINS,
) -> float:
    """Top-label ECE: a weighted mean of |accuracy - confidence| per bin."""
    probs = _normalize(_as_2d(probabilities))
    truth = np.asarray(labels, dtype=int).reshape(-1)
    if probs.shape[0] != truth.shape[0] or probs.shape[0] == 0:
        return 0.0
    confidence = probs.max(axis=1)
    predicted = probs.argmax(axis=1)
    correct = (predicted == truth).astype(float)

    edges = np.linspace(0.0, 1.0, bins + 1)
    total = float(probs.shape[0])
    ece = 0.0
    for low, high in itertools.pairwise(edges):
        mask = (confidence > low) & (confidence <= high)
        count = float(mask.sum())
        if count == 0.0:
            continue
        bin_acc = correct[mask].mean()
        bin_conf = confidence[mask].mean()
        ece += (count / total) * abs(bin_acc - bin_conf)
    return float(ece)


def apply_temperature(
    probabilities: Sequence[Sequence[float]] | Sequence[float] | np.ndarray,
    temperature: float,
) -> np.ndarray:
    """Re-shape probabilities at temperature ``T`` (argmax preserved)."""
    probs = _normalize(_as_2d(probabilities))
    temp = float(temperature)
    if temp <= 0.0 or abs(temp - 1.0) < 1e-9:
        return probs
    logits = np.log(probs) / temp
    logits -= logits.max(axis=1, keepdims=True)
    exp = np.exp(logits)
    return exp / exp.sum(axis=1, keepdims=True)


def _nll_at(probabilities: np.ndarray, labels: np.ndarray, temperature: float) -> float:
    calibrated = np.clip(apply_temperature(probabilities, temperature), 1e-12, 1.0)
    picked = calibrated[np.arange(labels.shape[0]), labels]
    return float(-np.mean(np.log(picked)))


def fit_temperature(
    probabilities: Sequence[Sequence[float]] | np.ndarray,
    labels: Sequence[int] | np.ndarray,
    *,
    iterations: int = 60,
) -> float:
    """Golden-section search for the T minimising held-out NLL."""
    probs = _normalize(_as_2d(probabilities))
    truth = np.asarray(labels, dtype=int).reshape(-1)
    if probs.shape[0] == 0 or probs.shape[0] != truth.shape[0]:
        return 1.0

    low, high = T_MIN, T_MAX
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    left = high - phi * (high - low)
    right = low + phi * (high - low)
    f_left, f_right = _nll_at(probs, truth, left), _nll_at(probs, truth, right)
    for _ in range(iterations):
        if f_left < f_right:
            high, right, f_right = right, left, f_left
            left = high - phi * (high - low)
            f_left = _nll_at(probs, truth, left)
        else:
            low, left, f_left = left, right, f_right
            right = low + phi * (high - low)
            f_right = _nll_at(probs, truth, right)
    return float(round((low + high) / 2.0, 4))


@dataclass(frozen=True)
class CalibrationArtifact:
    """The persisted fit: what the runtime loads and what an auditor diffs."""

    temperature: float
    ece_before: float
    ece_after: float
    samples: int
    bins: int = DEFAULT_BINS
    generated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "temperature": round(self.temperature, 6),
            "ece_before": round(self.ece_before, 6),
            "ece_after": round(self.ece_after, 6),
            "samples": self.samples,
            "bins": self.bins,
            "generated_at": self.generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        }


def fit_artifact(
    probabilities: Sequence[Sequence[float]] | np.ndarray,
    labels: Sequence[int] | np.ndarray,
    bins: int = DEFAULT_BINS,
) -> CalibrationArtifact:
    probs = _normalize(_as_2d(probabilities))
    truth = np.asarray(labels, dtype=int).reshape(-1)
    temperature = fit_temperature(probs, truth)
    before = expected_calibration_error(probs, truth, bins)
    after = expected_calibration_error(apply_temperature(probs, temperature), truth, bins)
    return CalibrationArtifact(
        temperature=temperature,
        ece_before=before,
        ece_after=after,
        samples=int(probs.shape[0]),
        bins=bins,
    )


def write_artifact(path: Path | str, artifact: CalibrationArtifact) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(artifact.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp, target)
    return target


def read_artifact(path: Path | str) -> dict[str, Any] | None:
    """Load + validate; anything unusable means 'no calibration' (fail-open)."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        temperature = float(payload.get("temperature", 1.0))
    except (TypeError, ValueError):
        return None
    if not (T_MIN <= temperature <= T_MAX):
        return None
    return {
        "temperature": temperature,
        "ece_before": float(payload.get("ece_before", 0.0) or 0.0),
        "ece_after": float(payload.get("ece_after", 0.0) or 0.0),
        "samples": int(payload.get("samples", 0) or 0),
    }


def artifacts_root() -> Path:
    override = os.environ.get("NEXUS_ARTIFACTS_DIR")
    return Path(override) if override else Path("artifacts")
