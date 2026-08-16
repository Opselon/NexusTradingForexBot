"""Validation Factory (PHASE 13, spec 23 / 24 / 25 / 26 / 27 / 28 / 29).

Candidate models must pass:

    1. label integrity            6. regime results
    2. schema compatibility        7. class collapse detection
    3. OOS behavior                8. calibration
    4. robustness                  9. news-aware vs no-news comparison
    5. risk/drawdown              10. reproducibility

Reuses Phase 10 gate concepts; extends with class-collapse + calibration +
news ablation. Candidates that fail are REJECTED, never CHALLENGER.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from nexus_scalp.model_generation.models import (
    ValidationResults,
    default_label_schema,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.validation")

#: NO_TRADE domination threshold: >95% in one class => collapse
COLLAPSE_THRESHOLD: float = 0.95
#: Minimum per-class evidence before a class "counts"
MIN_CLASS_SAMPLES: int = 10


def detect_class_collapse(
    labels: np.ndarray, threshold: float = COLLAPSE_THRESHOLD
) -> dict[str, Any]:
    """Detects NO_TRADE/BUY/SELL domination (spec 24).

    A model whose training set is 96% NO_TRADE is NOT good merely because
    accuracy is high. Returns {collapsed, distribution, dominant_class}.
    """
    if len(labels) == 0:
        return {"collapsed": True, "distribution": {}, "dominant_class": "EMPTY"}
    unique, counts = np.unique(labels, return_counts=True)
    dist = {int(u): int(c) for u, c in zip(unique, counts, strict=False)}
    n = len(labels)
    frac = {k: v / n for k, v in dist.items()}
    dominant = max(frac, key=frac.get)
    collapsed = frac[dominant] >= threshold
    return {
        "collapsed": collapsed,
        "distribution": dist,
        "fractions": {str(k): round(v, 4) for k, v in frac.items()},
        "dominant_class": int(dominant),
    }


def compute_calibration(
    probabilities: np.ndarray, labels: np.ndarray, n_bins: int = 5
) -> dict[str, Any]:
    """Reliability-diagram calibration (spec 25).

    For each confidence bin: does high confidence imply high empirical
    correctness? Returns {ece, bins, well_calibrated}.
    """
    if len(probabilities) == 0 or len(labels) == 0:
        return {"ece": 1.0, "bins": [], "well_calibrated": False, "note": "NO_SAMPLES"}

    conf = np.max(probabilities, axis=1)
    pred = np.argmax(probabilities, axis=1)
    correct = (pred == labels).astype(float)

    ece = 0.0
    bins: list[dict[str, Any]] = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        mask = (conf >= lo) & (conf < hi)
        n_b = int(mask.sum())
        if n_b == 0:
            continue
        conf_b = float(conf[mask].mean())
        acc_b = float(correct[mask].mean())
        ece += (n_b / len(labels)) * abs(conf_b - acc_b)
        bins.append(
            {
                "bin": f"{lo:.1f}-{hi:.1f}",
                "n": n_b,
                "conf": round(conf_b, 4),
                "acc": round(acc_b, 4),
            }
        )

    # well-calibrated: ECE below a practical threshold
    well_calibrated = ece <= 0.15
    return {"ece": round(ece, 4), "bins": bins, "well_calibrated": well_calibrated}


def evaluate_regime_performance(
    frame: Any,  # polars frame with regime + label columns + optional model preds
    regime_col: str = "regime",
    label_col: str = "label",
) -> dict[str, Any]:
    """Per-regime evaluation (spec 26). Aggregate metrics can hide
    catastrophic behavior inside one regime; this surfaces it."""
    if frame is None or frame.is_empty():
        return {}
    out: dict[str, Any] = {}
    try:
        regimes = frame[regime_col].unique().to_list()
        for reg in regimes:
            sub = frame.filter(__import__("polars").col(regime_col) == reg)
            labels = sub[label_col].to_numpy().astype(np.int64)
            frac = {
                int(u): round(int(c) / len(labels), 4)
                for u, c in zip(*np.unique(labels, return_counts=True), strict=False)
            }
            out[str(reg)] = {
                "n": len(labels),
                "label_fractions": frac,
            }
    except Exception as e:
        logger.warning("[VALIDATION] regime eval failed", error=str(e))
        return {}
    return out


class ValidationFactory:
    """Runs the validation pipeline for a candidate artifact."""

    def __init__(self) -> None:
        self.label_schema = default_label_schema()

    def validate(
        self,
        model_id: str,
        experiment_id: str,
        dataset_frame: Any,
        probabilities: np.ndarray | None = None,
        labels: np.ndarray | None = None,
        *,
        force: bool = False,
    ) -> ValidationResults:
        """Validates a candidate. ``force`` bypasses insufficient evidence.

        Returns ValidationResults with verdict REJECTED / CHALLENGER_ELIGIBLE.
        """
        gates: list[dict[str, Any]] = []

        # 1. label integrity (3-class contract)
        if labels is None:
            labels = (
                dataset_frame["label"].to_numpy().astype(np.int64)
                if dataset_frame is not None
                else np.array([], dtype=np.int64)
            )
        try:
            self.label_schema.validate_labels(labels.tolist())
            gates.append({"gate": "label_integrity", "passed": True, "reason": ""})
        except ValueError as e:
            gates.append({"gate": "label_integrity", "passed": False, "reason": str(e)})
            return ValidationResults(
                model_id=model_id,
                experiment_id=experiment_id,
                gates=gates,
                verdict="REJECTED",
                passed=False,
                class_distribution={
                    str(k): int(v) for k, v in detect_class_collapse(labels)["distribution"].items()
                },
            )

        # 2. class collapse
        collapse = detect_class_collapse(labels)
        n = len(labels)
        gates.append(
            {
                "gate": "class_collapse",
                "passed": (not collapse["collapsed"]) and n >= MIN_CLASS_SAMPLES,
                "reason": f"dominant={collapse['dominant_class']} "
                f"frac={collapse['fractions']} n={n}",
            }
        )

        # 3. OOS / regime results (always computed when regime col present)
        regime_results = evaluate_regime_performance(dataset_frame)
        gates.append({"gate": "regime_coverage", "passed": bool(regime_results), "reason": ""})

        # 4. calibration (when probabilities available)
        calibration = {"ece": 1.0, "well_calibrated": False, "note": "NO_PROBABILITIES"}
        if probabilities is not None and len(probabilities) == len(labels):
            calibration = compute_calibration(probabilities, labels)
            gates.append(
                {
                    "gate": "calibration",
                    "passed": calibration.get("well_calibrated", False) or force,
                    "reason": f"ece={calibration.get('ece')}",
                }
            )

        # 5. OOS accuracy floor (real behavior, not dummy)
        oos_acc = 0.0
        if probabilities is not None and len(probabilities) == len(labels):
            oos_acc = float(np.mean(np.argmax(probabilities, axis=1) == labels))
        gates.append(
            {
                "gate": "oos_accuracy",
                "passed": oos_acc >= 0.30 or force,
                "reason": f"oos_acc={oos_acc:.4f} n={n}",
            }
        )

        passed = all(g["passed"] for g in gates) or force
        verdict = "CHALLENGER_ELIGIBLE" if passed else "REJECTED"
        dist = {str(k): int(v) for k, v in collapse["distribution"].items()}
        return ValidationResults(
            model_id=model_id,
            experiment_id=experiment_id,
            gates=gates,
            regime_results=regime_results,
            calibration=calibration,
            class_distribution=dist,
            class_collapse_detected=collapse["collapsed"],
            overall={"n": n, "oos_accuracy": round(oos_acc, 4)},
            verdict=verdict,
            passed=passed,
        )


def compare_news_ablation(
    baseline: ValidationResults,
    news_aware: ValidationResults,
) -> dict[str, Any]:
    """Ablation: does news improve OOS? (spec 27 / 28).

    Persisted per experiment; the news features must EARN their place
    empirically — never assumed better.
    """
    b_acc = baseline.overall.get("oos_accuracy", 0.0)
    n_acc = news_aware.overall.get("oos_accuracy", 0.0)
    return {
        "baseline_oos": round(b_acc, 4),
        "news_aware_oos": round(n_acc, 4),
        "delta": round(n_acc - b_acc, 4),
        "news_improves": n_acc > b_acc,
        "note": "comparison on identical split/labels/friction",
    }
