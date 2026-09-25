"""RT-009: boot safety-state gate must fail closed on EVERY non-RUNNING state.

Live defect (2026-09-25 04:11 IST boot log)::

    04:11:01 [error] breaker anchor read FAILED (treated as absent)
    04:11:01 [error] runtime_risk_state persist FAILED state=RUNNING
    04:11:01 [info     ] [SAFETY_STATE] boot decision=RUNNING (trading permitted)

The persisted safety row could not be read, yet boot armed the trading loop
with RUNNING. ``_restore_runtime_risk_state`` already resolves a failed read
to ``DB_READ_UNCERTAIN`` (fail closed), but ``_apply_persisted_halt`` had a
WHITELIST of refused states — ``("HALTED", "KILL_SWITCH")`` — so the
uncertain state fell through the arm and the engine traded over an unreadable
audit store.

The fix replaces the whitelist with the only safe rule: refuse unless the
decision is RUNNING. Every non-RUNNING state resolved by
``resolve_boot_decision`` is therefore refused by construction, including the
unknown-state fail-closed branch and the read-failure state. First boot stays
safe: a missing row resolves to RUNNING.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.risk.runtime_safety import (
    BootDecision,
    PersistedRiskState,
    resolve_boot_decision,
)


def _noop(*args: Any, **kwargs: Any) -> None:
    return


def _assert_equal(actual: Any, expected: Any) -> None:
    assert actual == expected, f"expected {expected!r}, got {actual!r}"


class _MinimalEngine(LiveEngine):
    """LiveEngine with only the boot-safety surface wired.

    Constructing a full LiveEngine blocks (it boots workers, the web server
    and a broker connection); the restore-first contract under test needs only
    ``_apply_persisted_halt`` and the risk-state readers.
    """

    def __init__(self) -> None:
        # Do NOT call super().__init__(): this test targets the boot safety
        # gate, which must not depend on any subsystem being constructed.
        self._runtime_risk_state = "RUNNING"
        self._runtime_risk_detail = ""
        self._feed_stall_escalated = False
        self._running = False
        self._halt_reason = ""
        # The refused path emits an incident + notification; both are fully
        # suppressed by _apply_persisted_halt, but the attributes must exist.
        self._incident_telemetry = None
        self.notifier = SimpleNamespace(notify_kill_switch_activated=_noop)


# ---------------------------------------------------------------------------
# resolve_boot_decision — the safety verdict the gate must honour
# ---------------------------------------------------------------------------


def test_missing_row_is_running_first_boot_safe() -> None:
    d = resolve_boot_decision(None)
    assert d.trading_allowed is True
    _assert_equal(d.state, "RUNNING")


def test_running_row_is_running() -> None:
    d = resolve_boot_decision(PersistedRiskState(state="RUNNING"))
    assert d.trading_allowed is True
    _assert_equal(d.state, "RUNNING")


def test_halted_row_refuses_trading() -> None:
    d = resolve_boot_decision(PersistedRiskState(state="HALTED", reason="Max drawdown exceeded"))
    assert d.trading_allowed is False
    _assert_equal(d.state, "HALTED")


def test_kill_switch_row_refuses_trading() -> None:
    d = resolve_boot_decision(PersistedRiskState(state="KILL_SWITCH"))
    assert d.trading_allowed is False
    _assert_equal(d.state, "KILL_SWITCH")


def test_unknown_state_fails_closed() -> None:
    # PersistedRiskState validates its state against the known set, so an
    # unrecognized persisted value surfaces through the UNKNOWN branch of
    # resolve_boot_decision only when a future state is added to the enum
    # without a matching branch. Exercise that branch directly.
    d = resolve_boot_decision(SimpleNamespace(state="SOMETHING_NEW"))
    assert d.trading_allowed is False
    _assert_equal(d.state, "SOMETHING_NEW")


def test_unknown_persisted_row_state_is_refused() -> None:
    # The enum guard itself: a row carrying an unrecognized state must not
    # construct a PersistedRiskState (the loader treats it as unreadable
    # instead of silently accepting an untrusted state).
    with pytest.raises(ValueError):
        PersistedRiskState(state="SOMETHING_NEW")


# ---------------------------------------------------------------------------
# _apply_persisted_halt — the arm gate (the RT-009 fix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["HALTED", "KILL_SWITCH", "DB_READ_UNCERTAIN"])
def test_apply_persisted_halt_refuses_every_known_non_running_state(state: str) -> None:
    e = _MinimalEngine()
    e._apply_persisted_halt(BootDecision(trading_allowed=False, state=state, detail="why"))
    _assert_equal(e._runtime_risk_state, state)
    # The loop is NOT armed: restore-first contract.
    assert e._running is False


def test_apply_persisted_halt_refuses_unknown_state() -> None:
    # resolve_boot_decision fails closed on an unrecognized state; the gate
    # must honour that verdict instead of re-deriving its own whitelist.
    e = _MinimalEngine()
    e._apply_persisted_halt(
        BootDecision(trading_allowed=False, state="UNRECOGNIZED", detail="fail closed")
    )
    _assert_equal(e._runtime_risk_state, "UNRECOGNIZED")
    assert e._running is False


def test_apply_persisted_halt_only_arms_on_running() -> None:
    e = _MinimalEngine()
    e._apply_persisted_halt(BootDecision(trading_allowed=True, state="RUNNING", detail=""))
    _assert_equal(e._runtime_risk_state, "RUNNING")
    # Arming the loop is the caller's job (run_loop), not the gate's; the gate
    # only refuses. Verify it did not touch _running.
    assert e._running is False


# ---------------------------------------------------------------------------
# _is_trading_blocked — the runtime gate must agree with the boot gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["HALTED", "KILL_SWITCH", "DB_READ_UNCERTAIN"])
def test_trading_blocked_on_non_running_boot_state(state: str) -> None:
    # The boot gate refuses these states; the runtime gate must not let a
    # background path place new entries behind a decision boot refused.
    e = _MinimalEngine()
    e._runtime_risk_state = state
    assert e._trading_blocked_by_safety_state() is True


def test_trading_allowed_on_running() -> None:
    e = _MinimalEngine()
    e._runtime_risk_state = "RUNNING"
    e._feed_stall_escalated = False
    assert e._trading_blocked_by_safety_state() is False


def test_trading_blocked_on_feed_stall_escalation() -> None:
    # Unrelated to RT-009 but the same gate: a CRITICAL stall escalation must
    # still block new entries (self-clears on the first fresh tick).
    e = _MinimalEngine()
    e._runtime_risk_state = "RUNNING"
    e._feed_stall_escalated = True
    assert e._trading_blocked_by_safety_state() is True


# ---------------------------------------------------------------------------
# Source-level guard: the whitelist must not come back
# ---------------------------------------------------------------------------


def test_apply_persisted_halt_has_no_state_whitelist() -> None:
    # The old code was ``if decision.state in ("HALTED", "KILL_SWITCH")``. The
    # only safe rule is refuse-unless-RUNNING, so the whitelist membership test
    # must not reappear in the arm path.
    import inspect

    src = inspect.getsource(LiveEngine._apply_persisted_halt)
    assert 'in ("HALTED"' not in src, (
        "the boot gate must refuse every non-RUNNING state, not a whitelist"
    )
    assert '!= "RUNNING"' in src or "!= 'RUNNING'" in src, "the boot gate must arm ONLY on RUNNING"
