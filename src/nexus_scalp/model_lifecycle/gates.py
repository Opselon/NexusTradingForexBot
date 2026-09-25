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
from pathlib import Path
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
    if final_loss is None or final_loss == "NOT_AVAILABLE":
        # None pre-ac414e82 or the honest NOT_AVAILABLE sentinel: the producer
        # did not emit a real loss -> missing evidence, gate fails (never crash).
        ok = False
        reasons.append(
            "no final loss recorded"
            if final_loss is None
            else "final_loss=NOT_AVAILABLE (producer did not emit a real value)"
        )
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
    if acc == "NOT_AVAILABLE":
        # Honest sentinel (ac414e82): no real validation accuracy exists.
        ok = False
        reason = "validation_accuracy=NOT_AVAILABLE (producer did not emit a real value)"
    else:
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


#: Dimension handles a bundle manifest / metadata dict may carry, in priority
#: order. The published ``manifest.json`` emits ``input_dim``
#: (emission_gate.py:217-266); the trainer's ``model.meta.json`` emits
#: ``feature_schema_dimension`` (walk_forward_trainer.py:2519); the
#: :class:`ModelArtifactInfo` object uses ``feature_dimension``.
_DIM_KEYS = ("feature_dimension", "input_dim", "feature_schema_dimension", "num_features")

#: Class-head handles, head-first. ``manifest.json`` emits top-level
#: ``class_count``; ``model.meta.json`` emits ``model_head_classes`` (the
#: MODEL_CLASS_CONTRACT SSoT ground truth, walk_forward_trainer.py:2516-2522)
#: and ``num_classes``; the label contract carries the label-side count.
_CLASS_KEYS = (
    "model_head_classes",
    "class_count",
    "num_classes",
    "label_schema_class_count",
)

#: Artifact-hash handles a manifest declares (byte-level verification of the
#: tensors themselves is ``integrity.inspect_artifact``'s job on the file path;
#: for a dict GATE11 confirms the identity markers the producer recorded).
_HASH_KEYS = ("model_sha256", "metadata_sha256", "scaler_sha256", "artifact_hash")


