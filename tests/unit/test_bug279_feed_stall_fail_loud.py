"""BUG-279 (wave 2026-09-14): feed-stall must be FAIL-LOUD, not a silent warning loop.

Production evidence (lane 02 funnel report): the market feed froze at
2026-09-11T20:00Z. For 2.5 days the tick-stagnation watchdog logged
"[WATCHDOG] Tick stream stalled while MT5 reports connected" 1,828+ times and
emitted an MT5_TICK_STREAM_STALLED HIGH incident on EVERY 15s pass, while
``runtime_risk_state()`` kept reporting RUNNING and /health could return
READY-adjacent data — the engine looked healthy while producing ZERO
decisions. The BUG-169 duplicate-tick early-return (runtime_loop.py) skips
the whole pipeline on the frozen quote, so nothing downstream notices.

Required behavior (this test):
  * a stall keeps its remediation cadence, but the stall STATE is owned by
    the engine and escalates ONCE past a grace window (default 900s) into a
    CRITICAL operator-visible state;
  * while escalated, ``runtime_risk_state()`` reports DEGRADED (new entries
    blocked by every existing DEGRADED consumer) — never RUNNING;
  * escalation is suppressed inside the FOREX-WEEXND close window (Fri 22:00
    .. Sun 21:00 UTC, the repo's own convention in accounting/market_calendar
    semantics): a closed market is not a stalled feed;
  * incident/telemetry emissions are episode-throttled (start + escalation),
    not per-15s spam;
  * a fresh tick ends the episode and resets everything (recovery event).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace

from nexus_scalp.application.live_engine import LiveEngine

# A known WEEKDAY timestamp (2026-09-14 Monday) and weekend timestamps.
WEEKDAY_NOON = datetime(2026, 9, 14, 12, 0, tzinfo=UTC).timestamp()
WEEKEND_SAT = datetime(2026, 9, 12, 12, 0, tzinfo=UTC).timestamp()


def _surface(now: float | None = None) -> SimpleNamespace:
    """Fake engine surface for the unbound mixin methods (BUG-274 pattern:
    these methods are invoked with the composition root as state surface)."""
    s = SimpleNamespace()
    s._last_fresh_tick_at = (now if now is not None else time.time()) - 999.0
    s._feed_stall_grace_sec = 900.0
    s._feed_stall_escalated = False
    s._feed_stall_episode_started = False
    s._feed_stall_last_notify_at = 0.0
    s.emitted = []
    s.notified = []
    s._runtime_risk_state = "RUNNING"
    s._loss_freeze_active = False
    s._account_freshness = "FRESH"

    class _Circuit:
        def is_tripped(self, _now: float) -> bool:
            return False

    s._hot_path_circuit = _Circuit()
    s._now_wall = lambda: now if now is not None else time.time()

    def emit(**kw):
        s.emitted.append(kw)
        return True

    s.emit_incident_telemetry = emit

    class _Notifier:
        def notify_generic_message(self, title, message, severity="INFO", callback=None):
            s.notified.append((title, severity))
            return 1

    s.notifier = _Notifier()
    return s


def note_stall(s: SimpleNamespace, age: float, connected: bool = True) -> None:
    LiveEngine.note_tick_stream_stall(s, age_sec=age, adapter_connected=connected)


def test_below_grace_opens_episode_once_not_escalated() -> None:
    s = _surface(now=WEEKDAY_NOON)
    note_stall(s, 20.0)
    note_stall(s, 60.0)
    note_stall(s, 800.0)
    assert s._feed_stall_escalated is False
    # ONE episode-start incident, not one per call (spam fix)
    starts = [e for e in s.emitted if e.get("event_type") == "MT5_TICK_STREAM_STALLED"]
    assert len(starts) == 1
    assert starts[0]["severity"] == "HIGH"
    # state honesty: still RUNNING below grace (reconnect cadence ongoing)
    assert LiveEngine.runtime_risk_state(s) == "RUNNING"


def test_past_grace_escalates_once_critical_and_degraded() -> None:
    s = _surface(now=WEEKDAY_NOON)
    note_stall(s, 20.0)  # episode start
    note_stall(s, 950.0)  # crosses the 900s grace -> escalate
    assert s._feed_stall_escalated is True
    crit = [e for e in s.emitted if e.get("event_type") == "MT5_TICK_STREAM_STALLED_ESCALATED"]
    assert len(crit) == 1
    assert crit[0]["severity"] == "CRITICAL"
    assert any(sev == "CRITICAL" for (_t, sev) in s.notified), "operator must be notified"
    # FAIL-LOUD STATE: the canonical risk state must NOT claim RUNNING
    assert LiveEngine.runtime_risk_state(s) == "DEGRADED"
    # escalation is exactly-once per episode
    note_stall(s, 2000.0)
    assert (
        len([e for e in s.emitted if e.get("event_type") == "MT5_TICK_STREAM_STALLED_ESCALATED"])
        == 1
    )


def test_weekend_quiet_market_never_escalates() -> None:
    # evaluation instant (last-fresh + age) stays inside the weekend window
    s = _surface(now=WEEKEND_SAT)
    note_stall(s, 20.0)
    note_stall(s, 950.0)  # grace crossed, but Sat 12:00 +/- is market CLOSED
    assert s._feed_stall_escalated is False
    assert LiveEngine.runtime_risk_state(s) == "RUNNING"


def test_weekend_predicate_delegates_to_market_calendar() -> None:
    """BUG-285 (review follow-up): the engine inlined its own copy of the
    closed window while claiming market_calendar parity — a restated window
    rots the day the calendar is recalibrated. Behavior is deliberately
    PRESERVED here (the delegated predicate agrees with the old inline
    arithmetic across the whole week); what is fixed is single-ownership:
    pin (a) the source delegates and contains no restated arithmetic, and
    (b) behavioral parity with market_calendar.market_state at the edges."""
    import inspect

    src = inspect.getsource(LiveEngine._feed_stall_in_weekend)
    assert "market_state" in src, "predicate must DELEGATE to market_calendar"
    assert "weekday()" not in src and "hour >=" not in src, "no restated window arithmetic"

    edges = [
        (datetime(2026, 9, 11, 21, 59, tzinfo=UTC), False),  # Fri, pre-close
        (datetime(2026, 9, 11, 22, 0, tzinfo=UTC), True),  # Fri 22:00 close
        (datetime(2026, 9, 12, 12, 0, tzinfo=UTC), True),  # Saturday
        (datetime(2026, 9, 13, 12, 0, tzinfo=UTC), True),  # Sunday (calendar
        # keeps WEEKEND through all of Sunday; the nominal 21:00 reopen is not
        # trusted for suppression — conservative, quiet-not-stalled direction)
        (datetime(2026, 9, 14, 0, 30, tzinfo=UTC), False),  # Mon past midnight
        (datetime(2026, 9, 14, 12, 0, tzinfo=UTC), False),  # weekday noon
    ]
    for dt, want in edges:
        assert LiveEngine._feed_stall_in_weekend(dt.timestamp()) is want, dt.isoformat()


def test_recovery_resets_episode_and_can_escalate_again() -> None:
    s = _surface(now=WEEKDAY_NOON)
    note_stall(s, 20.0)
    note_stall(s, 950.0)
    assert s._feed_stall_escalated is True
    LiveEngine.note_tick_stream_recovered(s)
    assert s._feed_stall_escalated is False
    assert s._feed_stall_episode_started is False
    rec = [e for e in s.emitted if e.get("event_type") == "MT5_TICK_STREAM_RECOVERED"]
    assert len(rec) == 1
    assert LiveEngine.runtime_risk_state(s) == "RUNNING"
    # a NEW stall episode can escalate again (grace measured from recovery)
    note_stall(s, 950.0)
    assert s._feed_stall_escalated is True
    assert (
        len([e for e in s.emitted if e.get("event_type") == "MT5_TICK_STREAM_STALLED_ESCALATED"])
        == 2
    )


def test_unconnected_stall_uses_existing_reconnect_path_and_still_escalates() -> None:
    """Disconnected branch already logs + reconnects; escalation must cover it
    too (2.5-day incident included disconnected stretches)."""
    s = _surface(now=WEEKDAY_NOON)
    note_stall(s, 950.0, connected=False)
    assert s._feed_stall_escalated is True


def test_disconnected_never_reaches_here_because_age_is_wall_based() -> None:
    # Sanity: age values > grace escalate regardless of connected flag; the
    # watchdog keeps BOTH branches' remediation and feeds them the same age.
    s = _surface(now=WEEKDAY_NOON)
    note_stall(s, 901.0)
    assert s._feed_stall_escalated is True


def test_runtime_loop_tracks_last_fresh_tick_and_reports_age() -> None:
    """Loop wiring pin: _last_fresh_tick_at stamped ONLY when a NEW tick
    completes the pipeline (the BUG-169 duplicate early-return must NOT stamp
    it), and the watchdog branch reports wall-clock stall age to the engine."""
    import inspect

    from nexus_scalp.application.live.runtime_loop import RuntimeLoop

    src = inspect.getsource(RuntimeLoop.run)
    assert "_last_fresh_tick_at" in src, "loop must track last fresh-tick wall time"
    assert "note_tick_stream_stall" in src, "watchdog must report stall age to the engine"
    assert "note_tick_stream_recovered" in src, "fresh-tick path must close the episode"
    # The duplicate-tick early return happens BEFORE the pipeline call; the
    # fresh-tick stamp must sit AFTER _process_tick_pipeline in the source.
    dup_at = src.index("BUG-169 duplicate-tick")
    pipe_at = src.index("_process_tick_pipeline(")
    stamp_at = src.index("_last_fresh_tick_at = time.time()")
    assert dup_at < pipe_at < stamp_at, "stall clock must survive the dedupe early-return"


def test_escalated_stall_blocks_entry_via_safety_provider() -> None:
    """The BUG-256 dispatch entry-gate provider (wired as
    safety_state_provider into OrderLifecycleManager -> consulted by
    DispatchEngine dispatch_order/execute_order — ENTRY paths only, protective
    exits use different seams) must report BLOCKED while escalated."""
    s = _surface(now=WEEKDAY_NOON)
    assert LiveEngine._trading_blocked_by_safety_state(s) is False
    note_stall(s, 20.0)
    assert LiveEngine._trading_blocked_by_safety_state(s) is False  # below grace: cadence only
    note_stall(s, 950.0)
    assert LiveEngine._trading_blocked_by_safety_state(s) is True  # escalated: entries blocked
    LiveEngine.note_tick_stream_recovered(s)
    assert LiveEngine._trading_blocked_by_safety_state(s) is False  # self-clears
