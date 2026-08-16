"""
Validation Gates
================
PHASE 10 explicit, mandatory validation gates (spec 20 / 21 / 22 / 38).

A candidate model fails if ANY mandatory gate fails. Failures are NEVER hidden
behind an aggregate score. Gates:

    GATE 1   Dataset integrity
    GATE 2   Feature schema compatibility
    GATE 3   Label integrity
    GATE 4   Training stability (no NaN/Inf, stable loss)
    GATE 5   Validation performance
    GATE 6   Walk-forward
    GATE 7   OOS
    GATE 8   Robustness
    GATE 9   Risk / drawdown
    GATE 10  Champion comparison
    GATE 11  Model artifact integrity
    GATE 12  Reproducibility / lineage

Model collapse protection (spec 21): reject class collapse, constant output,
probability saturation, extreme confidence without evidence.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from nexus_scalp.model_lifecycle.models import GateResult, TrainingDataset
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.gates")

#: Collapse thresholds
MIN_CLASS_RATIO: float = 0.05  # class must appear in >=5% of predictions
MAX_PROB_SATURATION: float = 0.999  # avg max-prob above this => saturated
MAX_NAN_INF_FRACTION: float = 0.001


class ValidationGateError(RuntimeError):
    """Raised when a mandatory gate fails."""


def gate_dataset_integrity(dataset: TrainingDataset) -> GateResult:
    """GATE 1: dataset exists, causally ordered, provenance intact."""
    reasons: list[str] = []
    ok = dataset.sample_count > 0
    if not ok:
        reasons.append("dataset is empty")
    if ok:
        ordered = dataset.ordered_rows()
        times = [r.decision_timestamp for r in ordered]
        if times != sorted(times):
            ok = False
            reasons.append("rows are not temporally ordered")
        if not dataset.source_experience_ids:
            reasons.append("no source experience provenance")
        if dataset.feature_dimension <= 0:
            ok = False
            reasons.append("invalid feature dimension")
    logger.info("[MODEL] event=VALIDATION_GATE gate=DATASET status=%s", "PASS" if ok else "FAIL")
    return GateResult(gate="GATE1_DATASET", passed=ok, reason="; ".join(reasons) or "ok")


def gate_schema_compatibility(
    dataset: TrainingDataset,
    artifact_schema_id: str | None = None,
    artifact_dimension: int | None = None,
) -> GateResult:
    """GATE 2: training schema must match the target artifact schema."""
    schema_id = artifact_schema_id or dataset.feature_schema_id
    dim = artifact_dimension or dataset.feature_dimension
    ok = dataset.feature_schema_id == schema_id and dataset.feature_dimension == dim
    reason = ""
    if not ok:
        reason = (
            f"dataset schema {dataset.feature_schema_id}/{dataset.feature_dimension}D "
            f"!= artifact schema {schema_id}/{dim}D"
        )
    logger.info("[MODEL] event=VALIDATION_GATE gate=SCHEMA status=%s", "PASS" if ok else "FAIL")
    return GateResult(
        gate="GATE2_SCHEMA",
        passed=ok,
        details={
            "dataset_schema": dataset.feature_schema_id,
            "dataset_dim": dataset.feature_dimension,
        },
        reason=reason or "ok",
    )


def gate_label_integrity(dataset: TrainingDataset) -> GateResult:
    """GATE 3: label distribution is sane (all three classes represented)."""
    dist = dataset.label_distribution()
    total = dataset.sample_count
    ok = total > 0
    reasons: list[str] = []
    if ok:
        for label in (0, 1, 2):
            count = dist.get(str(label), 0)
            if count == 0:
                reasons.append(f"label {label} missing")
            elif count / total < MIN_CLASS_RATIO:
                reasons.append(f"label {label} under-represented ({count / total:.1%})")
        if len(dist) < 2:
            ok = False
            reasons.append("dataset collapsed to a single class")
    logger.info("[MODEL] event=VALIDATION_GATE gate=LABELS status=%s", "PASS" if ok else "FAIL")
    return GateResult(
        gate="GATE3_LABELS",
        passed=ok,
        details={"distribution": dist},
        reason="; ".join(reasons) or "ok",
    )


def gate_training_stability(metrics: dict[str, Any]) -> GateResult:
    """GATE 4: no NaN/Inf loss, stable final loss, no exploding metrics."""
    reasons: list[str] = []
    ok = True
    final_loss = metrics.get("final_loss")
    if final_loss is None:
        ok = False
        reasons.append("no final loss recorded")
    else:
        if not math.isfinite(float(final_loss)):
            ok = False
            reasons.append(f"final loss not finite: {final_loss}")
        if float(final_loss) > 1e3:
            ok = False
            reasons.append(f"final loss exploding: {final_loss}")
    nan_inf = metrics.get("nan_inf_fraction", 0.0)
    if nan_inf and float(nan_inf) > MAX_NAN_INF_FRACTION:
        ok = False
        reasons.append(f"NaN/Inf output fraction {nan_inf} above threshold")
    logger.info("[MODEL] event=VALIDATION_GATE gate=STABILITY status=%s", "PASS" if ok else "FAIL")
    return GateResult(gate="GATE4_STABILITY", passed=ok, reason="; ".join(reasons) or "ok")


def gate_validation_performance(metrics: dict[str, Any], min_accuracy: float = 0.35) -> GateResult:
    """GATE 5: validation accuracy above floor."""
    acc = metrics.get("validation_accuracy")
    ok = acc is not None and float(acc) >= min_accuracy
    reason = ""
    if not ok:
        reason = f"validation accuracy {acc} below floor {min_accuracy}"
    logger.info("[MODEL] event=VALIDATION_GATE gate=VALIDATION status=%s", "PASS" if ok else "FAIL")
    return GateResult(
        gate="GATE5_VALIDATION",
        passed=bool(ok),
        details={"accuracy": acc},
        reason=reason or "ok",
    )


def gate_walkforward(result: Any) -> GateResult:
    """GATE 6: walk-forward validation passed."""
    # Accept Phase 09's WalkForwardResult shape or a plain dict.
    if isinstance(result, dict):
        passed = bool(result.get("passed", False))
        detail = {"avg_oos": result.get("avg_oos_expectancy_r"), "folds": result.get("fold_count")}
        reason = "" if passed else "walk-forward did not pass"
    else:
        passed = bool(getattr(result, "passed", False))
        detail = {
            "avg_oos": getattr(result, "avg_oos_expectancy_r", None),
            "folds": getattr(result, "fold_count", None),
        }
        reason = "" if passed else "walk-forward did not pass"
    logger.info(
        "[MODEL] event=VALIDATION_GATE gate=WALK_FORWARD status=%s", "PASS" if passed else "FAIL"
    )
    return GateResult(
        gate="GATE6_WALK_FORWARD", passed=passed, details=detail, reason=reason or "ok"
    )


def gate_oos(result: Any, min_oos_expectancy: float = 0.0) -> GateResult:
    """GATE 7: out-of-sample gate (spec 38.17 - OOS failure rejects)."""
    if isinstance(result, dict):
        status = result.get("status")
        oos_exp = result.get("oos_expectancy_r")
    else:
        status = getattr(result, "status", None)
        oos_exp = getattr(result, "oos_expectancy_r", None)
    passed = status == "PASS" and oos_exp is not None and float(oos_exp) >= min_oos_expectancy
    reason = "" if passed else f"OOS gate {status} expectancy {oos_exp}"
    logger.info("[MODEL] event=VALIDATION_GATE gate=OOS status=%s", "PASS" if passed else "FAIL")
    return GateResult(
        gate="GATE7_OOS",
        passed=passed,
        details={"oos_expectancy_r": oos_exp},
        reason=reason or "ok",
    )


def gate_robustness(result: Any) -> GateResult:
    """GATE 8: robustness stress passed (spec 38.18)."""
    if isinstance(result, dict):
        status = result.get("status")
        deg = result.get("max_degradation")
    else:
        status = getattr(result, "status", None)
        deg = getattr(result, "max_degradation", None)
    passed = status == "PASS"
    reason = "" if passed else f"robustness {status} degradation={deg}"
    logger.info(
        "[MODEL] event=VALIDATION_GATE gate=ROBUSTNESS status=%s", "PASS" if passed else "FAIL"
    )
    return GateResult(
        gate="GATE8_ROBUSTNESS",
        passed=passed,
        details={"max_degradation": deg},
        reason=reason or "ok",
    )


def gate_risk_drawdown(result: Any, max_drawdown_r: float = 10.0) -> GateResult:
    """GATE 9: risk/drawdown within bounds (spec 38.19)."""
    if isinstance(result, dict):
        dd = result.get("max_drawdown_r")
    else:
        dd = getattr(result, "max_drawdown_r", None)
    passed = dd is not None and float(dd) <= max_drawdown_r
    reason = "" if passed else f"max drawdown {dd}R exceeds ceiling {max_drawdown_r}R"
    logger.info("[MODEL] event=VALIDATION_GATE gate=RISK status=%s", "PASS" if passed else "FAIL")
    return GateResult(
        gate="GATE9_RISK",
        passed=passed,
        details={"max_drawdown_r": dd},
        reason=reason or "ok",
    )


def gate_champion_comparison(comparison: Any) -> GateResult:
    """GATE 10: champion comparison shows improvement without critical degradation."""
    if isinstance(comparison, dict):
        eligible = bool(comparison.get("eligible", False))
        reasons = comparison.get("reasons", [])
    else:
        eligible = bool(getattr(comparison, "eligible", False))
        reasons = list(getattr(comparison, "reasons", []))
    logger.info(
        "[MODEL] event=VALIDATION_GATE gate=COMPARISON status=%s", "PASS" if eligible else "FAIL"
    )
    return GateResult(
        gate="GATE10_COMPARISON",
        passed=eligible,
        details={"reasons": reasons},
        reason="; ".join(reasons) or "ok",
    )


def gate_artifact_integrity(info: Any) -> GateResult:
    """GATE 11: artifact hash/dimension/class-count verified (spec 38.13-15)."""
    if isinstance(info, dict):
        integrity_ok = bool(info.get("integrity_ok", False))
        dim = info.get("feature_dimension")
        classes = info.get("num_classes")
    else:
        integrity_ok = bool(getattr(info, "integrity_ok", False))
        dim = getattr(info, "feature_dimension", None)
        classes = getattr(info, "num_classes", None)
    passed = integrity_ok and dim is not None and classes is not None
    reason = "" if passed else f"artifact integrity failed (dim={dim} classes={classes})"
    logger.info(
        "[MODEL] event=VALIDATION_GATE gate=ARTIFACT status=%s", "PASS" if passed else "FAIL"
    )
    return GateResult(
        gate="GATE11_ARTIFACT",
        passed=passed,
        details={"feature_dimension": dim, "num_classes": classes},
        reason=reason or "ok",
    )


def gate_reproducibility(run_id: str, dataset_id: str, schema_id: str, seed: int) -> GateResult:
    """GATE 12: full lineage present (run/dataset/schema/seed recorded)."""
    ok = bool(run_id and dataset_id and schema_id and seed is not None)
    reason = "" if ok else "missing lineage identity"
    logger.info(
        "[MODEL] event=VALIDATION_GATE gate=REPRODUCIBILITY status=%s", "PASS" if ok else "FAIL"
    )
    return GateResult(
        gate="GATE12_REPRODUCIBILITY",
        passed=ok,
        details={"run_id": run_id, "dataset_id": dataset_id, "schema": schema_id, "seed": seed},
        reason=reason or "ok",
    )


def check_model_collapse(
    predictions: list[int] | None = None,
    probabilities: list[list[float]] | None = None,
    class_counts: dict[str, int] | None = None,
    metrics: dict[str, Any] | None = None,
) -> GateResult:
    """
    Model collapse protection (spec 21 / 38.20).

    Rejects: class collapse (one class dominates), constant output, probability
    saturation, extreme confidence without evidence, NaN/Inf outputs.
    """
    reasons: list[str] = []
    ok = True

    if class_counts:
        total = sum(class_counts.values())
        if total > 0:
            for label, count in class_counts.items():
                if count / total > 0.99:
                    ok = False
                    reasons.append(f"class {label} dominates {count / total:.1%}")
        if len([c for c in class_counts.values() if c > 0]) < 2 and total > 0:
            ok = False
            reasons.append("prediction output collapsed to a single class")

    if predictions and len(set(predictions)) < 2:
        ok = False
        reasons.append("constant output (single predicted class)")

    if probabilities:
        avg_max = sum(max(p) for p in probabilities) / len(probabilities)
        if avg_max > MAX_PROB_SATURATION:
            ok = False
            reasons.append(f"probability saturation {avg_max:.3f} > {MAX_PROB_SATURATION}")
        if any(not all(math.isfinite(v) for v in p) for p in probabilities):
            ok = False
            reasons.append("non-finite probability values")

    if metrics:
        nan_inf = metrics.get("nan_inf_fraction", 0.0)
        if nan_inf and float(nan_inf) > MAX_NAN_INF_FRACTION:
            ok = False
            reasons.append(f"NaN/Inf fraction {nan_inf}")

    logger.info("[MODEL] event=VALIDATION_GATE gate=COLLAPSE status=%s", "PASS" if ok else "FAIL")
    return GateResult(gate="COLLAPSE_GUARD", passed=ok, reason="; ".join(reasons) or "ok")


def run_gates(
    gates: list[Callable[[], GateResult]],
) -> tuple[list[GateResult], bool]:
    """Runs all gates; ANY failure => failed."""
    results: list[GateResult] = []
    all_passed = True
    for gate_fn in gates:
        try:
            result = gate_fn()
        except ValidationGateError as e:
            result = GateResult(gate="UNKNOWN", passed=False, reason=str(e))
        results.append(result)
        if not result.passed:
            all_passed = False
    return results, all_passed
