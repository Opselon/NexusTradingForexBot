"""Validation Factory (PHASE 13, spec 23 / 24 / 25 / 26 / 27 / 28 / 29).

Candidate models must pass:

    1. label integrity            6. regime results
    2. schema compatibility        7. class collapse detection
    3. OOS behavior                8. calibration
    4. robustness                  9. news-aware vs no-news comparison
    5. risk/drawdown              10. reproducibility

Reuses Phase 10 gate concepts; extends with class-collapse + calibration +
news ablation. Candidates that fail are REJECTED, never CHALLENGER.

P0-4 EVALUATION INTEGRITY (deep audit Section K, Agent-2):
The historical "OOS" verdicts were computed over the ENTIRE dataset frame
(train+val+test), so CHALLENGER_ELIGIBLE rested on metrics that included
the rows the candidate trained on (three_model trains on train+val ~85%
of the frame). Contracts now enforced HERE, at the gate:

    * OOS population = rows with ``_split in {val, test}`` ONLY. Train and
      purged rows are excluded by construction. No provable val/test
      population => REJECTED (fail closed, never widen — BUG-245 class).
    * the protected TEST block is consumable ONCE per candidate
      (evaluate_test_block_once + the test_block_single_shot gate);
      reuse => TestBlockReuseError / REJECTED, recorded in a ledger.
    * ``force`` keeps its documented insufficient-evidence/calibration
      bypass semantics but can NEVER bypass split integrity (no path
      back to train-row contamination).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
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
#: Minimum TOTAL samples for a validation verdict (spec 18: tiny samples are
#: never evidence of superiority — the verdict stays REJECTED/INCONCLUSIVE).
MIN_EVIDENCE_SAMPLES: int = 100
#: Maximum acceptable Expected Calibration Error (spec 14). A model whose
#: confidence has no empirical meaning cannot become CHALLENGER.
ECE_FLOOR: float = 0.15

#: P0-4: splits that MAY enter an out-of-sample validation verdict.
#: Train rows (the candidate's own fitting data) and purged boundary rows
#: (BUG-244: belong to NO scored block) are excluded BY CONSTRUCTION.
#: ``OOS_SCOPES`` is the legacy name used by the benchmark lane's own
#: scope mask (kept as an alias so both callers share ONE contract).
OOS_SPLITS: frozenset[str] = frozenset({"val", "test"})
OOS_SCOPES: frozenset[str] = OOS_SPLITS

#: P0-4: persisted consumption ledger for the protected test block
#: (default location under the model-generation artifact root).
DEFAULT_TEST_BLOCK_LEDGER = Path("artifacts/model_generation/test_block_usage.json")


class TestBlockReuseError(RuntimeError):
    """The protected test block was already consumed by this candidate."""


def _dataset_fingerprint(dataset_frame: Any) -> str:
    """Deterministic identity of the dataset frame the test block belongs to
    (labels + timestamps when present + split markers). Any content change
    re-identifies; the fingerprint is what the ledger keys consumption on."""
    try:
        cols = [
            c
            for c in ("sample_id", "timestamp", "label", "_split")
            if dataset_frame is not None and c in dataset_frame.columns
        ]
        if dataset_frame is None or not cols:
            return "unknown"
        proj = dataset_frame.select(cols).to_dict(as_series=False)
        canonical = json.dumps(proj, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "unknown"


def evaluate_test_block_once(
    model_id: str,
    dataset_frame: Any,
    *,
    ledger_path: Path | str | None = None,
    artifact_store: Any = None,
    write_store: bool = True,
) -> tuple[bool, dict[str, Any]]:
    """Single-shot consumption of the protected TEST block (P0-4).

    The test block must answer ONE selection question per candidate.
    Repeated candidate-selection runs previously re-scored the same test
    rows, silently turning the protected block into a tuning set. This
    gate records each candidate's consumption in a persisted ledger and:

      * allows the FIRST consumption (record: status=CONSUMED);
      * raises TestBlockReuseError on reuse by the same model_id
        (the recorded status becomes REUSED_REJECTED — audit-visible);
      * keeps the ledger JSON co-located with the candidate artifacts
        (model_generation root) so the evidence survives the process.

    Returns (allowed, record). Callers that persist validation results via
    an ArtifactStore should pass ``artifact_store`` so the per-candidate
    model manifest carries the same evidence.
    """
    path = Path(ledger_path) if ledger_path else DEFAULT_TEST_BLOCK_LEDGER
    dataset_fp = _dataset_fingerprint(dataset_frame)
    now = datetime.now(UTC).isoformat()

    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    entry = data.get(model_id) or {}

    n_test_rows = 0
    try:
        if dataset_frame is not None and "_split" in dataset_frame.columns:
            n_test_rows = int((dataset_frame["_split"].to_numpy() == "test").sum())
    except Exception:
        n_test_rows = 0

    attempts = int(entry.get("attempts", 0) or 0) + 1
    if entry.get("status") == "CONSUMED":
        record = {
            "model_id": model_id,
            "status": "REUSED_REJECTED",
            "attempts": attempts,
            "dataset_fingerprint": dataset_fp,
            "first_consumed_at": entry.get("first_consumed_at", ""),
            "rejected_at": now,
            "n_test_rows": n_test_rows,
            "reason": "TEST_BLOCK_REUSE: protected test block already consumed",
        }
        data[model_id] = record
        # The ledger is the SAFETY MECHANISM — reuse detection only works
        # when consumption is durable, so it is ALWAYS persisted (write_store
        # only controls the optional artifact-store evidence stamp below).
        _write_ledger(path, data)
        logger.error(
            "[EVAL-GOV] event=TEST_BLOCK_REUSE model_id=%s attempts=%d",
            model_id,
            attempts,
        )
        raise TestBlockReuseError(
            f"TEST_BLOCK_REUSE: {model_id} already consumed the protected test "
            f"block (attempts={attempts}); second verdict refused"
        )

    record = {
        "model_id": model_id,
        "status": "CONSUMED",
        "attempts": attempts,
        "dataset_fingerprint": dataset_fp,
        "first_consumed_at": now,
        "n_test_rows": n_test_rows,
    }
    data[model_id] = record
    # Always durable: a consumed test block MUST be remembered, or the
    # single-shot protection does not exist. write_store governs only the
    # optional per-artifact evidence stamp below.
    _write_ledger(path, data)
    logger.info(
        "[EVAL-GOV] event=TEST_BLOCK_CONSUMED model_id=%s dataset=%s n_test_rows=%d",
        model_id,
        dataset_fp,
        n_test_rows,
    )
    if artifact_store is not None and write_store:
        try:
            store_save_test_block_evidence(artifact_store, model_id, record)
        except Exception as exc:  # evidence stamping must not break the gate
            logger.warning("[EVAL-GOV] test-block evidence stamp failed: %s", exc)
    return True, record


def _write_ledger(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def store_save_test_block_evidence(
    artifact_store: Any, model_id: str, record: dict[str, Any]
) -> None:
    """Stamps the consumption record into the candidate's persisted manifest
    (best effort): the artifact becomes self-describing about whether its
    validation verdict consumed the protected block."""
    manifest = artifact_store.read_model_manifest(model_id) or {}
    manifest["test_block_usage"] = record
    artifact_store.write_json(artifact_store.model_manifest_path(model_id), manifest)


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


def _balanced_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Macro-averaged per-class recall (balanced accuracy) — collapses to
    0.5 for a random 2-class model, ~0.333 for a 3-class no-info model."""
    n_classes = max(int(y_true.max()) + 1, int(y_pred.max()) + 1, 2)
    recalls = []
    for c in range(n_classes):
        mask = y_true == c
        if mask.sum() == 0:
            continue
        recalls.append(float((y_pred[mask] == c).mean()))
    return float(np.mean(recalls)) if recalls else 0.0


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