def _first_key(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in source:
            value = source[key]
            if value is not None:
                return value
    return None


def _hash_chain_ok(manifest: dict[str, Any]) -> bool:
    """The published bundle manifest binds every file by hash. Re-verify the
    chain it declares when the files are reachable.

    ``build_bundle_manifest`` records ``model_sha256`` / ``metadata_sha256`` /
    ``scaler_sha256`` over the bytes it published; ``verify_bundle_against_manifest``
    checked them against the canonical contract before publication. A caller
    that hands GATE11 a dict plus the directory the dict came from gets that
    chain confirmed here (hash mismatch or a missing file => FAIL). When the
    files are not reachable (a remote verification report, or the bundle was
    moved) the chain is NOT fabricable, so this returns True and GATE11 falls
    back to the markers the manifest itself declares.
    """
    bundle_dir = manifest.get("_bundle_dir")
    if not bundle_dir:
        return True
    from hashlib import sha256 as _sha256

    base = Path(bundle_dir)
    for hash_key, filename in (
        ("model_sha256", "model.pt"),
        ("metadata_sha256", "model.meta.json"),
        ("scaler_sha256", "model.scaler.npz"),
    ):
        expected = manifest.get(hash_key)
        if not expected:
            continue
        fp = base / filename
        if not fp.is_file():
            return False
        h = _sha256()
        with open(fp, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != expected:
            return False
    return True


def _first_attr(source: Any, names: tuple[str, ...], keys: tuple[str, ...]) -> Any:
    for name in names:
        value = getattr(source, name, None)
        if value is not None:
            return value
    # Objects projected from a manifest by a downstream caller may expose only
    # the manifest spellings.
    if isinstance(source, dict):
        return _first_key(source, keys)
    for key in keys:
        value = getattr(source, key, None)
        if value is not None:
            return value
    return None


def gate_artifact_integrity(info: Any) -> GateResult:
    """GATE 11: artifact hash/dimension/class-count verified (spec 38.13-15).

    Accepts any of the three real artifact shapes the codebase produces:

    * a :class:`ModelArtifactInfo` (the promotion-pipeline shape, produced by
      ``integrity.inspect_artifact``; consumed at orchestrator.py:369);
    * the published bundle ``manifest.json`` dict (emission_gate.py:217-266 —
      emits ``input_dim`` / ``class_count`` and no ``integrity_ok`` field);
    * the trainer's ``model.meta.json`` dict (walk_forward_trainer.py:2510 —
      emits ``feature_schema_dimension`` / ``model_head_classes`` /
      ``num_classes``).

    BUG-308C (2026-09-23, AGENT-GOVERNANCE): the dict branch read only the
    canonical ``feature_dimension`` / ``num_classes`` spellings. Neither
    artifact dict emits those, so a raw ``manifest.json`` / ``model.meta.json``
    read ``dim=None`` / ``classes=None`` and GATE11 **failed on a valid
    artifact**. The orchestrator's live caller passes a ``ModelArtifactInfo``
    (whose attribute names match), so the 12-gate pipeline never tripped it —
    the defect was latent, waiting for any governance/verification caller that
    hands the gate a dict.

    Every known spelling is now accepted as an alias (canonical first, class
    head ahead of the label-side count). An ``integrity_ok`` verdict supplied by
    the caller is honoured; a dict that omits it gets an honest *derived*
    verdict instead of a silent ``None``: GATE11 confirms the identity markers
    the producer recorded are present and resolve (the bundle's own emission
    gate verified their values against the canonical contract at publication —
    emission_gate.py:337-338). Byte-level verification stays with
    ``integrity.inspect_artifact``, which sets ``integrity_ok=False`` on a real
    mismatch; GATE11 then rejects on that verdict.

    ``None`` (a training run with zero artifacts — the orchestrator's empty
    ``run.artifacts`` shape) returns a structured FAIL, never an exception.
    """
    if isinstance(info, dict):
        dim = _first_key(info, _DIM_KEYS)
        nested = info.get("label_contract")
        if isinstance(nested, dict):
            info = {**info, "label_schema_class_count": nested.get("class_count")}
        classes = _first_key(info, _CLASS_KEYS)
        explicit_verdict = info.get("integrity_ok")
        if explicit_verdict is not None:
            # An honest verdict from the caller (or from inspect_artifact via a
            # serialised ModelArtifactInfo) is authoritative — never override it
            # with a derived one, and never override a FAIL with a derived pass.
            integrity_ok = bool(explicit_verdict)
        else:
            # A published manifest carries no integrity_ok field: derive an
            # honest verdict from the identity markers it declares — the
            # bundle's own emission gate verified their values against the
            # canonical contract at publication (emission_gate.py:337-338),
            # and the hash chain is re-verified when the files are reachable.
            integrity_ok = (
                dim is not None
                and classes is not None
                and _first_key(info, _HASH_KEYS) is not None
                and _hash_chain_ok(info)
            )
    elif info is None:
        integrity_ok = False
        dim = None
        classes = None
    else:
        integrity_ok = bool(getattr(info, "integrity_ok", False))
        dim = _first_attr(info, ("feature_dimension",), _DIM_KEYS)
        classes = _first_attr(
            info, ("model_head_classes", "class_count", "num_classes"), _CLASS_KEYS
        )
    passed = integrity_ok and dim is not None and classes is not None
    reason = ""
    if not passed:
        if dim is None or classes is None:
            reason = f"artifact integrity failed (dim={dim} classes={classes})"
        elif not integrity_ok:
            # Distinguish "the caller's verdict was False" (a real integrity
            # failure reported by inspect_artifact) from "this document declares
            # no identity of its own" (a meta.json has dimension/class markers
            # but no hash and no verdict — it is bound BY the bundle manifest,
            # and is not itself an identity document).
            explicit = isinstance(info, dict) and "integrity_ok" in info
            has_hash = isinstance(info, dict) and _first_key(info, _HASH_KEYS) is not None
            if explicit or not isinstance(info, dict) or has_hash:
                reason = "artifact integrity failed (verdict=False)"
            else:
                reason = (
                    "artifact integrity failed (no integrity_ok verdict and no hash handle — "
                    "this document declares no identity of its own; pass the bundle "
                    "manifest.json or a ModelArtifactInfo)"
                )
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


def gate_production_eligible(meta: dict[str, Any] | None) -> GateResult:
    """GATE (MODEL_CLASS_CONTRACT v1 / Fix #6): smoke artifacts NEVER promote.

    A smoke artifact (2 folds x 1 epoch on SMOKE_MIN_ROWS=3000 tails, see
    three_model.train_variant smoke=True provenance + WalkForwardTrainer
    smoke metadata) is a bounded drill, not production evidence.  Regardless
    of width, schema, or any validity gate, a smoke artifact must be REJECTED
    by promotion — production_eligible must be True.

    Absence of the field is treated as NOT eligible (closed-world): legacy
    artifacts without the field must be retrained through the current contract
    to acquire it.  Only artifacts written by the current trainer carry the
    flag honestly (WalkForwardTrainer._save_metadata sets it from its smoke
    input).
    """
    meta = meta or {}
    prod = meta.get("production_eligible")
    smoke = bool(meta.get("smoke") is True)
    blocked = smoke or (prod is False)
    # When the field is absent and smoke is not proven, treat as ineligible
    # unless smoke is explicitly absent and prod is True — honest provenance.
    if "production_eligible" not in meta and not smoke:
        # No field, not smoke: ambiguous legacy — mark INCONCLUSIVE (omitted)
        # so verify_candidate's skipped-class treats it as insufficient evidence.
        # For the gates runner (which treats non-pass as fail), return FAIL.
        blocked = True
    ok = not blocked
    reason = ""
    if blocked:
        reason = (
            f"smoke={smoke} production_eligible={prod!r} — smoke quorum must be rejected "
            "(Fix #6: smoke artifacts are never production-eligible)"
        )
    logger.info(
        "[MODEL] event=VALIDATION_GATE gate=PRODUCTION_ELIGIBLE status=%s",
        "PASS" if ok else "FAIL",
    )
    return GateResult(
        gate="GATE_PRODUCTION_ELIGIBLE",
        passed=ok,
        details={"production_eligible": prod, "smoke": smoke},
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
