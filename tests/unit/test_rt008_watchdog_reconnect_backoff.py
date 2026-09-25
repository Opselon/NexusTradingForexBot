"""RT-008: watchdog reconnect storm (live defect, 2026-09-25 04:11 IST).

Production evidence (live log)::

    04:11:01 [MT5_CONNECT] event=ATTEMPT attempt=1/3 -> Successfully connected
    ... engine runs, ticks flow ...
    04:11:33 [WARNING] Tick stream stalled and MT5 disconnected -> auto-reconnect
    04:11:33 MetaTrader 5 IPC connection closed.
    04:11:39 [MT5_CONNECT] RETRY 1/3 retcode=(-10005,'IPC timeout')
    04:11:44 [MT5_CONNECT] RETRY 2/3 retcode=(-10005,'IPC timeout')
    04:11:50 Failed to initialize ... after 3 attempts (-10005)
    04:11:50 [error] Error in live loop ... 'adapter not connected'
    <next ~0.05s pass: the SAME reconnect fires again>

Root cause: the stall watchdog arms on ``_stall_age_sec > 15.0`` where the
stall clock is ``_last_fresh_tick_at``. After a *failed* reconnect the loop
resets ``_last_tick_processed_time`` but never ``_last_fresh_tick_at`` —
correctly, since only a NEW non-duplicate tick may (BUG-279). So the stall
clock stays old and the watchdog re-fires on essentially every ~0.05s pass,
calling ``adapter.disconnect() + connect()`` each time. Each ``connect()``
drives its own ``mt5.initialize()`` retry ladder, and the storm on the single
process-global MT5 IPC handle yields IPC timeout -10005.

The fix bounds the RECONNECT only: base 5s, doubling per consecutive failure,
capped 300s. The stall itself stays reported every pass and ticks stay polled
every pass, so recovery is detected immediately.

These tests drive the REAL ``RuntimeLoop.run()`` with an injected clock and a
single scripted adapter whose state machine is the phase list — no new
production helpers to hide behind, no real sleeps, no network and no MT5
terminal. With the fix reverted, every test in the storm/backoff group fails
with the actual storm (one reconnect per ~0.05s pass, no backoff state).
"""

from __future__ import annotations

import asyncio
import contextlib
from itertools import pairwise
from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.application.live.runtime_loop import RuntimeLoop

# RT-008 added the backoff constants; import them resiliently so these tests
# still EXECUTE against a pre-fix tree (the A/B RED gate) rather than failing
# at import. On such a tree they are absent, the loop has no bound at all, and
# the storm assertions below must fail on their own merits.
try:
    from nexus_scalp.application.live.runtime_loop import (
        RECONNECT_BACKOFF_BASE_SEC,
        RECONNECT_BACKOFF_CAP_SEC,
        _reconnect_backoff_sec,
    )
except ImportError:  # pre-RT-008 tree
    RECONNECT_BACKOFF_BASE_SEC = 5.0
    RECONNECT_BACKOFF_CAP_SEC = 300.0

    def _reconnect_backoff_sec(consecutive_failures: int) -> float:  # type: ignore[misc]
        return 0.0


def _assert_equal(actual: Any, expected: Any) -> None:
    assert actual == expected, f"expected {expected!r}, got {actual!r}"


class _Clock:
    """Injected wall clock. time.time() and asyncio.sleep() both route here."""

    now: float = 1000.0

    @staticmethod
    def advance(dt: float) -> None:
        _Clock.now += max(dt, 0.0)


