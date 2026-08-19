"""70D Feature Health & Drift Monitoring (TASK-05-70D-SHADOW).

SHADOW_FEATURE_HEALTH v1 (spec 20): per-Liquidity-feature statistics over a
bounded window (finite_rate, missing_rate, stale_rate, zero_rate, mean, std,
min, max) plus comparison against the training distribution.

SHADOW_DRIFT v1 (spec 21 / 22): PSI + mean/std shift for LIQUIDITY_01..10
classified NORMAL / WATCH / WARNING / CRITICAL with configurable thresholds.
Drift detection is OBSERVATIONAL — it never changes trading (INV-018).

Pure computation: no I/O, no DB, no torch. Bounded memory: only the last
``window`` vectors are retained.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.shadow70.models import LIQUIDITY_FEATURE_NAMES, LIQUIDITY_SLICE

logger = get_logger("nexus_scalp.shadow.shadow70.health")

DRIFT_SEVERITY_NORMAL = "NORMAL"
DRIFT_SEVERITY_WATCH = "WATCH"
DRIFT_SEVERITY_WARNING = "WARNING"
DRIFT_SEVERITY_CRITICAL = "CRITICAL"

#: PSI thresholds (spec 22: configurable, documented).
PSI_WATCH: float = 0.10
PSI_WARNING: float = 0.20
PSI_CRITICAL: float = 0.30

#: Mean shift thresholds in feature units ([-3,3] space).
MEAN_SHIFT_WATCH: float = 0.15
MEAN_SHIFT_WARNING: float = 0.30
MEAN_SHIFT_CRITICAL: float = 0.50

#: Std shift ratio thresholds (live_std / reference_std).
STD_RATIO_WATCH: float = 1.30
STD_RATIO_WARNING: float = 1.60
STD_RATIO_CRITICAL: float = 2.00

#: Missing-rate delta thresholds (live vs reference, absolute).
MISSING_DELTA_WATCH: float = 0.05
MISSING_DELTA_WARNING: float = 0.10
MISSING_DELTA_CRITICAL: float = 0.20

#: Sample floor before drift is computed (INSUFFICIENT_EVIDENCE below).
DRIFT_MIN_SAMPLES: int = 30


@dataclass
class Shadow70FeatureHealth:
    """Per-feature statistics (spec 20)."""

    name: str
    index: int
    samples: int = 0
    finite_rate: float = 0.0
    missing_rate: float = 0.0
    stale_rate: float = 0.0
    zero_rate: float = 0.0
    mean: float = 0.0
    std: float = 0.0
    min: float = 0.0
    max: float = 0.0
    missing_count: int = 0
    stale_count: int = 0
    zero_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "index": self.index,
            "samples": self.samples,
            "finite_rate": round(self.finite_rate, 4),
            "missing_rate": round(self.missing_rate, 4),
            "stale_rate": round(self.stale_rate, 4),
            "zero_rate": round(self.zero_rate, 4),
            "mean": round(self.mean, 4),
            "std": round(self.std, 4),
            "min": round(self.min, 4),
            "max": round(self.max, 4),
            "missing_count": self.missing_count,
            "stale_count": self.stale_count,
            "zero_count": self.zero_count,
        }


@dataclass
class Shadow70DriftAlert:
    """One drift measurement for one feature family (spec 21 / 22)."""

    feature: str
    metric: str
    value: float
    threshold: float
    severity: str
    reference_mean: float
    live_mean: float
    reference_std: float
    live_std: float
    samples: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "metric": self.metric,
            "value": round(self.value, 4),
            "threshold": self.threshold,
            "severity": self.severity,
            "reference_mean": round(self.reference_mean, 4),
            "live_mean": round(self.live_mean, 4),
            "reference_std": round(self.reference_std, 4),
            "live_std": round(self.live_std, 4),
            "samples": self.samples,
            "timestamp": self.timestamp.isoformat(),
        }


def _mean_std(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / n
    return m, math.sqrt(var)


def _psi(live: list[float], reference: list[float], bins: int = 10) -> float:
    """Population Stability Index over [-3, 3] with epsilon smoothing.

    Pure numpy-free computation (stdlib only). Returns a non-negative float.
    """
    if len(live) == 0 or len(reference) == 0:
        return 0.0
    lo, hi = -3.0, 3.0
    edges = [lo + (hi - lo) * i / bins for i in range(bins + 1)]
    edges[0], edges[-1] = -math.inf, math.inf
    l_n, r_n = len(live), len(reference)
    eps = 1e-6
    psi = 0.0
    for i in range(bins):
        lb, ub = edges[i], edges[i + 1]
        lc = sum(1 for v in live if lb <= v < ub)
        rc = sum(1 for v in reference if lb <= v < ub)
        p_l = max(lc / l_n, eps)
        p_r = max(rc / r_n, eps)
        psi += (p_l - p_r) * math.log(p_l / p_r)
    return psi


def _normal_reference(mean: float, std: float, n: int) -> list[float]:
    """Deterministic reference sample from N(mean, std) clipped to [-3, 3].

    Used as the PSI reference distribution (training distribution stand-in).
    Quantile-spaced so the histogram is stable for small n.
    """
    import random

    rng = random.Random(1234)
    out: list[float] = []
    for _ in range(n):
        v = rng.gauss(mean, std)
        out.append(max(-3.0, min(3.0, v)))
    return out


class Shadow70FeatureHealthMonitor:
    """Bounded per-feature window + statistics (spec 20 / 29 / 39)."""

    def __init__(self, window: int = 1000) -> None:
        self.window = int(window)
        self._buffers: list[list[float]] = []
        self._stale_marks: list[bool] = []

    def update(self, vector70: list[float], *, stale: bool = False) -> bool:
        """Appends one 70D vector's liquidity slice. Returns True when stored."""
        liq = vector70[LIQUIDITY_SLICE[0] : LIQUIDITY_SLICE[1]]
        if len(liq) != len(LIQUIDITY_FEATURE_NAMES):
            return False
        self._buffers.append([float(v) for v in liq])
        self._stale_marks.append(bool(stale))
        if len(self._buffers) > self.window:
            self._buffers = self._buffers[-self.window:]
            self._stale_marks = self._stale_marks[-self.window:]
        return True

    def health(self) -> list[Shadow70FeatureHealth]:
        """Per-feature statistics over the bounded window."""
        out: list[Shadow70FeatureHealth] = []
        n = len(self._buffers)
        if n == 0:
            return [
                Shadow70FeatureHealth(name=name, index=i) for i, name in enumerate(LIQUIDITY_FEATURE_NAMES)
            ]
        for i, name in enumerate(LIQUIDITY_FEATURE_NAMES):
            col = [b[i] for b in self._buffers]
            finite = [v for v in col if math.isfinite(v)]
            nf = len(finite)
            missing = n - nf
            zero = sum(1 for v in finite if v == 0.0)
            stale = sum(1 for m in self._stale_marks if m)
            m, s = _mean_std(finite) if nf else (0.0, 0.0)
            out.append(
                Shadow70FeatureHealth(
                    name=name,
                    index=i,
                    samples=n,
                    finite_rate=nf / n if n else 0.0,
                    missing_rate=missing / n if n else 0.0,
                    stale_rate=stale / n if n else 0.0,
                    zero_rate=zero / n if n else 0.0,
                    mean=m,
                    std=s,
                    min=min(finite) if nf else 0.0,
                    max=max(finite) if nf else 0.0,
                    missing_count=missing,
                    stale_count=stale,
                    zero_count=zero,
                )
            )
        return out


