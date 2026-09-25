"""BUG-275 (NSE-Swarm role 6, 2026-09-14): DB-hygiene cadence clock-domain mix.

``MaintenanceCycle.run_cycle`` gates the runtime cleanup scheduler with its
wall-clock tick (``now_t = time.time()``), but ``RuntimeCleanupScheduler``
stamped ``_last_light`` / ``_last_deep`` internally with ``time.monotonic()``.
On every supported platform monotonic starts near boot (Linux/Windows/macOS:
~uptime, i.e. 1e5-1e6) while wall is ~1.79e9 — so ``is_deep_due(now_wall)``
was PERMANENTLY True: the DEEP index-scan branch (full PRAGMA scans over all
managed DBs) fired on EVERY 30-min light cycle instead of once per 6h, and
``next_light_in``/``status()`` reported nonsense across the domain boundary.

ML-QA-015 (clock-determinism pass): the suite previously *drove* that same
wall clock with three live ``time.time()`` reads, so its coverage of the
cadence boundary was a property of when the run happened, not of the code:
``run_cycle`` stamped ``time.time()`` and the assertions compared against
independent reads of it. The contract needs no real clock at all — every
cadence gate is ``(now - stamp) >= interval`` over two caller-domain values.
One deterministic wall clock now drives both sides (the module's own ``time``
reference, swapped in by the ``hygiene_clock`` fixture), so BOTH edges of the
inclusive ``>=`` boundary are exercised to the nanosecond without waiting out
a real 30-min/6h interval, and the domain invariant (wall, not monotonic) is
asserted against a fixed epoch instead of against a clock that moves while
the test runs.

Test-only: no production file is modified. The clock is swapped on the
production module's own ``time`` handle, so every ``time.time()`` call site
in ``hygiene_runtime`` (stamps, defaults, ``status``, the initial-audit run
id) is exercised — the cadence arithmetic is unchanged because it compares
caller-domain values.

xdist-safe: pure scheduler + stubbed worker/index-scan; no real DBs, no
threads, no network.
"""

from __future__ import annotations

import pytest

from nexus_scalp.hygiene import hygiene_runtime as _hygiene_module
from nexus_scalp.hygiene.hygiene_runtime import (
    RuntimeCleanupScheduler,
    RuntimeHygieneSettings,
)

# ML-QA-015: the one deterministic wall clock for the whole module. The
# production domain is WALL (time.time(), epoch ~1.79e9) — the BUG-275 defect
# was a monotonic stamp compared against a wall caller — so the fake reads a
# realistic epoch, not 0.0, and a domain regression (a monotonic stamp) still
# shows up as an out-of-range stamp instead of coincidentally passing.
_WALL_EPOCH = 1_900_000_000.0


class _WallClock:
    """A controllable stand-in for the production wall clock.

    ``advance``/``rewind`` move ONLY this clock; the real ``time`` module is
    untouched (``monotonic`` still measures real elapsed time, which is what
    ``run_cycle``'s duration timer is for).
    """

    def __init__(self) -> None:
        self._t = _WALL_EPOCH

    def time(self) -> float:
        return self._t

    def read(self) -> float:
        return self._t

    def advance(self, seconds: float) -> float:
        self._t += seconds
        return self._t

    def rewind(self, seconds: float) -> float:
        self._t -= seconds
        return self._t


_WALL_CLOCK = _WallClock()


@pytest.fixture
def hygiene_clock(monkeypatch):
    """Deterministic wall clock for the hygiene module.

    Swaps the production module's ``time`` reference so every ``time.time()``
    call site resolves to the shared clock, and resets it afterwards so one
    test's advance cannot leak into the next test's boundary arithmetic.
    """
    _real_time = _hygiene_module.time

    class _FakeTime:
        time = _WALL_CLOCK.time
        monotonic = _real_time.monotonic
        perf_counter = _real_time.perf_counter
        sleep = _real_time.sleep

    monkeypatch.setattr(_hygiene_module, "time", _FakeTime)
    _WALL_CLOCK.rewind(_WALL_CLOCK.read() - _WALL_EPOCH)
    return _WALL_CLOCK


