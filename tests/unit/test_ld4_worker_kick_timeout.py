"""
LD-4: worker-kick timeout budget (default must clear the research cycle ceiling).

Regression tests for the defect where ``WORKER_KICK_TIMEOUT_SEC`` defaulted to
45s while the RESEARCH worker's healthy dataset pass measures 125-150s, so
every kick abandoned a healthy in-flight cycle, logged a spurious ERROR, and
re-ran the work.

Covers:
  * the default budget is 180 (above the measured 150s ceiling, with headroom)
  * NSE_WORKER_KICK_TIMEOUT still overrides the default when set at import time
  * kick_worker still detaches a genuinely hung call (sleeps past the budget)
  * kick_worker completes a fast call instead of timing it out
  * the TIMEOUT log line carries the worker's last-known cycle duration so an
    operator can separate a slow-but-healthy cycle from a wedged call

Hermetic: no LiveEngine instance, no MT5, no real database — asyncio plus fake
workers. ``LiveEngine`` is imported only to read the class attribute.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import pytest

from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.application.live_workers import WorkerSupervisor, _last_cycle_hint

REPO_ROOT = "C:/Users/Capsizer/source/repos/nse-ld-lane-d"
ENV_OVERRIDE_NAME = "NSE_WORKER_KICK_TIMEOUT"


class _SlowWorker:
    """Stand-in for a worker whose real cycle outruns a too-small budget."""

    def __init__(self, cycle_sec: float, last_cycle_duration: float = 0.0) -> None:
        self._cycle_sec = cycle_sec
        self.last_cycle_duration = last_cycle_duration
        self.calls = 0
        self.completed = 0

    def tick(self) -> bool:
        self.calls += 1
        started = time.perf_counter()
        time.sleep(self._cycle_sec)
        self.last_cycle_duration = time.perf_counter() - started
        self.completed += 1
        return True


class _MsWorker:
    """Worker exposing its duration in milliseconds (IncidentWorker shape)."""

    def __init__(self, cycle_duration_ms: float = 0.0) -> None:
        self.cycle_duration_ms = cycle_duration_ms

    def tick(self) -> bool:
        return True


async def _await_kick(
    name: str,
    fn,
    inflight: set[str],
    background: set,
    timeout_sec: float,
) -> None:
    """Run one kick until its task settles (completion or timeout), then cancel stragglers."""
    WorkerSupervisor.kick_worker(name, fn, inflight, background, timeout_sec=timeout_sec)
    # Let the to_thread coroutine actually start, so the timeout races a running
    # call (a call that never starts cannot time out).
    await asyncio.sleep(0.02)
    # The timeout path leaves the to_thread coroutine suspended; give wait_for a
    # real chance to fire before we cancel it.
    await asyncio.sleep(min(timeout_sec, 0.5) + 0.15)
    for task in list(background):
        task.cancel()
    if background:
        await asyncio.gather(*background, return_exceptions=True)
    background.clear()


def _collect_timeout_lines(capsys) -> list[str]:
    """structlog writes to stdout, so capture there rather than via caplog."""
    captured = capsys.readouterr()
    return [line for line in (captured.out + captured.err).splitlines() if "event=TIMEOUT" in line]


class TestKickTimeoutBudget:
    def test_default_is_180_above_research_ceiling(self):
        # 45 was the defect value; the healthy ceiling observed was ~150s.
        assert LiveEngine.WORKER_KICK_TIMEOUT_SEC == 180
        assert LiveEngine.WORKER_KICK_TIMEOUT_SEC > 150.0

    def test_env_override_wins_when_set_at_import(self):
        # The attribute reads the env at class-definition time, so the override
        # is only visible to a process that had the variable set at import.
        assert run_fresh_import("240") == 240.0

    def test_env_override_unset_falls_back_to_180(self):
        assert run_fresh_import(None) == 180.0

    @pytest.mark.asyncio
    async def test_genuinely_hung_call_is_detached(self):
        """A call that outlives the budget is abandoned, not awaited forever."""
        inflight: set[str] = set()
        background: set = set()
        worker = _SlowWorker(cycle_sec=0.6, last_cycle_duration=0.05)

        await _await_kick("RESEARCH", worker.tick, inflight, background, timeout_sec=0.15)

        # The hung call was detached: it never completed inside the budget.
        assert worker.completed == 0
        # Backpressure was released so the next kick can be scheduled.
        assert "RESEARCH" not in inflight

    @pytest.mark.asyncio
    async def test_fast_call_completes_without_timeout(self):
        inflight: set[str] = set()
        background: set = set()
        worker = _SlowWorker(cycle_sec=0.05)

        await _await_kick("RESEARCH", worker.tick, inflight, background, timeout_sec=5.0)

        assert worker.completed == 1
        assert "RESEARCH" not in inflight

    @pytest.mark.asyncio
    async def test_backpressure_blocks_overlapping_kicks(self):
        inflight: set[str] = set()
        background: set = set()
        worker = _SlowWorker(cycle_sec=0.3)

        WorkerSupervisor.kick_worker("RESEARCH", worker.tick, inflight, background, timeout_sec=5.0)
        # The inflight marker is set synchronously by kick_worker, before the
        # thread even starts; the second kick must see it and no-op.
        WorkerSupervisor.kick_worker("RESEARCH", worker.tick, inflight, background, timeout_sec=5.0)
        assert "RESEARCH" in inflight

        await asyncio.sleep(0.5)
        assert worker.calls == 1, "second kick while inflight must be a no-op"

        for task in list(background):
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        background.clear()


class TestTimeoutLogVisibility:
    @pytest.mark.asyncio
    async def test_timeout_log_includes_last_cycle_duration(self, capsys):
        """LD-4 part 2: the line must say how long healthy cycles take."""
        inflight: set[str] = set()
        background: set = set()
        # A worker whose previous healthy cycle took 132s — the operator needs
        # to see that next to a 45s budget to read the log correctly.
        worker = _SlowWorker(cycle_sec=0.4, last_cycle_duration=132.0)

        await _await_kick("RESEARCH", worker.tick, inflight, background, timeout_sec=0.1)

        timeout_lines = _collect_timeout_lines(capsys)
        assert timeout_lines, "a hung call must still log TIMEOUT and be detached"
        line = timeout_lines[0]
        assert "last_cycle_duration_sec=132.0" in line
        assert "timeout_sec=0.1" in line
        assert "detaching hung call" in line

    @pytest.mark.asyncio
    async def test_research_healthy_cycle_fits_new_budget(self):
        """The LD-4 failure mode: a 132s healthy cycle must NOT be detached.

        With the old 45s default this completed==0 assertion failed; the budget
        now clears the measured 125-150s ceiling so a healthy cycle is allowed
        to finish instead of being abandoned and re-run.
        """
        inflight: set[str] = set()
        background: set = set()
        worker = _SlowWorker(cycle_sec=0.2, last_cycle_duration=132.0)

        # 180s default, scaled down proportionally to stay a sub-second test.
        await _await_kick("RESEARCH", worker.tick, inflight, background, timeout_sec=0.6)

        assert worker.completed == 1
        assert "RESEARCH" not in inflight

    @pytest.mark.asyncio
    async def test_timeout_log_unknown_when_worker_has_no_duration(self, capsys):
        """No duration attribute -> an explicit `unknown`, never an exception."""
        inflight: set[str] = set()
        background: set = set()

        class _Bare:
            def tick(self):
                time.sleep(0.3)

        await _await_kick("CAL", _Bare().tick, inflight, background, timeout_sec=0.1)

        lines = _collect_timeout_lines(capsys)
        assert lines
        assert "last_cycle_duration=unknown" in lines[0]

    def test_hint_reads_millisecond_attribute(self):
        hint = _last_cycle_hint(_MsWorker(cycle_duration_ms=1500.0).tick)
        assert hint == "last_cycle_duration_sec=1.5"

    def test_hint_reports_unknown_for_bare_callable(self):
        def bare():
            return None

        assert _last_cycle_hint(bare) == "last_cycle_duration=unknown"

    def test_hint_reports_unknown_for_zero_duration(self):
        # A worker that has never completed a cycle has no useful number yet.
        assert _last_cycle_hint(_SlowWorker(cycle_sec=0.0).tick) == "last_cycle_duration=unknown"

    def test_hint_never_raises_on_broken_worker(self):
        class _Broken:
            @property
            def last_cycle_duration(self):
                raise RuntimeError("state corrupted")

            def tick(self):
                return True

        assert _last_cycle_hint(_Broken().tick) == "last_cycle_duration=unknown"


class TestFastPathUntouched:
    @pytest.mark.asyncio
    async def test_no_timeout_logged_for_completed_call(self, capsys):
        """The hint is timeout-only: a completing call logs no TIMEOUT line."""
        inflight: set[str] = set()
        background: set = set()
        worker = _SlowWorker(cycle_sec=0.02)

        await _await_kick("RESEARCH", worker.tick, inflight, background, timeout_sec=5.0)

        assert worker.completed == 1
        assert _collect_timeout_lines(capsys) == []


def run_fresh_import(env_value: str | None) -> float:
    """Import ``LiveEngine`` in a clean interpreter with a controlled env var."""
    env = dict(os.environ)
    env.pop(ENV_OVERRIDE_NAME, None)
    if env_value is not None:
        env[ENV_OVERRIDE_NAME] = env_value
    env["PYTHONPATH"] = os.path.join(REPO_ROOT, "src")

    script = (
        "from nexus_scalp.application.live_engine import LiveEngine;"
        "print(LiveEngine.WORKER_KICK_TIMEOUT_SEC)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return float(result.stdout.strip())