class _ScriptedAdapter:
    """One adapter whose behaviour is a function of simulated time.

    Phases are ``(start_at, kind, connect_ok)``; kind is:

    * ``"stall"``  — transport down (is_connected() False): the storm case
    * ``"quiet"``  — transport up, no new ticks (BUGFIX-G29 case)
    * ``"fresh"``  — transport up and a NEW non-duplicate tick every pass

    ``connect()`` returns the phase's ``connect_ok`` — the handshake result —
    which is deliberately separate from ``is_connected()``: production shows
    MT5 reporting connected while zero new ticks arrive, and a reconnect
    attempt can fail while the transport is already back up.

    Boot must SUCCEED (RuntimeLoop.run() makes up to 3 boot connect() attempts
    and returns if all fail, never reaching the watchdog), so the phase at
    t=0 must have ``connect_ok=True``; a stall then begins slightly later.
    """

    def __init__(self, phases: list[tuple[float, str, bool]]) -> None:
        # Phase times are expressed relative to the boot instant, which is
        # _Clock.now at construction — translate them to absolute simulated
        # time so the first driver tick (boot) always lands in phase 0.
        _boot = _Clock.now
        self._phases = sorted((start + _boot, k, o) for start, k, o in phases)
        self.connect_calls: list[float] = []
        self.disconnect_calls: list[float] = []
        self.raise_on_connect = False
        self.resubscribe_calls: list[float] = []
        self._first_connect_t: float | None = None
        self._bid: float = 100.0
        # The tick timestamp is frozen outside "fresh" phases so the loop's
        # BUG-169 duplicate guard sees a repeated quote (no new information).
        self._frozen_ts: float = 999.0

    def _phase_at(self, t: float) -> tuple[str, bool]:
        kind, ok = "stall", False
        for start, k, o in self._phases:
            if t >= start:
                kind, ok = k, o
        return kind, ok

    # -- separation of boot vs watchdog attempts -----------------------------

    @property
    def watchdog_connects(self) -> list[float]:
        """connect() attempts made by the stall watchdog, excluding boot.

        Boot runs at the initial clock instant; every attempt strictly after
        that instant is a watchdog reconnect attempt.
        """
        if self._first_connect_t is None:
            return []
        return [t for t in self.connect_calls if t > self._first_connect_t]

    # -- adapter surface used by RuntimeLoop ---------------------------------

    def is_connected(self) -> bool:
        kind, _ = self._phase_at(_Clock.now)
        return kind in ("quiet", "fresh")

    def connect(self) -> bool:
        self.connect_calls.append(_Clock.now)
        if self._first_connect_t is None:
            # Record the boot handshake instant: RuntimeLoop.run() makes up to
            # 3 boot connect() attempts synchronously at startup, before the
            # driver advances the clock. Every later attempt is a watchdog one.
            self._first_connect_t = _Clock.now
        if self.raise_on_connect and _Clock.now > self._first_connect_t:
            # Only a WATCHDOG attempt may raise: the boot ladder catches a
            # raised connect() as a failure and returns, never reaching the
            # watchdog this test exercises.
            raise RuntimeError("connect() raised — still a failed attempt")
        _, ok = self._phase_at(_Clock.now)
        return ok

    def disconnect(self) -> None:
        self.disconnect_calls.append(_Clock.now)

    def get_last_tick(self, symbol: str) -> Any:
        kind, _ = self._phase_at(_Clock.now)
        if kind == "fresh":
            # A genuinely new quote: both the timestamp and the price move.
            self._bid += 1.0
            self._frozen_ts = _Clock.now
            return SimpleNamespace(timestamp=self._frozen_ts, bid=self._bid, ask=self._bid + 0.5)
        # Stalled / quiet feed: the broker keeps repeating the LAST quote with
        # an unchanged timestamp — the BUG-169 duplicate guard treats it as
        # "no new information" and does not run the pipeline.
        return SimpleNamespace(timestamp=self._frozen_ts, bid=self._bid, ask=self._bid + 0.5)

    def get_tick(self, symbol: str) -> Any:
        return self.get_last_tick(symbol)

    def resubscribe_symbol(self, symbol: str) -> None:
        self.resubscribe_calls.append(_Clock.now)

    def get_account_info(self) -> Any:
        return SimpleNamespace(balance=1.0, equity=1.0, login=1)

    def get_symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(digits=2)

    def get_account_snapshot(self) -> Any:
        return None


async def _noop_coro(*args: Any, **kwargs: Any) -> None:
    return


def _noop(*args: Any, **kwargs: Any) -> None:
    return


