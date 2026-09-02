"""Inference Latency Forensics — staged, honest latency measurement.

WHY THIS EXISTS
---------------
The UI previously displayed a single "Latency: 5.40ms" that actually covered
validate + feature-tensor conversion + scaler + tensor creation + nan_to_num
+ a debug `.detach().cpu().numpy().tolist()` copy + model forward — NOT the
model forward alone. This module splits the pipeline into honest stages and
uses ONLY high-resolution monotonic clocks (time.perf_counter_ns — never
wall-clock) so the displayed numbers are reproducible and attributable.

STAGES (brief 2):
    T0 market event received
    T1 feature preparation starts
    T2 70D feature vector complete
    T3 normalization/scaler complete
    T4 tensor creation complete
    T5 model forward starts
    T6 model forward ends
    T7 output decoding complete
    T8 confidence calculation complete
    T9 policy evaluation complete
    T10 prediction/decision published

Derived latencies:
    feature_ms      T2 - T1
    scaling_ms      T3 - T2
    tensor_ms       T4 - T3
    model_ms        T6 - T5   (the honest "Model Forward")
    postprocess_ms  T8 - T6
    decision_ms     T10 - T8
    e2e_ms          T10 - T0

The UI must not call all of these "Prediction Latency".
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class LatencyStage(StrEnum):
    """Canonical stage markers (ordered as the pipeline runs)."""

    T0_MARKET_EVENT = "T0"
    T1_FEATURE_START = "T1"
    T2_FEATURE_DONE = "T2"
    T3_SCALER_DONE = "T3"
    T4_TENSOR_DONE = "T4"
    T5_MODEL_START = "T5"
    T6_MODEL_DONE = "T6"
    T7_DECODE_DONE = "T7"
    T8_CONFIDENCE_DONE = "T8"
    T9_POLICY_DONE = "T9"
    T10_PUBLISHED = "T10"


#: Ordered stage list (index = execution order).
STAGE_ORDER: tuple[LatencyStage, ...] = (
    LatencyStage.T0_MARKET_EVENT,
    LatencyStage.T1_FEATURE_START,
    LatencyStage.T2_FEATURE_DONE,
    LatencyStage.T3_SCALER_DONE,
    LatencyStage.T4_TENSOR_DONE,
    LatencyStage.T5_MODEL_START,
    LatencyStage.T6_MODEL_DONE,
    LatencyStage.T7_DECODE_DONE,
    LatencyStage.T8_CONFIDENCE_DONE,
    LatencyStage.T9_POLICY_DONE,
    LatencyStage.T10_PUBLISHED,
)


def now_ns() -> int:
    """High-resolution monotonic clock (never wall-clock)."""
    return time.perf_counter_ns()


class LatencyTracer:
    """Records stage timestamps and derives honest per-stage latencies.

    Thread-safe per instance; one instance per inference event (cheap: a
    dict of 11 ints). Callers may record a SUBSET of stages — unrecorded
    stages simply yield no derived latency for the missing pair.
    """

    def __init__(self, prediction_id: str = "", correlation_id: str = "") -> None:
        self.prediction_id = prediction_id
        self.correlation_id = correlation_id
        self._stamps: dict[LatencyStage, int] = {}
        self._started = now_ns()

    def mark(self, stage: LatencyStage) -> None:
        self._stamps[stage] = now_ns()

    def stamp(self, stage: LatencyStage, ns: int | None = None) -> None:
        self._stamps[stage] = ns if ns is not None else now_ns()

    def has(self, stage: LatencyStage) -> bool:
        return stage in self._stamps

    def elapsed_ns(self, a: LatencyStage, b: LatencyStage) -> int | None:
        if a in self._stamps and b in self._stamps:
            return self._stamps[b] - self._stamps[a]
        return None

    def ms(self, a: LatencyStage, b: LatencyStage) -> float | None:
        ns = self.elapsed_ns(a, b)
        return None if ns is None else round(ns / 1_000_000.0, 3)

    # -- derived ------------------------------------------------------------
    def feature_ms(self) -> float | None:
        return self.ms(LatencyStage.T1_FEATURE_START, LatencyStage.T2_FEATURE_DONE)

    def scaling_ms(self) -> float | None:
        return self.ms(LatencyStage.T2_FEATURE_DONE, LatencyStage.T3_SCALER_DONE)

    def tensor_ms(self) -> float | None:
        return self.ms(LatencyStage.T3_SCALER_DONE, LatencyStage.T4_TENSOR_DONE)

    def model_ms(self) -> float | None:
        """HONEST Model Forward: T6 - T5 (nothing else)."""
        return self.ms(LatencyStage.T5_MODEL_START, LatencyStage.T6_MODEL_DONE)

    def postprocess_ms(self) -> float | None:
        return self.ms(LatencyStage.T6_MODEL_DONE, LatencyStage.T8_CONFIDENCE_DONE)

    def decision_ms(self) -> float | None:
        return self.ms(LatencyStage.T8_CONFIDENCE_DONE, LatencyStage.T10_PUBLISHED)

    def e2e_ms(self) -> float | None:
        """End-to-end: market event received (T0) to prediction published (T10)."""
        return self.ms(LatencyStage.T0_MARKET_EVENT, LatencyStage.T10_PUBLISHED)

    def pipeline_ms(self) -> float | None:
        """T1..T10 (feature start to published) — excludes queue wait."""
        return self.ms(LatencyStage.T1_FEATURE_START, LatencyStage.T10_PUBLISHED)

    def queue_ms(self) -> float | None:
        """T0..T1 (event received to processing start) — queue/调度 wait."""
        return self.ms(LatencyStage.T0_MARKET_EVENT, LatencyStage.T1_FEATURE_START)

    # -- serialization ------------------------------------------------------
    def to_dict(self, include_raw_ns: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "prediction_id": self.prediction_id,
            "correlation_id": self.correlation_id,
            "feature_ms": self.feature_ms(),
            "scaling_ms": self.scaling_ms(),
            "tensor_ms": self.tensor_ms(),
            "model_ms": self.model_ms(),
            "postprocess_ms": self.postprocess_ms(),
            "decision_ms": self.decision_ms(),
            "queue_ms": self.queue_ms(),
            "e2e_ms": self.e2e_ms(),
            "pipeline_ms": self.pipeline_ms(),
        }
        if include_raw_ns:
            out["stages_ns"] = {s.value: self._stamps.get(s) for s in STAGE_ORDER}
        return out

    def as_telemetry(self) -> dict[str, Any]:
        """[INFERENCE_LATENCY] structured telemetry payload (brief 38)."""
        return self.to_dict(include_raw_ns=False)


def percentiles_ms(samples_ms: list[float]) -> dict[str, float]:
    """p50/p90/p95/p99/max/min/mean/std of latency samples (ms).

    Inputs must already be ms floats (monotonic-derived). Returns {} for
    empty input.
    """
    if not samples_ms:
        return {}
    import statistics

    s = sorted(samples_ms)
    n = len(s)

    def pct(p: float) -> float:
        # nearest-rank percentile: ceil(p*n)-1 (0-based), clamped
        idx = min(max(math.ceil(p * n) - 1, 0), n - 1)
        return round(s[idx], 3)

    return {
        "min_ms": round(s[0], 3),
        "p50_ms": pct(0.50),
        "p90_ms": pct(0.90),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "max_ms": round(s[-1], 3),
        "mean_ms": round(statistics.fmean(s), 3),
        "std_ms": round(statistics.pstdev(s), 3),
        "sample_count": n,
    }


@dataclass
class LatencyStats:
    """Rolling latency stats window (bounded, cheap)."""

    max_samples: int = 2000
    samples: list[float] = field(default_factory=list)

    def add(self, ms: float) -> None:
        self.samples.append(ms)
        if len(self.samples) > self.max_samples:
            self.samples = self.samples[-self.max_samples :]

    def summary(self) -> dict[str, float]:
        return percentiles_ms(self.samples)

    def reset(self) -> None:
        self.samples.clear()


def latency_warning_threshold_ms() -> float:
    """Configurable observability threshold (brief 39); config-driven later."""
    return 100.0