def _scheduler(tmp_path, **overrides):
    settings = RuntimeHygieneSettings(
        enabled=True,
        interval_minutes=overrides.get("interval_minutes", 30),
        deep_maintenance_interval_hours=overrides.get("deep_hours", 6),
        telegram_min_interval_sec=overrides.get("tg_sec", 3600),
        dry_run=True,
    )
    sched = RuntimeCleanupScheduler(repo_root=tmp_path, settings=settings)

    class _StubWorker:
        mode = type("M", (), {"value": "AUDIT_ONLY"})()

        def run_cycle(self, _dbs):
            return {"databases": {}, "mode": "AUDIT_ONLY", "verification": "OK", "run_id": "t"}

        def status(self):
            return {"state": "IDLE"}

    sched._ensure_worker = _StubWorker  # type: ignore[method-assign]
    sched._run_initial_audit = lambda: {}
    return sched


def test_cadence_stamps_land_in_wall_domain(tmp_path, hygiene_clock) -> None:
    """The stamps the production caller compares against MUST live in the same
    clock the caller supplies (time.time()). A monotonic stamp (~boot uptime)
    compared against an epoch timestamp is permanently 'due'."""
    sched = _scheduler(tmp_path)
    assert sched.run_cycle(deep=True) is not None
    stamp = hygiene_clock.read()
    assert sched._last_light == stamp, (
        f"_last_light must be the deterministic WALL stamp ({stamp!r}); a "
        "monotonic stamp (~boot uptime) vs the caller's epoch now_t is the "
        "permanently-due BUG-275 gate"
    )
    assert sched._last_deep == stamp, f"_last_deep must be WALL, got {sched._last_deep!r}"


def test_deep_gate_respects_interval_after_fix(tmp_path, hygiene_clock) -> None:
    """The exact production shape: MaintenanceCycle passes its wall now_t.
    Pre-fix, a wall comparison against the monotonic stamp was True
    immediately after a deep cycle (deep index scans every 30min light pass)."""
    sched = _scheduler(tmp_path)
    sched.run_cycle(deep=True)
    # 30 min later: light due, deep NOT due
    now_wall = hygiene_clock.advance(1800.0)
    assert sched.is_light_due(now_wall) is True
    assert sched.is_deep_due(now_wall) is False, (
        "deep cadence broken: monotonic stamp vs wall now_t => permanently due"
    )
    # 7h after the deep cycle the deep gate must open again
    assert sched.is_deep_due(hygiene_clock.advance(6 * 3600.0)) is True


def test_telegram_cooldown_default_stamp_is_wall(tmp_path, hygiene_clock) -> None:
    sched = _scheduler(tmp_path)
    sched.mark_telegram_sent()  # no explicit now -> scheduler's own stamp
    assert sched._last_telegram == hygiene_clock.read(), (
        f"mark_telegram_sent default must stamp WALL ({sched._last_telegram!r}); "
        "maintenance compares is_telegram_due(now_wall)"
    )
    assert sched.is_telegram_due(hygiene_clock.advance(1800.0)) is False
    assert sched.is_telegram_due(hygiene_clock.advance(3601.0)) is True


def test_next_light_in_and_status_are_sane(tmp_path, hygiene_clock) -> None:
    sched = _scheduler(tmp_path)
    sched.run_cycle(deep=False)
    # default now: same domain as the fresh stamp -> within one interval
    wait = sched.next_light_in()
    assert 0.0 <= wait <= sched.light_interval_sec
    st = sched.status()
    assert 0.0 <= st["next_light_in_sec"] <= sched.light_interval_sec


def test_maintenance_caller_uses_wall_now_for_hygiene_gates(tmp_path) -> None:
    """Wiring pin (md7 class): the engine-side caller supplies time.time() to
    the hygiene gates; the scheduler contract above must stay in that domain.
    If either side switches clocks, this pin + the behavior tests fail together."""
    import inspect

    from nexus_scalp.application.live import maintenance as maint_mod
    from nexus_scalp.hygiene import hygiene_runtime as hy

    maint_src = inspect.getsource(maint_mod.MaintenanceCycle.run_cycle)
    assert "now_t = time.time()" in maint_src
    assert "is_deep_due(now_t)" in maint_src
    hy_src = inspect.getsource(hy.RuntimeCleanupScheduler)
    assert "self._last_deep = time.monotonic()" not in hy_src
    assert "self._last_light = time.monotonic()" not in hy_src


# ===========================================================================.
# ML-QA-015: the cadence boundary is the comparison, not the clock
#
# Every gate is ``(now - stamp) >= interval``. Both edges of that inclusive
# comparison are now exercised against fixed values: one tick SHORT of the
# interval must still be NOT due, and exactly AT the interval must be DUE
# (equality satisfies >=). Under the old live ``time.time()`` reads the two
# edges were indistinguishable — a 6-hour deep interval could only be crossed
# by waiting 6 hours, so only the NOT-due side was ever exercised.
# ===========================================================================.


