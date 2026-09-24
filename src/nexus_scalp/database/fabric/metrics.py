"""Fabric metrics — latency, pool, queue and failure observability.

Phase 26 of the DATABASE FABRIC mission (DB-FABRIC-001).

Design rules:
  * no secrets in metric names or labels (connection strings never appear);
  * lock-free fast path for the hot read/write recording — a metric must
    never become the latency problem it measures (plain float add +
    a ring buffer of recent samples for percentiles);
  * every counter is an int, every latency a float in milliseconds;
  * percentiles are computed on demand from the ring buffer, so p99 is a
    real measurement of real samples, not a guess.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Final

DEFAULT_LATENCY_SAMPLES: Final[int] = 512


@dataclass
class LatencyHistogram:
    """Bounded sample window with on-demand percentiles (ms)."""

    name: str
    capacity: int = DEFAULT_LATENCY_SAMPLES
    _samples: deque[float] = field(
        default_factory=lambda: deque(maxlen=DEFAULT_LATENCY_SAMPLES), repr=False
    )
    _count: int = 0
    _sum: float = 0.0
    _max: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            self.capacity = 1
        self._samples = deque(maxlen=self.capacity)

    def record(self, ms: float) -> None:
        """Record one latency sample (milliseconds)."""
        if ms is None or math.isnan(ms) or ms < 0:  # type: ignore[redundant-expr]
            return
        with self._lock:
            self._count += 1
            self._sum += ms
            self._max = max(self._max, ms)
            self._samples.append(ms)

    @property
    def count(self) -> int:
        return self._count

    def _sorted_window(self) -> list[float]:
        return sorted(self._samples)

    def percentile(self, p: float) -> float:
        """Percentile over the sample window (0-100). 0 when empty."""
        with self._lock:
            if not self._samples:
                return 0.0
            window = sorted(self._samples)
        if len(window) == 1:
            return window[0]
        rank = (p / 100.0) * (len(window) - 1)
        lo = math.floor(rank)
        hi = math.ceil(rank)
        if lo == hi:
            return window[int(rank)]
        frac = rank - lo
        return window[lo] + (window[hi] - window[lo]) * frac

    @property
    def p50(self) -> float:
        return self.percentile(50.0)

    @property
    def p95(self) -> float:
        return self.percentile(95.0)

    @property
    def p99(self) -> float:
        return self.percentile(99.0)

    @property
    def max_ms(self) -> float:
        return self._max

    @property
    def mean_ms(self) -> float:
        return self._sum / self._count if self._count else 0.0

    def snapshot(self) -> dict[str, float | int]:
        return {
            "count": self._count,
            "p50_ms": round(self.p50, 3),
            "p95_ms": round(self.p95, 3),
            "p99_ms": round(self.p99, 3),
            "max_ms": round(self._max, 3),
            "mean_ms": round(self.mean_ms, 3),
        }


class FabricMetrics:
    """Per-domain fabric metrics.  No secrets in any label.

    Counters are simple ints guarded by a lock; the hot path records one
    latency sample per operation (lock hold = a deque append).
    """

    __slots__ = (
        "_counters",
        "_lock",
        "_start",
        "batch_flush_latency",
        "connection_acquire_latency",
        "domain",
        "read_latency",
        "write_latency",
    )

    def __init__(self, domain: str) -> None:
        self.domain = domain
        self.read_latency = LatencyHistogram(f"db.{domain}.read")
        self.write_latency = LatencyHistogram(f"db.{domain}.write")
        self.batch_flush_latency = LatencyHistogram(f"db.{domain}.batch_flush")
        self.connection_acquire_latency = LatencyHistogram(f"db.{domain}.conn_acquire")
        # counters: name -> int
        self._counters: dict[str, int] = {
            "reads": 0,
            "writes": 0,
            "read_timeouts": 0,
            "write_timeouts": 0,
            "read_errors": 0,
            "write_errors": 0,
            "retries": 0,
            "dead_letter": 0,
            "overflow": 0,
            "overflow_recovered": 0,
            "overflow_failed": 0,
            "backpressure": 0,
            "dropped_telemetry": 0,
            "lock_waits": 0,
            "pool_exhausted": 0,
            "read_only_violations": 0,
            "write_intent_violations": 0,
        }
        self._lock = threading.Lock()
        self._start = time.monotonic()

    # -- counters ---------------------------------------------------------

    def inc(self, name: str, by: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + int(by)

    def counter(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    # -- gauge-ish state (set by pools; not a counter) --------------------

    def set_gauge(self, name: str, value: int) -> None:
        with self._lock:
            self._counters[f"gauge:{name}"] = int(value)

    def gauge(self, name: str) -> int:
        with self._lock:
            return self._counters.get(f"gauge:{name}", 0)

    # -- snapshot ---------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            counters = dict(self._counters)
        gauges = {k.split(":", 1)[1]: v for k, v in counters.items() if k.startswith("gauge:")}
        plain = {k: v for k, v in counters.items() if not k.startswith("gauge:")}
        return {
            "domain": self.domain,
            "uptime_sec": round(time.monotonic() - self._start, 1),
            "counters": plain,
            "gauges": gauges,
            "read_latency": self.read_latency.snapshot(),
            "write_latency": self.write_latency.snapshot(),
            "batch_flush_latency": self.batch_flush_latency.snapshot(),
            "connection_acquire_latency": self.connection_acquire_latency.snapshot(),
        }
