"""Feature-distribution drift monitor over the experience ledger (PHASE 4B).

Reuses the PSI math proven in ``news.drift`` (quantile-binned, smoothing
floor, identical thresholds 0.10 WARNING / 0.25 CRITICAL) and applies it to
FULL feature vectors recorded in experience snapshots: the reference window
is the earlier experience cohort, the current window the newest one. A drift
event is only actionable when BOTH windows have >= min_samples (INSUFFICIENT_DATA
otherwise forbids action — no behavioral change on thin evidence) and the
mean PSI crosses the configured threshold.

Deliberately minimal: no new statistical framework, no scheduler — the
training worker calls ``feature_drift_check`` when configured and the result
is logged + returned for observability. It NEVER mutates trading/risk/model
state by itself (learning stays on the paper side).
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.news.drift import _psi_1d
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.feature_drift")

WARNING_PSI = 0.10
CRITICAL_PSI = 0.25
MIN_SAMPLES = 30
MAX_FEATURES_CHECKED = 70


def feature_drift_check(
    reference_vectors: list[list[float]],
    current_vectors: list[list[float]],
    *,
    warning_psi: float = WARNING_PSI,
    critical_psi: float = CRITICAL_PSI,
    min_samples: int = MIN_SAMPLES,
) -> dict[str, Any]:
    """PSI per feature index between two experience cohorts.

    Returns {"verdict", "mean_psi", "max_psi", "worst_feature", "n_features",
    "reference_n", "current_n"} — verdict is INSUFFICIENT_DATA when either
    window is below ``min_samples`` (action forbidden), otherwise WARNING /
    CRITICAL / NORMAL by the mean PSI against the thresholds.
    """
    if len(reference_vectors) < min_samples or len(current_vectors) < min_samples:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "mean_psi": 0.0,
            "max_psi": 0.0,
            "worst_feature": -1,
            "n_features": 0,
            "reference_n": len(reference_vectors),
            "current_n": len(current_vectors),
        }
    dim = min(
        *(len(v) for v in reference_vectors),
        *(len(v) for v in current_vectors),
        MAX_FEATURES_CHECKED,
    )
    psis: list[float] = []
    for idx in range(dim):
        ref = [float(v[idx]) for v in reference_vectors]
        cur = [float(v[idx]) for v in current_vectors]
        psis.append(_psi_1d(ref, cur))
    mean_psi = sum(psis) / len(psis) if psis else 0.0
    worst_idx = max(range(len(psis)), key=lambda i: psis[i]) if psis else -1
    max_psi = psis[worst_idx] if psis else 0.0
    if mean_psi >= critical_psi:
        verdict = "CRITICAL"
    elif mean_psi >= warning_psi:
        verdict = "WARNING"
    else:
        verdict = "NORMAL"
    result = {
        "verdict": verdict,
        "mean_psi": round(mean_psi, 4),
        "max_psi": round(max_psi, 4),
        "worst_feature": worst_idx,
        "n_features": len(psis),
        "reference_n": len(reference_vectors),
        "current_n": len(current_vectors),
    }
    logger.info("[FEATURE_DRIFT] event=CHECK", **result)
    return result


__all__ = ["CRITICAL_PSI", "MIN_SAMPLES", "WARNING_PSI", "feature_drift_check"]