class _Harness:
    """Runs the real RuntimeLoop.run() under an injected clock.

    ``prime_stall_clock_sec`` backdates ``_last_fresh_tick_at`` so the watchdog
    is already armed on the first pass (production: the engine had been
    running for minutes before the feed died). Set it to ``0.0`` for healthy
    feeds, where the stall clock must start recent so the watchdog never arms.
    """

    def __init__(
        self,
        phases: list[tuple[float, str, bool]],
        *,
        stall_age_sec: float = 20.0,
    ) -> None:
        self.adapter = _ScriptedAdapter(phases)
        self.stall_notices: list[dict[str, Any]] = []
        self.recovered: int = 0
        self.pipeline_ticks: list[Any] = []
        self.shutdown: int = 0
        # The driver backdates the stall clock on each stall transition, so the
        # episode is already older than the 15s watchdog threshold.
        self._stall_age_sec = stall_age_sec
        self.stall_starts: list[float] = []
        self.flush_starts: list[float] = []
        # Set by the driver on the fresh→stalled transition; the loop must
        # never move the stall clock away from this value during a stall.
        self.stall_backdated_at: float | None = None
        # Set by the driver on the stalled→fresh transition (the instant the
        # feed revives), so recovery tests can measure from a known point
        # rather than from a stall clock the loop is free to re-stamp.
        self._recovered_at: float | None = None

        def _process_tick_pipeline(*, tick: Any, account: Any) -> None:
            # NOTE: RuntimeLoop calls this as a bare expression (line ~584), not
            # an await — it must therefore be a PLAIN callable, never a
            # coroutine function.
            self.pipeline_ticks.append(tick)

        async def _resync(symbol: str) -> None:
            return

        async def _service_pipeline_workers(*, now_t: float) -> None:
            return

        async def _maintenance(now_t: float) -> None:
            return

        async def _shutdown_async() -> None:
            self.shutdown += 1

        self.om = SimpleNamespace(
            adapter=self.adapter,
            _running=True,
            config=SimpleNamespace(
                execution=SimpleNamespace(symbol="XAUUSD"),
                model=SimpleNamespace(model_artifact_path="model.pt"),
            ),
            _symbol_info=None,
            _account_age_max_sec=30.0,
            _account_freshness="FRESH",
            _last_account_info=SimpleNamespace(balance=1.0, equity=1.0, login=1),
            _last_account_refresh=0.0,
            _last_snapshot_refresh=0.0,
            _account_last_successful_refresh=0.0,
            _account_snapshot=None,
            _account_stale_blocked_total=0,
            _accounting_worker_started=False,
            _runtime_risk_state="RUNNING",
            _command_bus=None,
            order_manager=SimpleNamespace(
                reconcile_pending_state=lambda **kw: {
                    "pending_internal": 0,
                    "pending_broker": 0,
                    "mismatch": False,
                    "repaired": False,
                }
            ),
            _restore_runtime_risk_state=lambda: SimpleNamespace(
                trading_allowed=True, state="RUNNING", detail=""
            ),
            _update_runtime_mode=lambda: None,
            _restore_peak_equity=lambda account: None,
            _notify_startup=lambda account: None,
            _cold_start_warmup=_noop_coro,
            _bootstrap_train_if_ready=_noop_coro,
            _startup_experience_self_heal=_noop_coro,
            _start_accounting_worker=lambda: None,
            _start_history_sync_worker=lambda: None,
            _start_intelligence_worker=lambda: None,
            _start_research_worker=lambda: None,
            _start_factory_worker=lambda: None,
            _start_training_worker=lambda: None,
            _start_shadow_worker=lambda: None,
            _start_news_worker=lambda: None,
            _resync_from_broker=_resync,
            _process_tick_pipeline=_process_tick_pipeline,
            _service_pipeline_workers=_service_pipeline_workers,
            _kick_worker=lambda *a, **kw: None,
            _maintenance=SimpleNamespace(run_cycle=_maintenance),
            _shutdown_async=_shutdown_async,
            notifier=SimpleNamespace(notify_error=_noop),
            emit_incident_telemetry=_noop,
            note_tick_stream_stall=self._note_stall,
            note_tick_stream_recovered=self._note_recovered,
        )
        self.om._last_fresh_tick_at = _Clock.now
        self.om._last_tick_processed_time = _Clock.now
        # Seed the duplicate guard with the frozen quote the scripted feed
        # repeats outside "fresh" phases, so the FIRST polled tick is already a
        # duplicate (no new information) and the stall is never cleared by it.
        self.om._pipeline_last_ts = self.adapter._frozen_ts
        self.om._pipeline_last_bid = self.adapter._bid
        self.om._pipeline_last_ask = self.adapter._bid + 0.5
        self.loop = RuntimeLoop(self.om)

    def _note_stall(self, *, age_sec: float, adapter_connected: bool) -> None:
        self.stall_notices.append(
            {"age": age_sec, "connected": adapter_connected, "at": _Clock.now}
        )

    def _note_recovered(self) -> None:
        self.recovered += 1

    async def run(self, *, until: float) -> None:
        """Run the real loop until the simulated horizon (seconds) is reached."""
        import nexus_scalp.application.live.runtime_loop as module

        real_time = module.time.time
        real_sleep = module.asyncio.sleep
        # NOTE: module.asyncio IS the global asyncio module object, so patching
        # asyncio.sleep also rewrites the symbol this helper calls — resolve the
        # real sleep first and invoke it by the captured reference.
        _real_sleep = module.asyncio.sleep

        def fake_time() -> float:
            return _Clock.now

        async def fake_sleep(dt: float) -> None:
            # Advance simulated time but ALWAYS yield to the event loop, so a
            # long scripted outage never starves the driving coroutine.
            _Clock.advance(max(dt, 0.0))
            await _real_sleep(0)

        module.time.time = fake_time  # type: ignore[attr-defined]
        module.asyncio.sleep = fake_sleep  # type: ignore[attr-defined]
        task: asyncio.Task[None] | None = None
        prev_kind = "fresh"
        try:
            task = asyncio.ensure_future(self.loop.run())
            deadline = _Clock.now + until
            while _Clock.now < deadline:
                if task.done():
                    break
                # Advance the clock BEFORE yielding: the loop's pass may
                # `continue` (duplicate tick / stall-with-resubscribe), so a
                # post-await advance would be skipped and the loop would spin
                # without time ever moving.
                _Clock.advance(0.05)
                # Model "the feed has ALREADY been dead for stall_age seconds"
                # at the instant the feed goes quiet/stalled: backdate the
                # stall clock once, on the transition out of "fresh". The loop
                # itself must never move it (that is the BUG-279 invariant
                # under test).
                kind, _ok = self.adapter._phase_at(_Clock.now)
                if kind != "fresh" and prev_kind == "fresh":
                    self.om._last_fresh_tick_at = _Clock.now - self._stall_age_sec
                    self.stall_backdated_at = self.om._last_fresh_tick_at
                if kind == "fresh" and prev_kind != "fresh":
                    self._recovered_at = _Clock.now
                prev_kind = kind
                await _real_sleep(0)  # let the loop body run a pass
            self.om._running = False
            await _real_sleep(0)
        finally:
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=5.0)
            module.time.time = real_time
            module.asyncio.sleep = real_sleep


