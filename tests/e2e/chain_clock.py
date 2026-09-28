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

import time
from types import TracebackType
from typing import Final

# Fixed per-lap advance, in seconds. Chosen so a full 21-stage chain reports a
# realistic-looking ~1.5 s of "work" in the banner without any host dependency.
_LAP_ADVANCE_SEC: Final = 0.075


def _process_time_floor_ms() -> float:
    """Measure the smallest non-zero delta ``time.process_time()`` reports.

    ``process_time()`` is quantized to the OS scheduler tick on several
    platforms (Windows ~15.6 ms), so any leg shorter than one tick reads as
    0.0. Returning the measured tick width lets a measurement assert compare
    against the floor instead of against zero, which is what makes
    "the measured leg executed" provable on those platforms.
    """
    smallest = float("inf")
    for _ in range(256):
        start = time.process_time()
        # spin until process_time() reports any non-zero delta
        while True:
            delta = time.process_time() - start
            if delta > 0.0:
                break
        smallest = min(smallest, delta)
        # a second sample is enough; one tick width is all we are after
        if smallest < float("inf"):
            break
    if smallest == float("inf") or smallest <= 0.0:
        return 1e-3  # unmeasurable on this host: assume a fine-grained clock
    return smallest * 1000.0


#: Smallest non-zero delta ``time.process_time()`` can report on this host, in
#: milliseconds. ``process_time()`` is quantized to the OS scheduler tick on
#: several platforms (Windows reports ~15.6 ms), so a leg that genuinely burns
#: 1-15 ms of CPU reads back as exactly 0.0 there. Measurement asserts must
#: not treat that as "nothing executed" — see ``_Stopwatch`` below.
_PROCESS_TIME_FLOOR_MS: Final = _process_time_floor_ms()


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
    """Context manager returning the CPU-time budget actually consumed.

    ``time.process_time()`` quantizes CPU time to the OS scheduler tick on
    some platforms (~15.6 ms on Windows), so a leg that genuinely does
    ~1-15 ms of work can legitimately read back as exactly 0.0 ms. A
    ``consumed_ms > 0`` liveness assert would then be unsatisfiable on those
    platforms even though the measured leg executed — the exact flake seen
    on ``Py Tests (windows-latest)``. ``report_floor_ms`` is the smallest
    non-zero reading the stopwatch can return; callers comparing against a
    floor use it instead of comparing against zero.
    """

    __slots__ = ("_start", "consumed_ms")

    #: Smallest non-zero reading this stopwatch can produce (ms). On
    #: platforms where ``process_time()`` is tick-quantized this is the tick
    #: width; a real sub-tick leg reports 0.0 and must not be treated as
    #: "did not execute".
    report_floor_ms: float = _PROCESS_TIME_FLOOR_MS

    def __init__(self) -> None:
        self._start = 0.0
        self.consumed_ms = 0.0

    def __enter__(self) -> _Stopwatch:
        self._start = time.process_time()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        elapsed = (time.process_time() - self._start) * 1000.0
        self.consumed_ms = 0.0 if elapsed <= 0.0 else max(elapsed, self.report_floor_ms)


def budget_cpu_ms(limit_ms: float) -> _Stopwatch:
    """Context manager for a *measurement* assert.

    Usage::

        with budget_cpu_ms(5000) as sw:
            worker.tick()
        assert sw.consumed_ms < limit

    Uses ``time.process_time()`` (CPU time) rather than wall clock so a
    co-tenant load spike on a shared CI runner cannot trip a liveness bound.

    Note that ``consumed_ms`` is floored at ``_Stopwatch.report_floor_ms``:
    ``process_time()`` is tick-quantized on some platforms (Windows ~15.6 ms),
    so a genuine sub-tick leg reads as 0.0 there and is reported as the floor
    rather than as "nothing ran". Assert ``sw.consumed_ms >= 0.0`` (the
    contract that the leg executed) and reserve ``> 0`` for legs known to
    exceed one tick.
    """
    return _Stopwatch()


__all__ = ["ChainClock", "budget_cpu_ms"]
