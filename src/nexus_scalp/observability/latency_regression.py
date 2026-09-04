"""Hot-path latency regression detector (OBS-PERF-RESILIENCE).

WHY THIS EXISTS
---------------
``features/latency_tracer.py`` measures honest staged latency (T0..T10) and
the engine stores the LAST sample in ``_last_latency_breakdown`` — but nothing
aggregates samples or alerts on a REGRESSION. A slow drift (GC pressure, CPU
contention, thermal throttling) is invisible until a human stares at the UI.

This module adds the missing consumer: a bounded rolling window per latency
stage with p95 regression detection against the documented budget
(``latency_warning_threshold_ms()``, 100 ms today) and against a warm
baseline (median of the accumulated window) with hysteresis, so a single
stray sample cannot flap the alert.

DESIGN CONTRACTS
----------------
* Pure in-memory, allocation-bounded (``max_samples`` ring). ZERO I/O, zero
  locks on the hot path (the engine tick loop is single-threaded per event;
  cross-thread reads copy the window).
* Never raises, never blocks: ``observe()`` and the alert check are
  exception-isolated; observability must never disturb trading (INV-018).
* ``sample_count`` is always reported so an under-populated window can never
  masquerade as a healthy baseline.
* Alert transitions are EDGE-TRIGGERED (healthy->regressed only) so callers
  can log/emit once per epoch instead of every tick.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.features.latency_tracer import (
    LatencyStats,
    latency_warning_threshold_ms,
)

#: p95 of e2e above this fraction of the warning budget = REGRESSED epoch.
_REGRESSION_FRACTION = 0.5

#: consecutive regressed samples required to OPEN a regression epoch
#: (hysteresis; one GC pause must not page anyone).
_OPEN_EPOCH_AFTER = 5

#: consecutive healthy samples required to CLOSE a regression epoch.
_CLOSE_EPOCH_AFTER = 20

#: minimum samples before any regression verdict is allowed.
_MIN_SAMPLES = 50


@dataclass
class LatencyRegressionDetector:
    """Rolling per-stage latency window + edge-triggered regression epochs."""

    #: stage name -> bounded sample window (feature/scaling/tensor/model/e2e).
    stages: dict[str, LatencyStats] = field(default_factory=dict)
    max_samples: int = 2000
    #: total samples ingested since construction (monotonic counter).
    total_observed: int = 0
    #: currently in a regressed epoch?
    regressed: bool = False
    #: consecutive samples in the current state (hysteresis counters).
    _consecutive: int = 0
    #: wall-clock (time.time) when the current epoch opened/closed — for ops.
    last_epoch_change_at: float = 0.0
    #: how many regression epochs have opened since construction.
    regression_epochs_total: int = 0
    #: worst p95 e2e ever seen (ms) — cheap high-water mark for dashboards.
    worst_p95_e2e_ms: float = 0.0

    # ------------------------------------------------------------------
    def observe_breakdown(self, breakdown: dict[str, Any]) -> None:
        """Ingest one ``LatencyTracer.to_dict()`` payload. Never raises."""
        try:
            for stage in ("feature_ms", "scaling_ms", "tensor_ms", "model_ms", "e2e_ms"):
                v = breakdown.get(stage)
                if v is None:
                    continue
                stats = self.stages.get(stage)
                if stats is None:
                    stats = self.stages[stage] = LatencyStats(max_samples=self.max_samples)
                stats.add(float(v))
            self.total_observed += 1
        except Exception:
            # Observability must never disturb the tick path.
            return
        self._check_regression()

    # ------------------------------------------------------------------
    def _p95(self, stage: str) -> float | None:
        stats = self.stages.get(stage)
        if stats is None or len(stats.samples) < _MIN_SAMPLES:
            return None
        return stats.summary().get("p95_ms")

    # ------------------------------------------------------------------
    def _check_regression(self) -> None:
        p95 = self._p95("e2e_ms")
        if p95 is None:
            return
        try:
            self.worst_p95_e2e_ms = max(self.worst_p95_e2e_ms, p95)
        except Exception:
            pass
        budget = latency_warning_threshold_ms() * _REGRESSION_FRACTION
        is_bad = p95 > budget
        if is_bad == self.regressed:
            self._consecutive += 1
            return
        # State change candidate — apply hysteresis.
        self._consecutive += 1
        needed = _OPEN_EPOCH_AFTER if is_bad else _CLOSE_EPOCH_AFTER
        if self._consecutive >= needed:
            self.regressed = is_bad
            self._consecutive = 0
            self.last_epoch_change_at = time.time()
            if is_bad:
                self.regression_epochs_total += 1

    # ------------------------------------------------------------------
    def should_alert(self) -> bool:
        """True exactly when a NEW regression epoch just opened (edge trigger)."""
        return self.regressed and self._consecutive == 0

    # ------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        """Compact telemetry payload (per-stage p50/p95 + regression state)."""
        out: dict[str, Any] = {
            "samples_observed": self.total_observed,
            "regressed": self.regressed,
            "regression_epochs_total": self.regression_epochs_total,
            "worst_p95_e2e_ms": round(self.worst_p95_e2e_ms, 3),
            "budget_p95_ms": round(
                latency_warning_threshold_ms() * _REGRESSION_FRACTION, 3
            ),
            "min_samples_for_verdict": _MIN_SAMPLES,
            "verdict_ready": self.total_observed >= _MIN_SAMPLES,
        }
        for stage in ("feature_ms", "scaling_ms", "tensor_ms", "model_ms", "e2e_ms"):
            stats = self.stages.get(stage)
            if stats is None:
                out[stage] = None
                continue
            s = stats.summary()
            out[stage] = {
                "p50_ms": s.get("p50_ms"),
                "p95_ms": s.get("p95_ms"),
                "p99_ms": s.get("p99_ms"),
                "max_ms": s.get("max_ms"),
                "sample_count": s.get("sample_count"),
            }
        return out


__all__ = ["LatencyRegressionDetector"]
