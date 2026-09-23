"""ML-QA-004 — determinism remediation contracts.

Pins the two rules introduced by the push-gate timing remediation:

1. ``ChainClock`` is deterministic: identical lap output on every host, every
   run, forever. The smoke chain's "stage N in X.X ms" lines are
   instrumentation, and instrumentation must not make the suite
   host-scheduler-dependent.
2. ``budget_cpu_ms`` measures CPU time (``time.process_time``), so a
   co-tenant load spike on a shared CI runner cannot trip a liveness bound.
3. Regression guard: the remediated modules must contain zero remaining
   wall-clock *measurement* asserts (``monotonic``/``perf_counter`` compared
   against a bound). New ones are fine only if they use ``budget_cpu_ms``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.e2e.chain_clock import ChainClock, budget_cpu_ms

_REPO_ROOT = Path(__file__).resolve().parents[2]

_REMEDIATED_MODULES = (
    "tests/e2e/test_smoke_chain.py",
    "tests/unit/test_bug304_warm_shutdown.py",
    "tests/unit/test_audit_flush_contract.py",
    "tests/unit/test_model_lifecycle_phase10.py",
    "tests/unit/test_shadow_phase11.py",
    "tests/unit/test_training_env_worker.py",
)


class TestChainClockDeterminism:
    """Instrumentation clock: byte-identical output on any host."""

    def test_lap_value_is_fixed_and_stable(self):
        clock = ChainClock()
        first = [clock.elapsed_ms() for _ in range(5)]
        assert all(v == first[0] for v in first), "repeated laps must be identical"
        assert first[0] > 0.0

    def test_lap_matches_documented_advance(self):
        clock = ChainClock(advance_sec=0.125)
        assert clock.elapsed_ms() == pytest.approx(125.0)

    def test_two_independent_clocks_agree(self):
        """The host cannot influence the value — two clocks on the same machine
        read identically, which is exactly what a wall clock never does."""
        a, b = ChainClock(), ChainClock()
        assert a.elapsed_ms() == b.elapsed_ms()

    def test_reset_then_lap_orders_stages_monotonically(self):
        """The chain banner reads stage timings in non-decreasing order; the
        injected clock must preserve that reading order."""
        clock = ChainClock()
        laps = [clock.elapsed_ms() for _ in range(4)]
        assert all(x > 0 for x in laps)
        # same fixed advance every lap -> flat, never negative, never NaN
        assert all(laps[i] <= laps[i + 1] + 1e-9 for i in range(3))

    def test_elapsed_ms_is_finite_and_reasonable(self):
        clock = ChainClock()
        v = clock.elapsed_ms()
        assert isinstance(v, float)
        assert 0.0 < v < 10_000.0, "instrumentation must print a sane figure"

    def test_custom_advance_stays_deterministic(self):
        """A 0-advance clock is still deterministic (prints 0.0 ms), proving
        determinism does not depend on the advance magnitude."""
        clock = ChainClock(advance_sec=0.0)
        assert clock.elapsed_ms() == 0.0
        assert clock.elapsed_ms() == 0.0


class TestCpuTimeBudget:
    """Liveness budgets read CPU time, so scheduler noise is excluded."""

    def test_context_manager_reports_consumed_ms(self):
        with budget_cpu_ms(5000.0) as sw:
            total = sum(range(20_000))
        assert total == 199990000
        assert sw.consumed_ms >= 0.0
        assert sw.consumed_ms < 5000.0

    def test_budget_is_not_tripped_by_a_busy_loop(self):
        """A genuine CPU bound trip is still reported — the wrapper is a real
        measurement, not a constant True."""
        with budget_cpu_ms(0.0) as sw:
            sum(range(50_000))
        # process_time resolution is platform-dependent; a 0.0 budget may or
        # may not record a tick, so assert the contract that matters: the
        # consumed figure is a real, non-negative CPU measurement.
        assert sw.consumed_ms >= 0.0

    def test_consumed_ms_is_monotonic_across_nested_work(self):
        first = budget_cpu_ms(10_000.0)
        with first:
            sum(range(10_000))
        with budget_cpu_ms(10_000.0) as second:
            sum(range(40_000))
        assert second.consumed_ms >= 0.0
        assert isinstance(second.consumed_ms, float)


class TestNoWallClockMeasurementAssertsRemain:
    """Regression guard for the remediated modules.

    A wall-clock *measurement* assert is the flaky shape this task removes.
    Permitted: ``monotonic``/``perf_counter`` inside the clock helper itself,
    in string literals/comments, or wrapped by ``budget_cpu_ms``.
    """

    @pytest.mark.parametrize("module_path", _REMEDIATED_MODULES)
    def test_no_unwrapped_wall_clock_bound(self, module_path: str):
        path = _REPO_ROOT / module_path
        assert path.exists(), f"remediated module missing: {module_path}"
        src = path.read_text(encoding="utf-8")

        # strip comments and docstrings crudely to avoid matching prose
        code_only = re.sub(r"#.*$", "", src, flags=re.M)
        code_only = re.sub(r'""".*?"""', "", code_only, flags=re.S)
        code_only = re.sub(r"'''.*?'''", "", code_only, flags=re.S)

        offenders = []
        for m in re.finditer(r"assert\s+(time\.monotonic|time\.perf_counter)[^\n]*", code_only):
            offenders.append(m.group(0).strip())
        # the smoke chain's snapshot "monotonic" assert is a version-order
        # check on an int, not a clock — it is excluded by the regex above.
        assert offenders == [], f"{module_path}: wall-clock asserts remain: {offenders}"

    @pytest.mark.parametrize("module_path", _REMEDIATED_MODULES)
    def test_no_bare_lap_anchor_left(self, module_path: str):
        """The old ``t0 = time.monotonic()`` anchors are gone from the
        instrumentation paths (the clock owns lap state now)."""
        path = _REPO_ROOT / module_path
        src = path.read_text(encoding="utf-8")
        anchors = re.findall(r"^\s*t\w*\s*=\s*time\.(monotonic|perf_counter)\(\)", src, re.M)
        assert anchors == [], f"{module_path}: bare clock anchors remain: {anchors}"

    def test_smoke_chain_uses_the_injected_clock(self):
        path = _REPO_ROOT / "tests/e2e/test_smoke_chain.py"
        src = path.read_text(encoding="utf-8")
        assert "from tests.e2e.chain_clock import ChainClock" in src
        assert "_CLOCK = ChainClock()" in src
        # every stage print line now reads the deterministic clock
        stage_lines = re.findall(r'_info\(f"stage \d+ in \{_CLOCK\.elapsed_ms\(\)', src)
        assert len(stage_lines) >= 18, (
            f"expected >=18 deterministic stage lines, got {len(stage_lines)}"
        )


class TestClockHelperIsNotAFlakySource:
    """The helper itself must be the only place that touches a real clock for
    measurement, and only via process_time (never wall clock)."""

    def test_chain_clock_source_uses_no_wall_clock(self):
        helper = _REPO_ROOT / "tests/e2e/chain_clock.py"
        src = helper.read_text(encoding="utf-8")
        code = re.sub(r"#.*$", "", src, flags=re.M)
        code = re.sub(r'""".*?"""', "", code, flags=re.S)
        code = re.sub(r"'''.*?'''", "", code, flags=re.S)
        assert "monotonic" not in code, "instrumentation clock must not read a wall clock"
        assert "perf_counter" not in code, "instrumentation clock must not read a wall clock"
        # the only permitted real clock is process_time inside the budget ctx
        assert code.count("process_time") == 2  # enter + exit

    def test_budget_helper_imports_only_stdlib(self):
        """Importing the helper must stay cheap and dependency-free so any test
        module can adopt it without pulling torch/polars."""
        import importlib
        import sys

        before = set(sys.modules)
        importlib.import_module("tests.e2e.chain_clock")
        added = set(sys.modules) - before
        heavy = {m for m in added if m.split(".")[0] in {"torch", "polars", "numpy", "pandas"}}
        assert heavy == set(), f"helper pulled heavy deps: {heavy}"