# All stall scenarios need boot to SUCCEED, then the transport to drop.
# RuntimeLoop.run() runs its boot connect() synchronously on the first step
# (before the driver advances the clock), so the phase at t=0 must have
# connect_ok=True and a healthy stream; the stall then begins at t=0.05.
#
# NOTE: boot must be "fresh", not "quiet". A "quiet" boot leaves the duplicate
# guard's seen-quote equal to the frozen quote the feed keeps repeating, which
# is what makes the stall honest — but the boot pass itself then treats the
# very first polled tick as NEW (the guard compares against this seed and the
# feed has not yet produced anything else), which runs the pipeline once and
# wrongly clears the stall clock. "fresh" at t=0 makes the boot tick a
# genuinely new quote so the guard is seeded correctly, and the stall is
# honest from t=0.05 onward.
_STALL = [(0.0, "fresh", True), (1.0, "stall", False)]


def _watchdog_connects_after_boot(h: _Harness) -> list[float]:
    """Boot attempts plus the first watchdog attempt are all in one window when
    the stall begins immediately, so measure from the FIRST POST-BOOT attempt.
    """
    return h.adapter.watchdog_connects


def _quiet_phases(seconds_of_quiet: float) -> list[tuple[float, str, bool]]:
    """A quiet feed that first ticks normally, then goes quiet."""
    return [(0.0, "fresh", True), (0.05, "quiet", True)]