def scope_oos_frame(dataset_frame: Any) -> tuple[Any, dict[str, Any]]:
    """P0-4: restrict a dataset frame to the provable OOS population.

    Returns (oos_frame, audit) where audit describes exactly what was
    excluded. Raises ValueError (fail closed) when no provable val/test
    population exists — the verdict then has no honest evaluation set
    (BUG-245 precedent: never widen the population to keep going).
    """
    audit: dict[str, Any] = {
        "total_rows": 0,
        "train_rows_excluded": 0,
        "purged_rows_excluded": 0,
        "evaluated_splits": [],
    }
    if dataset_frame is None:
        # Labels-only gate exercise (collapse / calibration / min-evidence unit
        # tests pass frame=None with explicit label vectors): no DATASET
        # population is claimed, so there is nothing to scope or hide. The
        # caller's label vector IS the evaluation set; the OOS floors still
        # apply. A None frame can never smuggle train rows because there are
        # no rows at all.
        audit["reason"] = "LABELS_ONLY_NO_FRAME"
        return None, audit
    try:
        total = int(dataset_frame.height)
    except Exception as exc:
        raise ValueError(
            "OOS_SPLIT_INTEGRITY: dataset frame unreadable — no OOS population"
        ) from exc
    audit["total_rows"] = total
    if "_split" not in dataset_frame.columns:
        # A frame that cannot prove its split scope is the contamination-risk
        # class this gate targets. Tiny hand-built gate-exercise fixtures
        # (n < MIN_EVIDENCE_SAMPLES) can never become CHALLENGER_ELIGIBLE
        # anyway: route them to the honest INSUFFICIENT_EVIDENCE rejection
        # instead of raising past every gate, and keep the hard failure for
        # real-scale frames where a hidden train block would matter.
        if total < MIN_EVIDENCE_SAMPLES:
            audit["reason"] = "NO_SPLIT_MARKERS_INSUFFICIENT"
            return None, audit
        audit["reason"] = "NO_SPLIT_MARKERS"
        raise ValueError(
            "OOS_SPLIT_INTEGRITY: dataset frame carries no _split markers — "
            "the OOS population cannot be proven (fail closed, no widening)"
        )
    split = dataset_frame["_split"].to_numpy()
    oos_mask = np.isin(split, sorted(OOS_SPLITS))
    audit["train_rows_excluded"] = int((split == "train").sum())
    audit["purged_rows_excluded"] = int((split == "purged").sum())
    audit["evaluated_splits"] = sorted({str(s) for s in split[oos_mask]})
    if not oos_mask.any():
        audit["reason"] = "NO_VAL_TEST_ROWS"
        raise ValueError(
            "OOS_SPLIT_INTEGRITY: frame contains no val/test rows — "
            "every candidate would be scored on train rows (fail closed)"
        )
    return dataset_frame.filter(__import__("polars").Series("_oos_mask", oos_mask)), audit


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
        artifact_store: Any = None,
        test_block_ledger: Path | str | None = None,
    ) -> ValidationResults:
        """Validates a candidate. ``force`` bypasses insufficient evidence.

        Returns ValidationResults with verdict REJECTED / CHALLENGER_ELIGIBLE.

        P0-4: the verdict is computed on the OOS population ONLY (rows with
        ``_split in {val, test}``). ``force`` can never bypass that scoping.
        When ``artifact_store``/``test_block_ledger`` are provided the
        protected test block is consumed single-shot per candidate.
        """
        gates: list[dict[str, Any]] = []

        # 0. OOS split integrity (P0-4) — BEFORE anything can pass.
        #    The evaluation population is val/test rows only; train rows can
        #    never enter the verdict and force cannot change that.
        try:
            oos_frame, split_audit = scope_oos_frame(dataset_frame)
            split_ok = True
            split_reason = (
                f"evaluated={'/'.join(split_audit['evaluated_splits'])} "
                f"train_excluded={split_audit['train_rows_excluded']} "
                f"purged_excluded={split_audit['purged_rows_excluded']}"
                if split_audit.get("evaluated_splits")
                else str(split_audit.get("reason", ""))
            )
        except ValueError as exc:
            oos_frame, split_audit, split_ok, split_reason = None, {}, False, str(exc)
        gates.append(
            {
                "gate": "oos_split_integrity",
                "passed": split_ok,
                "reason": split_reason,
            }
        )
        if not split_ok:
            # Fail closed with an honest REJECTED — no OOS verdict exists.
            return ValidationResults(
                model_id=model_id,
                experiment_id=experiment_id,
                gates=gates,
                verdict="REJECTED",
                passed=False,
                class_distribution={},
                overall={
                    "n": 0,
                    "oos_accuracy": 0.0,
                    "reason": "NO_OOS_POPULATION",
                    "split_audit": split_audit,
                },
            )

        # 0b. Protected test block single-shot consumption (P0-4) — enforced
        #     wherever artifacts persist (ledger path given or default).
        test_block_gate: dict[str, Any] | None = None
        if artifact_store is not None or test_block_ledger is not None:
            try:
                allowed, tb_record = evaluate_test_block_once(
                    model_id,
                    oos_frame,
                    ledger_path=test_block_ledger,
                    artifact_store=artifact_store,
                )
            except Exception as exc:  # corrupted ledger must not fabricate a PASS
                allowed, tb_record = False, {"status": "ERROR", "reason": str(exc)}
            test_block_gate = {
                "gate": "test_block_single_shot",
                "passed": allowed,
                "reason": tb_record.get("reason", f"status={tb_record.get('status')}"),
            }
            gates.append(test_block_gate)
            if not allowed:
                return ValidationResults(
                    model_id=model_id,
                    experiment_id=experiment_id,
                    gates=gates,
                    verdict="REJECTED",
                    passed=False,
                    class_distribution={},
                    overall={
                        "n": 0,
                        "oos_accuracy": 0.0,
                        "reason": "TEST_BLOCK_REUSE",
                        "test_block": tb_record,
                    },
                )

        # 1. label integrity (3-class contract) — on the OOS population
        if labels is None:
            labels = (
                oos_frame["label"].to_numpy().astype(np.int64)
                if oos_frame is not None
                else np.array([], dtype=np.int64)
            )
        elif oos_frame is None:
            pass  # labels-only gate exercise: the caller's vector IS the set
        # P0-4 hard rule (oos_frame proven): a caller-supplied label vector is
        # accepted ONLY when it already describes the OOS population. A
        # full-frame label vector (the historical contamination shape) is
        # REFUSED — never silently re-aligned, train rows never scored.
        elif labels is not None and len(labels) != len(oos_frame):
            if len(labels) == int(split_audit.get("total_rows", -1)):
                raise ValueError(
                    "OOS_SPLIT_INTEGRITY: label vector covers the FULL frame "
                    "(incl. train rows) while the verdict evaluates the OOS "
                    "population only — scope the labels/probabilities to the "
                    "val/test rows before calling validate (no silent widening)"
                )
            raise ValueError(
                "OOS_SPLIT_INTEGRITY: label/probability vector length "
                f"({len(labels)}) != OOS population ({len(oos_frame)} rows)"
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

        # 2. class collapse + minimum evidence (OOS population only)
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
        # MIN_EVIDENCE: a validation verdict on 5 samples is not evidence.
        if n < MIN_EVIDENCE_SAMPLES:
            return ValidationResults(
                model_id=model_id,
                experiment_id=experiment_id,
                gates=[
                    *gates,
                    {
                        "gate": "min_evidence",
                        "passed": False,
                        "reason": f"n={n} < {MIN_EVIDENCE_SAMPLES}",
                    },
                ],
                verdict="REJECTED",
                passed=False,
                class_distribution={str(k): int(v) for k, v in collapse["distribution"].items()},
                overall={
                    "n": n,
                    "oos_accuracy": 0.0,
                    "reason": "INSUFFICIENT_EVIDENCE",
                    "evaluated_splits": split_audit.get("evaluated_splits", []),
                    "train_rows_excluded": split_audit.get("train_rows_excluded", 0),
                    "purged_rows_excluded": split_audit.get("purged_rows_excluded", 0),
                    "total_rows": split_audit.get("total_rows", 0),
                    "rows_eval": n,
                    "rows_dropped": split_audit.get("total_rows", 0) - n,
                    "split_scope": {
                        "split_scoped": split_ok,
                        "scope": "val+test",
                        "rows_total": split_audit.get("total_rows", 0),
                        "rows_eval": n,
                        "rows_dropped": split_audit.get("total_rows", 0) - n,
                        "evaluated_splits": split_audit.get("evaluated_splits", []),
                        "train_rows_excluded": split_audit.get("train_rows_excluded", 0),
                        "purged_rows_excluded": split_audit.get("purged_rows_excluded", 0),
                    },
                },
            )

        # 3. OOS / regime results (always computed when regime col present)
        regime_results = evaluate_regime_performance(oos_frame)
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
            ece_value = calibration.get("ece", 1.0)
            ece_val = float(ece_value) if isinstance(ece_value, (int, float)) else float("inf")
            gates.append(
                {
                    "gate": "calibration_floor",
                    "passed": ece_val <= ECE_FLOOR or force,
                    "reason": f"ece={ece_val:.4f} floor={ECE_FLOOR}",
                }
            )

        # 5. OOS accuracy + macro-F1 floors (real behavior, not dummy).
        #    A 3-class no-information baseline is 1/3 macro-F1; the gates must
        #    demand evidence ABOVE it or the candidate is REJECTED (a
        #    NO_TRADE-dominated model gets high accuracy with ~0.33 macro-F1).
        oos_acc = 0.0
        oos_macro_f1 = 0.0
        oos_balanced_acc = 0.0
        if probabilities is not None and len(probabilities) == len(labels):
            preds = np.argmax(probabilities, axis=1)
            oos_acc = float(np.mean(preds == labels))
            cm = confusion_and_class_metrics(labels, preds)
            oos_macro_f1 = float(cm.get("macro_f1", 0.0))
            oos_balanced_acc = _balanced_accuracy(labels, preds)
        gates.append(
            {
                "gate": "oos_accuracy",
                "passed": oos_acc >= 0.30 or force,
                "reason": f"oos_acc={oos_acc:.4f} n={n}",
            }
        )
        gates.append(
            {
                "gate": "oos_macro_f1_floor",
                "passed": oos_macro_f1 > 0.34 or force,
                "reason": f"oos_macro_f1={oos_macro_f1:.4f} (no-info baseline 0.333)",
            }
        )
        gates.append(
            {
                "gate": "oos_balanced_accuracy_floor",
                "passed": oos_balanced_acc > 0.34 or force,
                "reason": f"oos_balanced_acc={oos_balanced_acc:.4f}",
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
            overall={
                "n": n,
                "oos_accuracy": round(oos_acc, 4),
                "oos_macro_f1": round(oos_macro_f1, 4),
                "oos_balanced_accuracy": round(oos_balanced_acc, 4),
                "evaluated_splits": split_audit.get("evaluated_splits", []),
                "train_rows_excluded": split_audit.get("train_rows_excluded", 0),
                "purged_rows_excluded": split_audit.get("purged_rows_excluded", 0),
                "total_rows": split_audit.get("total_rows", 0),
                "rows_eval": n,
                "rows_dropped": split_audit.get("total_rows", 0) - n,
                "split_scope": {
                    "split_scoped": split_ok,
                    "scope": "val+test",
                    "rows_total": split_audit.get("total_rows", 0),
                    "rows_eval": n,
                    "rows_dropped": split_audit.get("total_rows", 0) - n,
                    "evaluated_splits": split_audit.get("evaluated_splits", []),
                    "train_rows_excluded": split_audit.get("train_rows_excluded", 0),
                    "purged_rows_excluded": split_audit.get("purged_rows_excluded", 0),
                },
            },
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


# =============================================================================
# PER-CLASS METRICS + HEAD-TO-HEAD COMPARISON (PHASE 13B benchmark)
# =============================================================================


def confusion_and_class_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    num_classes: int = 3,
) -> dict[str, Any]:
    """Per-class precision / recall / F1 + confusion matrix + support.

    Detects class collapse beyond a single accuracy number (spec 12): a
    model predicting NO_TRADE 97% of the time has terrible recall on
    BUY/SELL even if accuracy is high.
    """
    n = len(y_true)
    if n == 0:
        return {"error": "empty"}
    cm = np.zeros((num_classes, num_classes), dtype=np.int64)
    for t, p in zip(y_true, y_pred, strict=False):
        if 0 <= t < num_classes and 0 <= p < num_classes:
            cm[t, p] += 1
    per_class: dict[str, Any] = {}
    for cidx in range(num_classes):
        tp = int(cm[cidx, cidx])
        fp = int(cm[:, cidx].sum()) - tp
        fn = int(cm[cidx, :].sum()) - tp
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per_class[str(cidx)] = {
            "support": int(cm[cidx, :].sum()),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
        }
    # macro F1 (class-balanced, resists NO_TRADE domination)
    macro_f1 = float(np.mean([v["f1"] for v in per_class.values()])) if per_class else 0.0
    return {
        "confusion_matrix": cm.tolist(),
        "per_class": per_class,
        "macro_f1": round(macro_f1, 4),
        "accuracy": round(float(np.mean(y_true == y_pred)), 4),
        "n": n,
    }


def head_to_head(
    legacy_results: dict[str, Any],
    new_results: dict[str, Any],
) -> dict[str, Any]:
    """Direct legacy-vs-new comparison table (spec 24)."""
    keys = sorted(
        set(legacy_results) | set(new_results),
        key=lambda k: list(legacy_results).index(k) if k in legacy_results else 999,
    )
    rows: list[dict[str, Any]] = []
    for k in keys:
        lv = legacy_results.get(k)
        nv = new_results.get(k)
        delta = None
        if isinstance(lv, (int, float)) and isinstance(nv, (int, float)):
            delta = round(nv - lv, 4)
        rows.append({"metric": k, "legacy": lv, "new": nv, "delta": delta})
    return {"rows": rows}
