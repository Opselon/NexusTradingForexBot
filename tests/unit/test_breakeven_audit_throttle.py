"""Regression tests for the BREAKEVEN_FAILED audit spam root cause.

The bug: `_log_throttled_be_failure` throttled the CONSOLE log to once per
TELEMETRY_CONSOLE_INTERVAL_SEC per ticket, but wrote the audit row on every
call. The engine calls it from `apply_breakeven_lock` on every management tick
when the broker keeps rejecting/deferring the modification, so one stuck ticket
produced hundreds of audit rows — one per tick, for hours.

The fix throttles BOTH the console log and the audit record by the same
per-ticket interval, so a persistent rejection produces at most one
BREAKEVEN_FAILED audit row per interval instead of one per tick.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nexus_scalp.execution.lifecycle.protection import ProtectionEngine
from nexus_scalp.execution.protection_ledger import PositionProtectionState


def _make_engine(interval_sec: float):
    """Build a ProtectionEngine whose om composition root is a mock.

    `_om_protection_symbols()` returns a tuple of tunables indexed positionally;
    index 13 is TELEMETRY_CONSOLE_INTERVAL_SEC. We patch that function so the
    test does not depend on the live production constants.
    """
    engine = ProtectionEngine.__new__(ProtectionEngine)
    om = MagicMock()
    om._log_protection_audit = MagicMock()
    engine.om = om
    engine._tunable_interval = interval_sec
    return engine


def _patch_symbols(monkeypatch, interval_sec: float):
    """Point _om_protection_symbols at a tuple whose index 13 is the interval."""
    t = tuple(interval_sec if i == 13 else 0 for i in range(20))
    monkeypatch.setattr(
        "nexus_scalp.execution.lifecycle.protection._om_protection_symbols",
        lambda: t,
    )


def _pos(ticket: int = 777):
    return SimpleNamespace(
        ticket=ticket,
        symbol="XAUUSD",
        price_open=2350.0,
        sl=2340.0,
        tp=2370.0,
        volume=0.10,
        profit=12.5,
    )


def test_throttle_emits_once_per_interval(monkeypatch):
    """Repeated ticks inside ONE interval window write exactly one audit row."""
    _patch_symbols(monkeypatch, 60.0)
    engine = _make_engine(60.0)
    state = PositionProtectionState()

    base = 1000.0
    for i in range(50):  # 50 management ticks, all inside the window
        tick_at = base + i
        monkeypatch.setattr("time.monotonic", lambda tick_at=tick_at: tick_at)
        engine._log_throttled_be_failure(state, _pos(), "BREAKEVEN DEFERRED: stuck", 2345.0)

    assert engine.om._log_protection_audit.call_count == 1, (
        f"expected 1 audit row per interval, got {engine.om._log_protection_audit.call_count}"
    )


def test_throttle_emits_again_after_interval_elapses(monkeypatch):
    """A second call after the interval passes is NOT suppressed (real failure still audited)."""
    _patch_symbols(monkeypatch, 60.0)
    engine = _make_engine(60.0)
    state = PositionProtectionState()

    clock = {"t": 1000.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["t"])
    engine._log_throttled_be_failure(state, _pos(), "first", 2345.0)
    assert engine.om._log_protection_audit.call_count == 1

    clock["t"] = 1000.0 + 61.0  # next window
    engine._log_throttled_be_failure(state, _pos(), "second", 2346.0)
    assert engine.om._log_protection_audit.call_count == 2


def test_throttle_is_per_ticket(monkeypatch):
    """Two different tickets each get their own window; neither starves the other."""
    _patch_symbols(monkeypatch, 60.0)
    engine = _make_engine(60.0)
    s1 = PositionProtectionState()
    s2 = PositionProtectionState()

    clock = {"t": 1000.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["t"])
    engine._log_throttled_be_failure(s1, _pos(101), "ticket 1", 2345.0)
    engine._log_throttled_be_failure(s2, _pos(202), "ticket 2", 2345.0)
    assert engine.om._log_protection_audit.call_count == 2


def test_throttle_does_not_suppress_the_first_failure(monkeypatch):
    """The first-ever failure for a ticket is always audited (no silent drop)."""
    _patch_symbols(monkeypatch, 60.0)
    engine = _make_engine(60.0)
    state = PositionProtectionState()
    # start the clock well past t=0 so the first call is genuinely inside a window
    monkeypatch.setattr("time.monotonic", lambda: 1000.0)
    engine._log_throttled_be_failure(state, _pos(), "initial", 2345.0)
    assert engine.om._log_protection_audit.call_count == 1
    # the audit row carries the real failure data, not a placeholder
    _, kwargs = engine.om._log_protection_audit.call_args
    assert kwargs["action"] == "BREAKEVEN_FAILED"
    assert kwargs["stop_loss"] == 2345.0


def test_retry_storm_cannot_rebuild(monkeypatch):
    """The exact live pattern: one ticket, many ticks, one interval.

    Before the fix this produced up to 377 audit rows per ticket (6,685 total
    from 190 tickets). After the fix the same input yields one row per interval.
    """
    _patch_symbols(monkeypatch, 30.0)
    engine = _make_engine(30.0)
    state = PositionProtectionState()

    ticks = 400  # far more ticks than the live storm
    for i in range(ticks):
        tick_at = 0.0 + i * 0.25
        monkeypatch.setattr("time.monotonic", lambda tick_at=tick_at: tick_at)
        engine._log_throttled_be_failure(state, _pos(), "persistent rejection", 2345.0 + i * 0.01)

    # 400 ticks * 0.25s = 100s of wall time; at a 30s interval that is 4 windows
    # (the tick at t=0 plus 3 more), never hundreds.
    n = engine.om._log_protection_audit.call_count
    assert n <= 5, f"throttle failed: {n} audit rows for one ticket in 100s"
    assert n >= 1, "throttle must not silently drop the first failure"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-o", "addopts="]))
