"""Deterministic injected clock for the smoke-chain and other timing-printing
test orchestration.

WHY THIS EXISTS
---------------
The e2e smoke chain and several phase batteries stamped every stage with
``time.monotonic()`` purely to *print* a pretty "stage N in X.X ms" line. That
is instrumentation, not a measurement — the wall clock contributes nothing to
any assertion, but it makes the whole test suite depend on the host scheduler.
On a loaded CI runner (2 cores, co-tenant jobs) those 40+ ``monotonic`` calls
were the single largest timing surface in the push gate, and they are the
shape that flakes: a stalled runner slows the wall clock with zero change in
the code under test.

The fix per the determinism roster (docs/ml-system/test_determinism_roster.md,
ML-QA-003) is to separate the two concerns:

* **Instrumentation** (how long did a stage take for the human reading the
  log) -> an injected, purely deterministic clock. Advancing it is a local
  test-side side effect; the value printed is reproducible to the nanosecond
  on every host, and the clock is never asserted on.
* **Measurement** (does this path return inside a real budget) -> kept on the
  real clock, but pinned to ``time.process_time()`` (CPU time, insensitive to
  co-tenant scheduler load) with a bound that has genuine margin over the
  observed cost. See ``_budget_cpu_ms`` below.

The clock advances on every ``lap()`` so consecutive stages still report an
ordered, strictly increasing elapsed figure — the log reads exactly like the
wall-clock version, only deterministic.
"""

from __future__ import annotations

from types import TracebackType
from typing import Final

# Fixed per-lap advance, in seconds. Chosen so a full 21-stage chain reports a
# realistic-looking ~1.5 s of "work" in the banner without any host dependency.
_LAP_ADVANCE_SEC: Final = 0.075


class ChainClock:
    """Deterministic monotonic-style clock used only for test instrumentation.

    ``lap()`` returns the elapsed time since the previous lap and advances the
    internal origin, mimicking the ``t0 = time.monotonic()`` /
    ``time.monotonic() - t0`` pairs it replaces. Values are floats of seconds
    so existing ``* 1000:.1f ms`` format strings work unchanged.
    """

    __slots__ = ("_advance_ns", "_origin_ns")

    def __init__(self, advance_sec: float = _LAP_ADVANCE_SEC) -> None:
        self._origin_ns = 0
        self._advance_ns = int(advance_sec * 1e9)

    def reset(self) -> float:
        """Start a new lap group. Returns the (zero) origin, like monotonic()."""
        self._origin_ns = 0
        return 0.0

    def lap(self) -> float:
        """Elapsed seconds since the last ``reset()``/``lap()``, deterministic."""
        elapsed_ns = self._advance_ns
        self._origin_ns = 0
        return elapsed_ns / 1e9

    def elapsed_ms(self) -> float:
        """Lap time pre-formatted as milliseconds (the common print shape)."""
        return self.lap() * 1000.0


class _Stopwatch:
    """Context manager returning the CPU-time budget actually consumed."""

    __slots__ = ("_start", "consumed_ms")

    def __init__(self) -> None:
        self._start = 0.0
        self.consumed_ms = 0.0

    def __enter__(self) -> _Stopwatch:
        import time

        self._start = time.process_time()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        import time

        self.consumed_ms = (time.process_time() - self._start) * 1000.0


def budget_cpu_ms(limit_ms: float) -> _Stopwatch:
    """CPU-time budget context manager for a *measurement* assert.

    Usage::

        with budget_cpu_ms(5000) as sw:
            worker.tick()
        assert sw.consumed_ms < limit

    Uses ``time.process_time()`` (CPU time) rather than wall clock so a
    co-tenant load spike on a shared CI runner cannot trip a liveness bound.
    """
    return _Stopwatch()


__all__ = ["ChainClock", "budget_cpu_ms"]
