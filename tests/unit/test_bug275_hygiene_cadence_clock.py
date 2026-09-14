"""BUG-275 (NSE-Swarm role 6, 2026-09-14): DB-hygiene cadence clock-domain mix.

``MaintenanceCycle.run_cycle`` gates the runtime cleanup scheduler with its
wall-clock tick (``now_t = time.time()``), but ``RuntimeCleanupScheduler``
stamped ``_last_light`` / ``_last_deep`` internally with ``time.monotonic()``.
On every supported platform monotonic starts near boot (Linux/Windows/macOS:
~uptime, i.e. 1e5-1e6) while wall is ~1.79e9 — so ``is_deep_due(now_wall)``
was PERMANENTLY True: the DEEP index-scan branch (full PRAGMA scans over all
managed DBs) fired on EVERY 30-min light cycle instead of once per 6h, and
``next_light_in``/``status()`` reported nonsense across the domain boundary.

xdist-safe: pure scheduler + stubbed worker/index-scan; no real DBs, no
threads, no network.
"""

from __future__ import annotations

import time

from nexus_scalp.hygiene.hygiene_runtime import (
    RuntimeCleanupScheduler,
    RuntimeHygieneSettings,
)


def _scheduler(tmp_path, **overrides) -> RuntimeCleanupScheduler:
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


def test_cadence_stamps_land_in_wall_domain(tmp_path) -> None:
    """The stamps the production caller compares against MUST live in the same
    clock the caller supplies (time.time()). A monotonic stamp (~boot uptime)
    compared against an epoch timestamp is permanently 'due'."""
    sched = _scheduler(tmp_path)
    assert sched.run_cycle(deep=True) is not None
    epoch = time.time()
    assert sched._last_light > epoch - 3600, (
        f"_last_light must be a WALL stamp (~epoch), got {sched._last_light!r} "
        "(monotonic stamp vs the caller's time.time() now_t = permanently-due gate)"
    )
    assert sched._last_deep > epoch - 3600, f"_last_deep must be WALL, got {sched._last_deep!r}"


def test_deep_gate_respects_interval_after_fix(tmp_path) -> None:
    """The exact production shape: MaintenanceCycle passes its wall now_t.
    Pre-fix, a wall comparison against the monotonic stamp was True
    immediately after a deep cycle (deep index scans every 30min light pass)."""
    sched = _scheduler(tmp_path)
    sched.run_cycle(deep=True)
    now_wall = time.time() + 1800.0  # 30 min later: light due, deep NOT due
    assert sched.is_light_due(now_wall) is True
    assert sched.is_deep_due(now_wall) is False, (
        "deep cadence broken: monotonic stamp vs wall now_t => permanently due"
    )
    # 7h after the deep cycle the deep gate must open again
    assert sched.is_deep_due(now_wall + 6 * 3600.0) is True


def test_telegram_cooldown_default_stamp_is_wall(tmp_path) -> None:
    sched = _scheduler(tmp_path)
    sched.mark_telegram_sent()  # no explicit now -> scheduler's own stamp
    assert sched._last_telegram > time.time() - 3600, (
        f"mark_telegram_sent default must stamp WALL ({sched._last_telegram!r}); "
        "maintenance compares is_telegram_due(now_wall)"
    )
    assert sched.is_telegram_due(time.time() + 1800.0) is False
    assert sched.is_telegram_due(time.time() + 3601.0) is True


def test_next_light_in_and_status_are_sane(tmp_path) -> None:
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
