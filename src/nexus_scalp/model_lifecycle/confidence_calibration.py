"""Confidence calibration module (P0 phase 3 — calibrated confidence before risk).

MISSION: risk sizing must never multiply money-at-risk by a RAW softmax
output. A raw softmax is not a calibrated probability.

What exists here:

* :func:`fit_platt_calibration` — logistic (Platt) calibration of the
  directional trained-class confidence onto realized trade outcomes
  (pure numpy/torch, no new dependencies — sklearn is NOT in this repo's
  dependency set).
* :func:`evaluate_calibration` — ECE (expected calibration error) and
  Brier score for a set of (probability, outcome) pairs.
* :class:`ConfidenceCalibrator` — runtime artifact wrapper with explicit
  provenance, a bounded monotone output, and FAIL-CLOSED semantics:
  an absent / invalid / too-small calibration is reported as
  ``NOT_CALIBRATED`` and the caller MUST fall back to safe flat sizing
  (never to raw-softmax scaling).

ANTI-OVERFITTING CONTRACT (mission phase 16): fitting happens on a
CALIBRATION slice only. Validation metrics must be computed on a DISJOINT
slice; the numbers here are evidence, not auto-tuned thresholds.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.confidence_calibration")

#: Minimum samples before a calibration may be trusted at all. Below this the
#: calibrator reports NOT_CALIBRATED (mission phase 16: INSUFFICIENT_DATA,
#: never OPTIMIZED).
MIN_CALIBRATION_SAMPLES: int = 30

#: Bounds of the calibrated risk multiplier that any consumer may derive from
#: calibrated confidence. Risk NEVER exceeds 1.0x base sizing from confidence:
#: confidence can only DE-RISK, never lever up (mission phase 3D).
MAX_CONFIDENCE_RISK_MULTIPLIER: float = 1.0
MIN_CONFIDENCE_RISK_MULTIPLIER: float = 0.25

#: Maximum ECE for the calibration to be reported USABLE. Above this the
#: calibrator still functions but consumers must treat it as degraded
#: (flat-size fallback is the safe choice).
MAX_USABLE_ECE: float = 0.20

CALIBRATION_SCHEMA_VERSION: str = "CONFIDENCE_CALIBRATION v1"


@dataclass(frozen=True)
class CalibrationProvenance:
    """Provenance block REQUIRED for every persisted calibration artifact.

    Mission phase 1E: a threshold/curve without provenance is not calibrated —
    it is decoration. Every field must be a real recorded value; empty strings
    are NOT_RECORDED-equivalent and make the artifact UNUSABLE.
    """

    model_version: str
    calibration_dataset_id: str
    #: sha256[:16] of the EXACT model artifact this calibration was fit for.
    #: Empty = NOT_RECORDED = the artifact must be treated as NOT_CALIBRATED
    #: (a calibration without model-identity binding is unusable — mission
    #: phase 7: a calibration must never survive a model replacement).
    artifact_fingerprint: str
    calibration_period_start: str  # ISO timestamps of the fitting slice
    calibration_period_end: str
    validation_dataset_id: str
    validation_period_start: str
    validation_period_end: str
    method: str  # e.g. "platt_logistic"
    created_at: str
    feature_schema_version: str
    sample_count: int
    validation_sample_count: int

    def is_complete(self) -> bool:
        return all(
            [
                bool(self.model_version),
                bool(self.calibration_dataset_id),
                bool(self.artifact_fingerprint),
                bool(self.calibration_period_start),
                bool(self.calibration_period_end),
                bool(self.validation_dataset_id),
                bool(self.validation_period_start),
                bool(self.validation_period_end),
                bool(self.method),
                bool(self.created_at),
                bool(self.feature_schema_version),
                self.sample_count >= MIN_CALIBRATION_SAMPLES,
                self.validation_sample_count >= MIN_CALIBRATION_SAMPLES,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CALIBRATION_SCHEMA_VERSION,
            "model_version": self.model_version,
            "calibration_dataset_id": self.calibration_dataset_id,
            "artifact_fingerprint": self.artifact_fingerprint,
            "calibration_period_start": self.calibration_period_start,
            "calibration_period_end": self.calibration_period_end,
            "validation_dataset_id": self.validation_dataset_id,
            "validation_period_start": self.validation_period_start,
            "validation_period_end": self.validation_period_end,
            "method": self.method,
            "created_at": self.created_at,
            "feature_schema_version": self.feature_schema_version,
            "sample_count": self.sample_count,
            "validation_sample_count": self.validation_sample_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationProvenance:
        return cls(
            model_version=str(data.get("model_version", "")),
            calibration_dataset_id=str(data.get("calibration_dataset_id", "")),
            artifact_fingerprint=str(data.get("artifact_fingerprint", "")),
            calibration_period_start=str(data.get("calibration_period_start", "")),
            calibration_period_end=str(data.get("calibration_period_end", "")),
            validation_dataset_id=str(data.get("validation_dataset_id", "")),
            validation_period_start=str(data.get("validation_period_start", "")),
            validation_period_end=str(data.get("validation_period_end", "")),
            method=str(data.get("method", "")),
            created_at=str(data.get("created_at", "")),
            feature_schema_version=str(data.get("feature_schema_version", "")),
            sample_count=int(data.get("sample_count", 0)),
            validation_sample_count=int(data.get("validation_sample_count", 0)),
        )


def fit_platt_calibration(
    confidences: list[float],
    outcomes: list[int],
) -> dict[str, float]:
    """Fits a Platt (logistic) mapping p_cal = sigmoid(a * conf + b).

    Pure numpy gradient descent (no sklearn in the dependency set).
    Deterministic: fixed iterations, L2-regularized a to avoid runaway
    slopes on small samples.

    Returns {"a": slope, "b": intercept}. Raises ValueError on malformed
    input — the caller decides the fail-closed behavior.
    """
    import numpy as np

    if len(confidences) != len(outcomes):
        raise ValueError("confidences/outcomes length mismatch")
    if len(confidences) == 0:
        raise ValueError("empty calibration sample")

    x = np.asarray(confidences, dtype=np.float64)
    y = np.asarray(outcomes, dtype=np.float64)
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    if len(x) == 0:
        raise ValueError("no finite calibration samples")
    if not np.isin(y, (0.0, 1.0)).all():
        raise ValueError("outcomes must be binary 0/1 (win/loss)")
    x = np.clip(x, 0.0, 1.0)

    # Deterministic plain gradient descent (Newton on 2 params is also fine,
    # but GD with a fixed schedule is simpler to reason about + reproducible).
    a, b = 0.0, 0.0
    lr = 0.5
    l2_a = 1e-3  # tiny slope regularizer
    n = len(x)
    prev_ll = -math.inf
    for _ in range(5000):
        z = a * x + b
        p = 1.0 / (1.0 + np.exp(-z))
        # negative log-likelihood gradient
        ga = float(np.sum((p - y) * x)) / n + l2_a * a
        gb = float(np.sum(p - y)) / n
        a -= lr * ga
        b -= lr * gb
        ll = -float(np.sum(y * np.log(p + 1e-12) + (1 - y) * np.log(1 - p + 1e-12))) / n
        if abs(prev_ll - ll) < 1e-10:
            break
        prev_ll = ll
    if not (math.isfinite(a) and math.isfinite(b)):
        raise ValueError("platt fit did not converge to finite parameters")
    return {"a": round(a, 8), "b": round(b, 8)}


def evaluate_calibration(
    confidences: list[float],
    outcomes: list[int],
    n_bins: int = 10,
) -> dict[str, Any]:
    """ECE + Brier for (confidence, binary outcome) pairs.

    Works on ANY confidence-like score: raw softmax max-prob or the Platt-
    calibrated output — the consumer compares both to prove improvement.
    """
    import numpy as np

    if len(confidences) != len(outcomes) or len(confidences) == 0:
        return {"ece": 1.0, "brier": 1.0, "bins": [], "n": 0}
    x = np.asarray(confidences, dtype=np.float64)
    y = np.asarray(outcomes, dtype=np.float64)
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    if len(x) == 0:
        return {"ece": 1.0, "brier": 1.0, "bins": [], "n": 0}
    x = np.clip(x, 0.0, 1.0)
    n = len(x)
    ece = 0.0
    bins: list[dict[str, Any]] = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        mask = (x >= lo) & (x < hi) if b < n_bins - 1 else (x >= lo) & (x <= hi)
        nb = int(mask.sum())
        if nb == 0:
            continue
        mean_conf = float(x[mask].mean())
        frac_win = float(y[mask].mean())
        ece += (nb / n) * abs(mean_conf - frac_win)
        bins.append(
            {
                "bin": f"{lo:.1f}-{hi:.1f}",
                "n": nb,
                "mean_conf": round(mean_conf, 4),
                "win_rate": round(frac_win, 4),
            }
        )
    brier = float(np.mean((x - y) ** 2))
    return {
        "ece": round(ece, 4),
        "brier": round(brier, 4),
        "bins": bins,
        "n": int(n),
    }


class ConfidenceCalibrator:
    """Runtime calibrated-confidence provider (FAIL-CLOSED).

    State:
      CALIBRATED      — provenance complete, params finite, ECE within budget
      DEGRADED        — params usable but validation ECE above budget
      NOT_CALIBRATED  — no/invalid artifact: caller MUST flat-size

    Output of :meth:`calibrate` is ALWAYS in [0, 1] and clamped; it never
    manufactures probability from nothing (invalid input -> NOT_CALIBRATED).
    """

    def __init__(
        self,
        params: dict[str, float] | None = None,
        provenance: CalibrationProvenance | None = None,
        validation_metrics: dict[str, Any] | None = None,
    ) -> None:
        self._params = params
        self._provenance = provenance
        self._validation_metrics = validation_metrics or {}

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_artifact(cls, path: str | Path) -> ConfidenceCalibrator:
        """Loads a persisted calibration artifact; corrupt/missing -> NOT_CALIBRATED."""
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            params = data.get("params")
            prov = CalibrationProvenance.from_dict(data.get("provenance", {}))
            metrics = data.get("validation_metrics", {})
            return cls(
                params={k: float(v) for k, v in params.items()} if params else None,
                provenance=prov,
                validation_metrics=metrics,
            )
        except Exception as exc:
            logger.warning("[CALIBRATION] artifact load failed -> NOT_CALIBRATED", error=str(exc))
            return cls()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        if self._params is None or self._provenance is None:
            return "NOT_CALIBRATED"
        if not self._provenance.is_complete():
            return "NOT_CALIBRATED"
        a = self._params.get("a")
        b = self._params.get("b")
        if a is None or b is None or not (math.isfinite(a) and math.isfinite(b)):
            return "NOT_CALIBRATED"
        ece = float(self._validation_metrics.get("ece", 1.0))
        if ece > MAX_USABLE_ECE:
            return "DEGRADED"
        return "CALIBRATED"

    def identity(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "method": self._provenance.method if self._provenance else "none",
            "model_version": self._provenance.model_version if self._provenance else "",
            "ece": self._validation_metrics.get("ece"),
            "brier": self._validation_metrics.get("brier"),
            "sample_count": self._provenance.sample_count if self._provenance else 0,
        }

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    def calibrate(self, confidence: float) -> tuple[float, str]:
        """Maps a raw directional confidence to its calibrated probability.

        Returns (calibrated_value, state). NOT_CALIBRATED returns the input
        UNCHANGED plus its state — the caller must treat that as flat-size
        evidence, never as a probability claim.
        """
        if self.state == "NOT_CALIBRATED":
            return float(confidence), "NOT_CALIBRATED"
        assert self._params is not None
        a, b = self._params["a"], self._params["b"]
        c = float(confidence)
        if not math.isfinite(c) or c < 0.0 or c > 1.0:
            return 0.0, self.state
        z = a * c + b
        # numerically stable logistic
        if z >= 0:
            out = 1.0 / (1.0 + math.exp(-z))
        else:
            e = math.exp(z)
            out = e / (1.0 + e)
        return min(1.0, max(0.0, out)), self.state


def confidence_to_risk_multiplier(
    calibrated_confidence: float,
    state: str,
) -> float:
    """Bounded risk multiplier from CALIBRATED confidence (mission phase 3D).

    Contract:
      * state != CALIBRATED  -> 1.0 exactly (flat sizing; never scales UP on
        unproven evidence, never scales DOWN on broken evidence either).
      * calibrated input is clipped to [0,1]; the multiplier is linear in
        [MIN_CONFIDENCE_RISK_MULTIPLIER, MAX_CONFIDENCE_RISK_MULTIPLIER].
      * NEVER exceeds 1.0 — confidence can de-risk, not lever.
    """
    if state != "CALIBRATED":
        return 1.0
    c = min(1.0, max(0.0, float(calibrated_confidence)))
    span = MAX_CONFIDENCE_RISK_MULTIPLIER - MIN_CONFIDENCE_RISK_MULTIPLIER
    return MIN_CONFIDENCE_RISK_MULTIPLIER + span * c


def persist_calibration_artifact(
    path: str | Path,
    params: dict[str, float],
    provenance: CalibrationProvenance,
    validation_metrics: dict[str, Any],
) -> bool:
    """Writes the versioned calibration artifact (atomic tmp+replace)."""
    payload = {
        "schema": CALIBRATION_SCHEMA_VERSION,
        "params": {k: float(v) for k, v in params.items()},
        "provenance": provenance.to_dict(),
        "validation_metrics": validation_metrics,
    }
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as exc:
        logger.error("[CALIBRATION] artifact persist failed", error=str(exc))
        return False


def build_calibration_artifact(
    *,
    model_version: str,
    feature_schema_version: str,
    cal_confidences: list[float],
    cal_outcomes: list[int],
    val_confidences: list[float],
    val_outcomes: list[int],
    calibration_dataset_id: str,
    validation_dataset_id: str,
    cal_start: str,
    cal_end: str,
    val_start: str,
    val_end: str,
    artifact_fingerprint: str = "",
    calibration_is_oos: bool = False,
    validation_is_oos: bool = False,
) -> dict[str, Any]:
    """Fits on the calibration slice, evaluates on the DISJOINT validation
    slice, and returns the artifact payload (params + provenance + metrics).

    ``artifact_fingerprint`` (sha256[:16] of the exact model artifact the
    confidences came from) is REQUIRED for a production-trustworthy artifact:
    without it ``is_complete()`` is False and every consumer treats the
    calibration as NOT_CALIBRATED (mission phase 7 identity binding).

    ``calibration_is_oos`` / ``validation_is_oos`` are the explicit holdout
    declaration (mission phase 2): both slices must be declared OOS/holdout.
    A train-slice observation is in-sample BY DEFINITION — fitting on it
    would measure memory, not calibration — so the caller must declare the
    slice status and a False declaration is rejected with TRAIN_SLICE.

    Raises ValueError on insufficient samples — the caller must then keep
    flat sizing (INSUFFICIENT_DATA, never OPTIMIZED).
    """
    if not calibration_is_oos or not validation_is_oos:
        raise ValueError(
            "TRAIN_SLICE_REJECTED: calibration requires OOS/holdout-declared "
            "slices (calibration_is_oos and validation_is_oos must both be "
            "True); fitting on training observations is contamination."
        )
    if len(cal_confidences) < MIN_CALIBRATION_SAMPLES:
        raise ValueError(
            f"INSUFFICIENT_DATA: {len(cal_confidences)} calibration samples < "
            f"{MIN_CALIBRATION_SAMPLES}"
        )
    if len(val_confidences) < MIN_CALIBRATION_SAMPLES:
        raise ValueError(
            f"INSUFFICIENT_DATA: {len(val_confidences)} validation samples < "
            f"{MIN_CALIBRATION_SAMPLES}"
        )
    params = fit_platt_calibration(cal_confidences, cal_outcomes)
    calibrator = ConfidenceCalibrator(
        params=params,
        provenance=CalibrationProvenance(
            model_version=model_version,
            calibration_dataset_id=calibration_dataset_id,
            artifact_fingerprint=artifact_fingerprint,
            calibration_period_start=cal_start,
            calibration_period_end=cal_end,
            validation_dataset_id=validation_dataset_id,
            validation_period_start=val_start,
            validation_period_end=val_end,
            method="platt_logistic",
            created_at=datetime.now(UTC).isoformat(),
            feature_schema_version=feature_schema_version,
            sample_count=len(cal_confidences),
            validation_sample_count=len(val_confidences),
        ),
    )
    # validation-side calibrated scores
    val_cal = [calibrator.calibrate(c)[0] for c in val_confidences]
    raw_metrics = evaluate_calibration(val_confidences, val_outcomes)
    cal_metrics = evaluate_calibration(val_cal, val_outcomes)
    return {
        "params": params,
        "provenance": calibrator._provenance.to_dict(),
        "validation_metrics": {
            "raw": raw_metrics,
            "calibrated": cal_metrics,
            "ece": cal_metrics["ece"],
            "brier": cal_metrics["brier"],
        },
    }
