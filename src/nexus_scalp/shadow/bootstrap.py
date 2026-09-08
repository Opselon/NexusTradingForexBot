"""Deterministic paired-difference bootstrap confidence interval (P0).

STATISTICAL PROMOTION EVIDENCE — the promotion gate must quantify uncertainty
over the paired delta metric it already uses (delta_r = shadow_r - champion_r
per outcome-resolved record, same market path).

Requirements encoded here (mission P0):
* deterministic under a fixed seed — the seed is DERIVED from the run_id via
  BLAKE2b unless an explicit seed is passed, so re-evaluating the same run
  reproduces the same interval bit-for-bit (provenance, not folklore);
* independent of the global random state — a dedicated ``numpy.random.Generator``
  is used; ``random``/``np.random`` global state is never touched;
* no future leakage — the bootstrap resamples ONLY the observed paired deltas;
  nothing about labels, features or timestamps is used;
* computationally bounded — resampling is chunked; memory is O(chunk * n);
* fail-closed — malformed input (non-finite values, empty, wrong dtype shape)
  raises :class:`BootstrapError`; the caller must treat that as a veto, never
  as a pass.

Method: percentile bootstrap on the MEAN of paired deltas (Efron). The paired
design is used because the comparison is already paired per record (same tick
path for both models) — pairing removes per-record market variance and is the
higher-powered, more honest estimator for this data.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np

__all__ = [
    "BootstrapError",
    "bootstrap_mean_ci",
    "derive_seed_from_run_id",
]

#: Chunk size for resampling (rows of the index matrix). Bounds memory at
#: CHUNK_RESAMPLES * n int64 values (e.g. 512 * 5000 * 8B = ~20MB) regardless
#: of the requested resample count.
CHUNK_RESAMPLES: int = 512


class BootstrapError(ValueError):
    """Raised when the bootstrap inputs are malformed (fail-closed)."""


def derive_seed_from_run_id(run_id: str) -> int:
    """Derives a stable 32-bit seed from the run identity.

    Deterministic policy: BLAKE2b-256 over the UTF-8 run_id, first 4 bytes
    big-endian. The same run always yields the same CI; different runs are
    independent. Global RNG state is never consulted or modified.
    """
    digest = hashlib.blake2b(str(run_id).encode("utf-8"), digest_size=32).digest()
    return int.from_bytes(digest[:4], "big")


def bootstrap_mean_ci(
    deltas: list[float] | tuple[float, ...] | np.ndarray,
    *,
    resamples: int,
    confidence_level: float,
    seed: int,
) -> dict[str, float | int | str]:
    """Percentile bootstrap CI for the mean of paired deltas.

    Args:
        deltas: paired differences (challenger - champion) per record.
        resamples: bootstrap replicate count (policy value, >= 100).
        confidence_level: two-sided confidence level in (0, 1), e.g. 0.95.
        seed: explicit RNG seed for the dedicated Generator.

    Returns:
        dict with ``mean_delta``, ``median_delta``, ``ci_lower``,
        ``ci_upper``, ``sample_count``, ``resamples``, ``confidence_level``,
        ``method``. All floats finite.

    Raises:
        BootstrapError: empty input, non-finite values, or out-of-range
            policy arguments. NEVER returns a fabricated interval.
    """
    arr = np.asarray(deltas, dtype=np.float64)
    if arr.ndim != 1 or arr.size == 0:
        raise BootstrapError(
            f"paired deltas must be a non-empty 1-D sequence, got shape {arr.shape}"
        )
    if not np.all(np.isfinite(arr)):
        raise BootstrapError("paired deltas contain non-finite values (NaN/Inf)")
    n = int(arr.size)
    if resamples < 100:
        raise BootstrapError(f"bootstrap resamples {resamples} below floor 100 (unstable interval)")
    if not (0.0 < confidence_level < 1.0):
        raise BootstrapError(f"confidence_level {confidence_level} outside (0, 1)")

    rng = np.random.default_rng(seed)
    # Chunked percentile bootstrap on the mean.
    boot_means = np.empty(resamples, dtype=np.float64)
    done = 0
    while done < resamples:
        k = min(CHUNK_RESAMPLES, resamples - done)
        idx = rng.integers(0, n, size=(k, n))
        boot_means[done : done + k] = arr[idx].mean(axis=1)
        done += k

    alpha = 1.0 - confidence_level
    lo_q = 100.0 * (alpha / 2.0)
    hi_q = 100.0 * (1.0 - alpha / 2.0)
    ci_lower = float(np.percentile(boot_means, lo_q))
    ci_upper = float(np.percentile(boot_means, hi_q))
    mean_delta = float(arr.mean())
    if not (math.isfinite(ci_lower) and math.isfinite(ci_upper) and math.isfinite(mean_delta)):
        raise BootstrapError("bootstrap produced non-finite bounds")
    return {
        "sample_count": n,
        "mean_delta": mean_delta,
        "median_delta": float(np.median(arr)),
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "resamples": int(resamples),
        "confidence_level": float(confidence_level),
        "method": "percentile_bootstrap_mean_paired",
    }