def test_light_gate_both_edges_of_the_inclusive_boundary(tmp_path, hygiene_clock) -> None:
    """``is_light_due`` at exactly the interval is DUE (``>=``); one tick short
    is NOT due."""
    sched = _scheduler(tmp_path)
    sched.run_cycle(deep=False)
    light = sched.light_interval_sec
    assert light > 0.0
    assert sched.is_light_due(hygiene_clock.read() + light - 0.001) is False, (
        "one tick short of the light interval the gate must stay closed"
    )
    assert sched.is_light_due(hygiene_clock.read() + light) is True, (
        "exactly at the light interval the gate must open (inclusive >=)"
    )


def test_deep_gate_both_edges_of_the_inclusive_boundary(tmp_path, hygiene_clock) -> None:
    """``is_deep_due`` at exactly the deep interval is DUE; one tick short is
    NOT. Pre-ML-QA-015 this edge had never been exercised: reaching it needed
    a real 6-hour wait."""
    sched = _scheduler(tmp_path)
    sched.run_cycle(deep=True)
    deep = sched.deep_interval_sec
    assert deep > 0.0
    assert sched.is_deep_due(hygiene_clock.read() + deep - 0.001) is False, (
        "one tick short of the deep interval the gate must stay closed"
    )
    assert sched.is_deep_due(hygiene_clock.read() + deep) is True, (
        "exactly at the deep interval the gate must open (inclusive >=)"
    )


def test_telegram_gate_both_edges_of_the_inclusive_boundary(tmp_path, hygiene_clock) -> None:
    """``is_telegram_due`` at exactly the cooldown is DUE; one tick short is
    NOT."""
    sched = _scheduler(tmp_path)
    sched.mark_telegram_sent()
    cd = sched.settings.telegram_min_interval_sec
    assert cd > 0.0
    assert sched.is_telegram_due(hygiene_clock.read() + cd - 0.001) is False
    assert sched.is_telegram_due(hygiene_clock.read() + cd) is True


def test_next_light_in_counts_down_to_the_exact_boundary(tmp_path, hygiene_clock) -> None:
    """``next_light_in`` must report the exact remainder to the interval, so
    an operator's reported wait tracks the boundary the gate actually uses."""
    sched = _scheduler(tmp_path)
    sched.run_cycle(deep=False)
    light = sched.light_interval_sec
    assert sched.next_light_in(hygiene_clock.read()) == light
    assert sched.next_light_in(hygiene_clock.advance(light)) == 0.0
    # clamped at 0 once past the boundary (never a negative wait)
    assert sched.next_light_in(hygiene_clock.advance(60.0)) == 0.0


def test_a_light_cycle_advances_only_the_light_stamp(tmp_path, hygiene_clock) -> None:
    """A light (non-deep) cycle must refresh ``_last_light`` and leave
    ``_last_deep`` alone, otherwise a light pass would silently reset the deep
    cadence and the deep branch would never fire twice."""
    sched = _scheduler(tmp_path)
    sched.run_cycle(deep=True)
    deep_stamp = sched._last_deep
    hygiene_clock.advance(3600.0)
    sched.run_cycle(deep=False)
    assert sched._last_light > deep_stamp
    assert sched._last_deep == deep_stamp, "a light cycle must not touch _last_deep"


def test_deep_cycle_refreshes_both_stamps(tmp_path, hygiene_clock) -> None:
    sched = _scheduler(tmp_path)
    sched.run_cycle(deep=True)
    light_stamp = sched._last_light
    assert sched._last_deep == light_stamp
    later = hygiene_clock.advance(7200.0)
    sched.run_cycle(deep=True)
    assert sched._last_light == later
    assert sched._last_deep == later
    assert sched._last_light > light_stamp


def test_initial_zero_stamps_make_every_gate_due_immediately(tmp_path, hygiene_clock) -> None:
    """Fresh construction (all stamps 0.0) is the first-boot path: every gate
    must be due at any realistic wall epoch, which is exactly why a monotonic
    stamp was also 'due' — the contract distinguishes them by DOMAIN."""
    sched = _scheduler(tmp_path)
    assert sched._last_light == 0.0
    assert sched._last_deep == 0.0
    assert sched._last_telegram == 0.0
    now = hygiene_clock.read()
    assert sched.is_light_due(now) is True
    assert sched.is_deep_due(now) is True
    assert sched.is_telegram_due(now) is True