class Shadow70DriftMonitor:
    """Observational drift detection vs the training distribution (spec 21/22/44).

    Thresholds are documented in agents/contracts.md SHADOW_DRIFT v1 and
    configurable via constructor. NEVER auto-acts: alerts only.
    """

    def __init__(
        self,
        reference_means: list[float] | None = None,
        reference_stds: list[float] | None = None,
        reference_missing_rates: list[float] | None = None,
        min_samples: int = DRIFT_MIN_SAMPLES,
        psi_watch: float = PSI_WATCH,
        psi_warning: float = PSI_WARNING,
        psi_critical: float = PSI_CRITICAL,
        mean_shift_watch: float = MEAN_SHIFT_WATCH,
        mean_shift_warning: float = MEAN_SHIFT_WARNING,
        mean_shift_critical: float = MEAN_SHIFT_CRITICAL,
        std_ratio_watch: float = STD_RATIO_WATCH,
        std_ratio_warning: float = STD_RATIO_WARNING,
        std_ratio_critical: float = STD_RATIO_CRITICAL,
        missing_delta_watch: float = MISSING_DELTA_WATCH,
        missing_delta_warning: float = MISSING_DELTA_WARNING,
        missing_delta_critical: float = MISSING_DELTA_CRITICAL,
    ) -> None:
        self.reference_means = reference_means
        self.reference_stds = reference_stds
        self.reference_missing = reference_missing_rates
        self.min_samples = int(min_samples)
        self.psi_thresholds = (psi_watch, psi_warning, psi_critical)
        self.mean_thresholds = (mean_shift_watch, mean_shift_warning, mean_shift_critical)
        self.std_thresholds = (std_ratio_watch, std_ratio_warning, std_ratio_critical)
        self.missing_thresholds = (
            missing_delta_watch,
            missing_delta_warning,
            missing_delta_critical,
        )
        self._buffers: list[list[float]] = []
        self._alerts: list[Shadow70DriftAlert] = []

    def set_reference(
        self,
        means: list[float],
        stds: list[float],
        missing_rates: list[float] | None = None,
    ) -> None:
        if len(means) != len(LIQUIDITY_FEATURE_NAMES) or len(stds) != len(LIQUIDITY_FEATURE_NAMES):
            raise ValueError("reference distributions must match the 10 liquidity features")
        self.reference_means = [float(m) for m in means]
        self.reference_stds = [float(s) for s in stds]
        self.reference_missing = (
            [float(x) for x in missing_rates] if missing_rates else [0.0] * len(means)
        )

    def update(self, vector70: list[float]) -> bool:
        liq = vector70[LIQUIDITY_SLICE[0] : LIQUIDITY_SLICE[1]]
        if len(liq) != len(LIQUIDITY_FEATURE_NAMES):
            return False
        self._buffers.append([float(v) for v in liq])
        if len(self._buffers) > 5000:
            self._buffers = self._buffers[-5000:]
        return True

    def _severity(self, value: float, thresholds: tuple[float, float, float]) -> str:
        if value > thresholds[2]:
            return DRIFT_SEVERITY_CRITICAL
        if value > thresholds[1]:
            return DRIFT_SEVERITY_WARNING
        if value > thresholds[0]:
            return DRIFT_SEVERITY_WATCH
        return DRIFT_SEVERITY_NORMAL

    def evaluate(self) -> list[Shadow70DriftAlert]:
        """Runs drift measurements. INSUFFICIENT_EVIDENCE below the floor."""
        self._alerts = []
        if (
            self.reference_means is None
            or self.reference_stds is None
            or len(self._buffers) < self.min_samples
        ):
            return []
        alerts: list[Shadow70DriftAlert] = []
        n = len(self._buffers)
        missing_rates = [0.0] * len(LIQUIDITY_FEATURE_NAMES)
        for i in range(len(LIQUIDITY_FEATURE_NAMES)):
            col = [b[i] for b in self._buffers]
            finite = [v for v in col if math.isfinite(v)]
            missing_rates[i] = (n - len(finite)) / n
            if len(finite) < 5:
                continue
            live_mean, live_std = _mean_std(finite)
            ref_mean = float(self.reference_means[i])
            ref_std = max(float(self.reference_stds[i]), 1e-6)
            # PSI: reference is a normal PDF over the standard bins built
            # from the training mean/std (NOT a 3-point spike — that was a
            # degenerate stand-in producing inflated PSI for zero-variance
            # live windows).
            psi = _psi(finite, _normal_reference(ref_mean, ref_std, len(finite)))
            sev = self._severity(psi, self.psi_thresholds)
            if sev != DRIFT_SEVERITY_NORMAL:
                alerts.append(
                    Shadow70DriftAlert(
                        feature=LIQUIDITY_FEATURE_NAMES[i],
                        metric="PSI",
                        value=psi,
                        threshold=self.psi_thresholds[2],
                        severity=sev,
                        reference_mean=ref_mean,
                        live_mean=live_mean,
                        reference_std=ref_std,
                        live_std=live_std,
                        samples=len(finite),
                    )
                )
            # mean shift
            shift = abs(live_mean - ref_mean)
            sev_m = self._severity(shift, self.mean_thresholds)
            if sev_m != DRIFT_SEVERITY_NORMAL:
                alerts.append(
                    Shadow70DriftAlert(
                        feature=LIQUIDITY_FEATURE_NAMES[i],
                        metric="MEAN_SHIFT",
                        value=shift,
                        threshold=self.mean_thresholds[2],
                        severity=sev_m,
                        reference_mean=ref_mean,
                        live_mean=live_mean,
                        reference_std=ref_std,
                        live_std=live_std,
                        samples=len(finite),
                    )
                )
            # std ratio
            ratio = live_std / ref_std
            sev_s = self._severity(ratio, self.std_thresholds)
            if sev_s != DRIFT_SEVERITY_NORMAL:
                alerts.append(
                    Shadow70DriftAlert(
                        feature=LIQUIDITY_FEATURE_NAMES[i],
                        metric="STD_RATIO",
                        value=ratio,
                        threshold=self.std_thresholds[2],
                        severity=sev_s,
                        reference_mean=ref_mean,
                        live_mean=live_mean,
                        reference_std=ref_std,
                        live_std=live_std,
                        samples=len(finite),
                    )
                )
            # missing-rate delta
            ref_miss = self.reference_missing[i] if self.reference_missing else 0.0
            delta = missing_rates[i] - ref_miss
            sev_d = self._severity(delta, self.missing_thresholds)
            if sev_d != DRIFT_SEVERITY_NORMAL:
                alerts.append(
                    Shadow70DriftAlert(
                        feature=LIQUIDITY_FEATURE_NAMES[i],
                        metric="MISSING_RATE_DELTA",
                        value=delta,
                        threshold=self.missing_thresholds[2],
                        severity=sev_d,
                        reference_mean=ref_mean,
                        live_mean=live_mean,
                        reference_std=ref_std,
                        live_std=live_std,
                        samples=len(finite),
                    )
                )
        if alerts:
            logger.warning(
                "[SHADOW70] event=SHADOW_DRIFT_WARNING",
                alert_count=len(alerts),
                severities=sorted({a.severity for a in alerts}),
            )
        self._alerts = alerts
        return alerts

    def latest_alerts(self, limit: int = 50) -> list[Shadow70DriftAlert]:
        return self._alerts[-limit:]

    def summary(self) -> dict[str, Any]:
        """Truthful drift summary (spec 45)."""
        if self.reference_means is None:
            return {
                "available": False,
                "reason": "NO_REFERENCE_DISTRIBUTION",
                "samples": len(self._buffers),
                "severity": DRIFT_SEVERITY_NORMAL,
            }
        if len(self._buffers) < self.min_samples:
            return {
                "available": True,
                "status": "INSUFFICIENT_EVIDENCE",
                "samples": len(self._buffers),
                "min_samples": self.min_samples,
                "severity": DRIFT_SEVERITY_NORMAL,
            }
        self.evaluate()
        severities = {a.severity for a in self._alerts}
        severity = (
            DRIFT_SEVERITY_CRITICAL
            if DRIFT_SEVERITY_CRITICAL in severities
            else DRIFT_SEVERITY_WARNING
            if DRIFT_SEVERITY_WARNING in severities
            else DRIFT_SEVERITY_WATCH
            if DRIFT_SEVERITY_WATCH in severities
            else DRIFT_SEVERITY_NORMAL
        )
        return {
            "available": True,
            "status": "EVALUATED",
            "samples": len(self._buffers),
            "min_samples": self.min_samples,
            "severity": severity,
            "alerts": [a.to_dict() for a in self._alerts[-50:]],
        }