# ---------------------------------------------------------------------------
# Backoff schedule unit
# ---------------------------------------------------------------------------


def test_reconnect_backoff_schedule_is_base_x2_capped() -> None:
    _assert_equal(_reconnect_backoff_sec(0), 0.0)
    _assert_equal(_reconnect_backoff_sec(1), RECONNECT_BACKOFF_BASE_SEC)
    _assert_equal(_reconnect_backoff_sec(2), RECONNECT_BACKOFF_BASE_SEC * 2)
    _assert_equal(_reconnect_backoff_sec(3), RECONNECT_BACKOFF_BASE_SEC * 4)
    # 2**(k-1) * 5 exceeds the cap well before k=10 (5*512=2560 > 300).
    _assert_equal(_reconnect_backoff_sec(10), RECONNECT_BACKOFF_CAP_SEC)
    _assert_equal(_reconnect_backoff_sec(1000), RECONNECT_BACKOFF_CAP_SEC)


# ---------------------------------------------------------------------------
# The storm itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failing_connect_reconnects_once_per_backoff_window() -> None:
    # 6s of dead feed with a connect() that always fails. Without the bound the
    # watchdog reconnects every ~0.05s pass (the storm); with it there is ONE
    # attempt, then the next is suppressed until the 5s window elapses.
    h = _Harness(_STALL)
    await h.run(until=6.0)
    connects = h.adapter.watchdog_connects
    assert connects, "the watchdog must still attempt a reconnect"
    in_first_window = [t for t in connects if t - connects[0] < RECONNECT_BACKOFF_BASE_SEC - 0.5]
    assert len(in_first_window) <= 1, (
        f"reconnect storm: {len(in_first_window)} watchdog connect() calls in one "
        f"{RECONNECT_BACKOFF_BASE_SEC}s backoff window (times={connects[:6]})"
    )


@pytest.mark.asyncio
async def test_backoff_escalates_across_consecutive_failures() -> None:
    # 20s of dead feed: base 5s, then 10s, then 20s -> at most 3-4 attempts
    # total, each in its own escalating window. Unbounded behaviour is ~400.
    h = _Harness(_STALL)
    await h.run(until=20.0)
    connects = h.adapter.watchdog_connects
    assert len(connects) <= 4, (
        f"expected one attempt per escalating window, got {len(connects)} (times={connects})"
    )
    gaps = [b - a for a, b in pairwise(connects)]
    assert gaps == sorted(gaps), f"backoff must escalate monotonically: {gaps}"
    assert all(g > RECONNECT_BACKOFF_BASE_SEC - 1.0 for g in gaps), (
        f"backoff did not escalate: gaps={gaps}"
    )


# ---------------------------------------------------------------------------
# Recovery contract — the bound must never starve recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_fresh_tick_resets_backoff_and_recovers_immediately() -> None:
    # 8s of dead feed (escalated backoff), then the feed revives at t=8. A NEW
    # tick on the very next pass must reset the backoff state and reach the
    # pipeline — recovery is never starved by the bound.
    h = _Harness([*_STALL, (8.0, "fresh", True)])
    await h.run(until=11.0)
    assert h.pipeline_ticks, "a recovered feed must reach the pipeline immediately"
    _assert_equal(h.loop._reconnect_failures, 0)
    _assert_equal(h.loop._next_reconnect_at, 0.0)
    assert h.recovered >= 1


@pytest.mark.asyncio
async def test_restored_feed_resets_backoff_for_the_next_stall() -> None:
    # Dead, recovered, dead again: the second stall's first reconnect must not
    # inherit the first episode's escalated window.
    h = _Harness([*_STALL, (6.0, "fresh", True), (9.0, "stall", False)])
    await h.run(until=18.0)
    connects = h.adapter.watchdog_connects
    # The recovery point is when the feed actually revived (the loop is free to
    # re-stamp the stall clock on a new tick, so measure from the driver's
    # known transition instant instead).
    recovered_at = h._recovered_at
    assert recovered_at is not None, "the feed must revive mid-run"
    second_episode = [t for t in connects if t > recovered_at]
    assert second_episode, "the second stall must still trigger a reconnect"
    gap = second_episode[0] - recovered_at
    assert gap <= RECONNECT_BACKOFF_BASE_SEC + 1.0, (
        f"escalated backoff leaked past recovery: gap={gap}"
    )


# ---------------------------------------------------------------------------
# BUG-279 invariant — the stall clock is single-writer
# ---------------------------------------------------------------------------


def test_reconnect_path_does_not_touch_stall_clock_in_source() -> None:
    # Structural guard: the ONLY place the loop may stamp _last_fresh_tick_at
    # is the fresh-tick path after _process_tick_pipeline. The watchdog block
    # may not reset the stall clock — bounding the reconnect must never make a
    # frozen quote look alive. The single tolerated exception is the
    # init-order guard that materialises the clock when it is still None.
    import inspect

    import nexus_scalp.application.live.runtime_loop as module

    src = inspect.getsource(module)
    body = src.split("while self.om._running:", 1)[1]
    pre, _sep, _post = body.partition("self.om._last_fresh_tick_at = time.time()")
    pre = pre.replace("self.om._last_fresh_tick_at = current_time", "<INIT-ORDER>")
    assert "_last_fresh_tick_at =" not in pre, (
        "only the fresh-tick path may stamp the stall clock (BUG-279): found an "
        "earlier assignment in the loop body"
    )
    assert pre.count("<INIT-ORDER>") <= 1, "more than one init-order stamp"


@pytest.mark.asyncio
async def test_watchdog_never_stamps_last_fresh_tick_at() -> None:
    # Behavioural guard for the same invariant: a long dead feed keeps the
    # stall clock frozen at its backdated value for the whole episode — the
    # loop itself must never advance it.
    h = _Harness(_STALL)
    await h.run(until=12.0)
    backdated = h.stall_backdated_at
    assert backdated is not None, "the driver must backdate the stall clock"
    _assert_equal(h.om._last_fresh_tick_at, backdated)
    assert len(h.stall_notices) > 1, "the stall must keep being reported every pass"


# ---------------------------------------------------------------------------
# No regression on the healthy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthy_feed_never_reconnects() -> None:
    # A feed that ticks the whole time must never arm the watchdog: every pass
    # delivers a NEW tick that re-stamps the stall clock.
    h = _Harness([(0.0, "fresh", True)])
    await h.run(until=10.0)
    # The boot handshake connect() is allowed; it is not a watchdog attempt.
    assert h.adapter.connect_calls, "the boot handshake connect() must run"
    _assert_equal(h.adapter.watchdog_connects, [])
    _assert_equal(h.adapter.disconnect_calls, [])
    assert len(h.pipeline_ticks) > 1


# ---------------------------------------------------------------------------
# Bounded state — the storm cannot return from an error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_stays_armed_and_backoff_state_is_bounded() -> None:
    # A connect() that RAISES must still be bounded (the exception path used to
    # restart the storm). 12s of dead feed with a raising connect().
    h = _Harness(_STALL)
    h.adapter.raise_on_connect = True
    await h.run(until=12.0)
    connects = h.adapter.watchdog_connects
    assert len(connects) <= 4, f"the exception path restarted the storm: {len(connects)} attempts"
    assert h.loop._reconnect_failures >= 0
    assert h.loop._next_reconnect_at >= 0.0
    assert len(h.stall_notices) > 1


@pytest.mark.asyncio
async def test_quiet_feed_reports_stall_without_reconnect_storm() -> None:
    # BUGFIX-G29 path: is_connected() True but no new ticks. The watchdog must
    # report the stall and force a resubscribe — but never hammer connect().
    h = _Harness(_quiet_phases(12.0))
    await h.run(until=12.0)
    _assert_equal(h.adapter.watchdog_connects, [])
    assert h.adapter.resubscribe_calls, "a quiet feed must force a resubscribe"
    assert len(h.stall_notices) > 1
    # And the stall clock must stay frozen at its backdated value (no fresh
    # tick arrived after boot to re-stamp it).
    _assert_equal(h.om._last_fresh_tick_at, h.stall_backdated_at)
