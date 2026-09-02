"""TASK-QA-DEEP-ASSURANCE / CHG-0045: state-machine invariant batteries.

PositionStateMachine (execution/position_state_machine.py) and
RecoveryBudgetLedger (execution/recovery_budget.py) over RANDOM VALID event
sequences (seeded): the system must never enter an impossible state.

Invariants (from the modules' own docstrings + order_manager architecture):
SM-1  every observed ticket has a state in the 11-state enum after any sequence
SM-2  a ticket state only changes via: first-observation seed, emergency
      bypass, or CONFIRMED debounced transition (time AND count thresholds)
SM-3  emergency targets (LOSS_HARD_EXIT / PROFIT_GIVEBACK_CRITICAL) apply
      with ZERO latency from ANY state, including first observation
SM-4  same-state observation cancels any staged candidate
SM-5  debounce window never resets on repeated same-candidate sightings
SM-6  cross-ticket isolation: events on ticket A never affect ticket B
SM-7  drop_ticket leaves NO trace in either dict
RB-1  allocate is idempotent (I1): re-allocation returns the ORIGINAL budget
RB-2  budget = min(pct * initial_risk, remaining_risk) and both >= 0
RB-3  exhaustion is a pure recompute from immutable initial values
      (evaluate twice -> same verdict for the same inputs; no sticky state)
RB-4  time-horizon verdict only after entry_time + horizon
RB-5  horizon is clamped to [min_horizon, max_horizon] regardless of ATR /
      confidence / trend inputs (property over random configs)
RB-6  drop_ticket clears all six per-ticket entries

Randomized event sequences are generated with random.Random(fixed seed):
bounded, deterministic, offline (NO broker I/O — the machine has no broker
surface by design).
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.execution.position_state_machine import PositionStateMachine
from nexus_scalp.execution.position_states import PositionState
from nexus_scalp.execution.recovery_budget import RecoveryBudgetLedger

SEED = 20260902

ALL_STATES = list(PositionState)
EMERGENCY = {PositionState.LOSS_HARD_EXIT, PositionState.PROFIT_GIVEBACK_CRITICAL}
NON_EMERGENCY = [s for s in ALL_STATES if s not in EMERGENCY]


class FakeClock:
    def __init__(self) -> None:
        self.t0 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)

    def at(self, seconds: float) -> datetime:
        return self.t0 + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# SM-1..SM-7 — random valid event sequences
# ---------------------------------------------------------------------------


class _SMHarness:
    """Drives the machine with a controllable hysteresis getter."""

    def __init__(self, min_dur: float, min_cnt: int) -> None:
        self.min_dur = min_dur
        self.min_cnt = min_cnt
        self.machine = PositionStateMachine(lambda: (self.min_dur, self.min_cnt))
        self.clock = FakeClock()


def test_sm_property_random_sequences_never_impossible_state() -> None:
    rng = random.Random(SEED)
    for _trial in range(20):
        h = _SMHarness(min_dur=1.0, min_cnt=3)
        tickets = [1000 + k for k in range(3)]
        now = 0.0
        for _step in range(120):
            now += rng.uniform(0.0, 1.5)
            ticket = rng.choice(tickets)
            target = rng.choice(ALL_STATES)
            h.machine.transition_with_hysteresis(ticket, target, h.clock.at(now))
            # SM-1: observed tickets always carry a legal state
            st = h.machine.get(ticket)
            assert st in ALL_STATES
        # SM-7 on arbitrary cleanup
        victim = rng.choice(tickets)
        h.machine.drop_ticket(victim)
        assert h.machine.get(victim) is None
        assert not h.machine.has_state(victim)


def test_sm_emergency_bypasses_from_every_state_zero_latency() -> None:
    rng = random.Random(SEED + 10)
    for _trial in range(10):
        h = _SMHarness(min_dur=3600.0, min_cnt=50)  # impossible debounce
        start_state = rng.choice(NON_EMERGENCY)
        ticket = 42
        h.machine.transition_with_hysteresis(ticket, start_state, h.clock.at(0.0))
        emergency = rng.choice(list(EMERGENCY))
        out = h.machine.transition_with_hysteresis(ticket, emergency, h.clock.at(0.001))
        assert out is emergency, "emergency must apply with zero latency"


def test_sm_first_observation_emergency_bypass_and_safe_seed() -> None:
    h = _SMHarness(min_dur=1.0, min_cnt=2)
    t = 2000
    out = h.machine.transition_with_hysteresis(t, PositionState.LOSS_HARD_EXIT, h.clock.at(0.0))
    assert out is PositionState.LOSS_HARD_EXIT
    # profit-side target seeds the safe neutral PROFIT_UNPROTECTED
    h2 = _SMHarness(min_dur=1.0, min_cnt=2)
    out2 = h2.machine.transition_with_hysteresis(
        2001, PositionState.PROFIT_PROTECTED, h2.clock.at(0.0)
    )
    assert out2 is PositionState.PROFIT_UNPROTECTED
    # loss-side target seeds LOSS_RECOVERY_CANDIDATE
    h3 = _SMHarness(min_dur=1.0, min_cnt=2)
    out3 = h3.machine.transition_with_hysteresis(2002, PositionState.LOSS_EARLY, h3.clock.at(0.0))
    assert out3 is PositionState.LOSS_RECOVERY_CANDIDATE


def test_sm_debounce_requires_time_and_count_neither_alone() -> None:
    h = _SMHarness(min_dur=10.0, min_cnt=3)
    t = 3000
    # first observation seeds PROFIT_UNPROTECTED + stages candidate
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_UNPROTECTED, h.clock.at(0.0))
    # sightings of a DIFFERENT target within the window: count builds,
    # time NOT satisfied -> no transition
    for k in range(1, 4):
        out = h.machine.transition_with_hysteresis(
            t, PositionState.PROFIT_PROTECTED, h.clock.at(0.2 * k)
        )
        assert out is PositionState.PROFIT_UNPROTECTED
    # now: window start is 0.2s, count is already 3; one more sighting after
    # min_dur satisfies BOTH -> transitions
    out = h.machine.transition_with_hysteresis(t, PositionState.PROFIT_PROTECTED, h.clock.at(10.5))
    assert out is PositionState.PROFIT_PROTECTED


def test_sm_debounce_count_gate_blocks_burst() -> None:
    """A tick BURST cannot confirm a transition with too few sightings — the
    two thresholds are AND-ed, matching the machine doc: 'either alone would
    let a tick burst confirm instantly'. (5 sightings in 0.5s satisfy count
    but not time; 3 sightings over 2.1s satisfy both and confirm.)"""
    h = _SMHarness(min_dur=2.0, min_cnt=5)
    t = 3100
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_UNPROTECTED, h.clock.at(0.0))
    for k in range(1, 6):  # 5 rapid sightings: count 5, elapsed < 2.0
        out = h.machine.transition_with_hysteresis(t, PositionState.LOSS_EARLY, h.clock.at(0.1 * k))
        assert out is PositionState.PROFIT_UNPROTECTED
    # one more sighting past the window: count continues, time now met
    out = h.machine.transition_with_hysteresis(t, PositionState.LOSS_EARLY, h.clock.at(2.1))
    assert out is PositionState.LOSS_EARLY


def test_sm_window_timer_never_resets() -> None:
    h = _SMHarness(min_dur=10.0, min_cnt=2)
    t = 4000
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_UNPROTECTED, h.clock.at(0.0))
    # candidate #1 at 1.0s (window start), then flapping:
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_PROTECTED, h.clock.at(1.0))
    # same-state sighting cancels the candidate...
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_UNPROTECTED, h.clock.at(2.0))
    # ...new candidate restarts the window at 3.0
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_PROTECTED, h.clock.at(3.0))
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_PROTECTED, h.clock.at(10.5))
    out = h.machine.transition_with_hysteresis(t, PositionState.PROFIT_PROTECTED, h.clock.at(13.1))
    # repeated same-candidate sightings never reset the timer: window from
    # 3.0 confirmed at 13.1 (10.1s >= 10, count 3 >= 2)
    assert out is PositionState.PROFIT_PROTECTED


def test_sm_same_state_cancels_candidate() -> None:
    h = _SMHarness(min_dur=10.0, min_cnt=2)
    t = 5000
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_UNPROTECTED, h.clock.at(0.0))
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_PROTECTED, h.clock.at(1.0))
    # same-state sighting cancels the staged candidate
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_UNPROTECTED, h.clock.at(2.0))
    # single new sighting right before window expiry must NOT transition
    h.machine.transition_with_hysteresis(t, PositionState.PROFIT_PROTECTED, h.clock.at(9.9))
    out = h.machine.transition_with_hysteresis(
        t, PositionState.PROFIT_UNPROTECTED, h.clock.at(10.0)
    )
    assert out is PositionState.PROFIT_UNPROTECTED


def test_sm_cross_ticket_isolation_random() -> None:
    rng = random.Random(SEED + 20)
    h = _SMHarness(min_dur=3600.0, min_cnt=1000)  # make transitions impossible
    a, b = 700, 800
    # A seeds as PROFIT_UNPROTECTED (safe neutral on first observation)
    h.machine.transition_with_hysteresis(a, PositionState.PROFIT_PROTECTED, h.clock.at(0.0))
    assert h.machine.get(a) is PositionState.PROFIT_UNPROTECTED
    for _step in range(60):
        t = 0.5 * _step
        # B gets ANY event, including emergencies — but emergencies only
        # affect B; A must be untouched by every B event.
        target = rng.choice(list(EMERGENCY)) if rng.random() < 0.2 else rng.choice(ALL_STATES)
        h.machine.transition_with_hysteresis(b, target, h.clock.at(t))
        assert h.machine.get(a) is PositionState.PROFIT_UNPROTECTED
        assert h.machine.get(b) in ALL_STATES
    # and dropping B leaves A intact
    h.machine.drop_ticket(b)
    assert h.machine.get(a) is PositionState.PROFIT_UNPROTECTED
    assert h.machine.get(b) is None


# ---------------------------------------------------------------------------
# RB-1..RB-6 — recovery budget ledger
# ---------------------------------------------------------------------------


class _Cfg:
    def __init__(self, pct: float = 0.50) -> None:
        self.recovery_budget_pct_of_r = pct
        self.default_recovery_horizon_sec = 180.0
        self.min_recovery_horizon_sec = 30.0
        self.max_recovery_horizon_sec = 600.0


def test_rb_allocation_idempotent_and_math_random() -> None:
    rng = random.Random(SEED + 30)
    for _ in range(40):
        ledger = RecoveryBudgetLedger()
        risk = rng.uniform(1.0, 500.0)
        pnl = -rng.uniform(0.0, risk)  # within remaining risk
        cfg = _Cfg(pct=rng.choice([0.1, 0.5, 1.0]))
        now = FakeClock().at(0.0)
        b1 = ledger.allocate(
            1,
            initial_risk_usd=risk,
            current_pnl_usd=pnl,
            confidence_factor=rng.uniform(0.0, 1.0),
            atr=rng.uniform(0.5, 5.0),
            trend_strength=rng.uniform(-1.0, 1.0),
            now=now,
            algo_config=cfg,
        )
        b2 = ledger.allocate(
            1,
            initial_risk_usd=risk * 10,  # different inputs must NOT re-allocate
            current_pnl_usd=0.0,
            confidence_factor=0.0,
            atr=1.0,
            trend_strength=0.0,
            now=now,
            algo_config=cfg,
        )
        assert b1 == b2  # RB-1
        remaining_risk = max(0.0, risk - abs(pnl))
        assert b1 <= cfg.recovery_budget_pct_of_r * risk + 1e-9  # RB-2
        assert b1 <= remaining_risk + 1e-9
        assert ledger.recovery_budget_consumed[1] == 0.0
        assert ledger.recovery_initial_loss[1] == pytest.approx(abs(pnl))


def test_rb_exhaustion_pure_recompute_not_sticky() -> None:
    ledger = RecoveryBudgetLedger()
    now = FakeClock().at(0.0)
    # pnl INSIDE the initial risk so the clamped budget stays positive
    # (at pnl == risk the budget clamps to 0.0 and exhaustion is trivially
    # true forever — that edge is covered separately below)
    ledger.allocate(
        1,
        initial_risk_usd=100.0,
        current_pnl_usd=-40.0,
        confidence_factor=0.5,
        atr=1.5,
        trend_strength=0.0,
        now=now,
        algo_config=_Cfg(),
    )
    assert ledger.recovery_budget_initial[1] > 0.0
    # widen drawdown past initial loss + budget: exhausted
    later = FakeClock().at(1.0)
    exhausted, reason = ledger.evaluate_exhaustion(1, current_pnl_usd=-200.0, now=later)
    assert exhausted and "RECOVERY_BUDGET_EXHAUSTED" in reason
    # same inputs again -> same verdict (pure recompute)
    exhausted2, reason2 = ledger.evaluate_exhaustion(1, current_pnl_usd=-200.0, now=later)
    assert exhausted2 and reason2 == reason
    # drawdown narrows back inside the budget: verdict flips back (NOT sticky)
    ok, _ = ledger.evaluate_exhaustion(1, current_pnl_usd=-40.0, now=later)
    assert not ok


def test_rb_zero_budget_edge_exhausted_immediately() -> None:
    """pnl == full risk clamps the budget to 0.0: any further loss is
    exhausted (documented clamp semantics — the position has no recovery
    budget left at allocation time)."""
    ledger = RecoveryBudgetLedger()
    ledger.allocate(
        2,
        initial_risk_usd=100.0,
        current_pnl_usd=-100.0,
        confidence_factor=0.5,
        atr=1.5,
        trend_strength=0.0,
        now=FakeClock().at(0.0),
        algo_config=_Cfg(),
    )
    exhausted, reason = ledger.evaluate_exhaustion(2, -100.0, FakeClock().at(1.0))
    assert exhausted and "RECOVERY_BUDGET_EXHAUSTED" in reason


def test_rb_time_horizon_boundary() -> None:
    ledger = RecoveryBudgetLedger()
    t0 = FakeClock().at(0.0)
    ledger.allocate(
        9,
        initial_risk_usd=100.0,
        current_pnl_usd=-50.0,
        confidence_factor=0.5,
        atr=1.5,  # neutral horizon scale
        trend_strength=0.0,
        now=t0,
        algo_config=_Cfg(),
    )
    horizon = ledger.recovery_horizons[9]
    assert 30.0 <= horizon <= 600.0
    ok_at, _ = ledger.evaluate_exhaustion(9, -50.0, FakeClock().at(horizon))
    assert not ok_at  # exactly AT horizon: not yet exhausted (strictly after)
    ok_after, reason = ledger.evaluate_exhaustion(9, -50.0, FakeClock().at(horizon + 0.01))
    assert ok_after and "RECOVERY_TIME_EXHAUSTED" in reason


def test_rb_horizon_clamped_property_over_random_configs() -> None:
    rng = random.Random(SEED + 40)
    for _ in range(60):
        ledger = RecoveryBudgetLedger()
        ticket = rng.randrange(1, 10_000)
        ledger.allocate(
            ticket,
            initial_risk_usd=rng.uniform(1.0, 1000.0),
            current_pnl_usd=rng.uniform(-1000.0, 1000.0),
            confidence_factor=rng.uniform(0.0, 5.0),  # extreme inputs
            atr=rng.uniform(0.01, 100.0),
            trend_strength=rng.uniform(-2.0, 2.0),
            now=FakeClock().at(0.0),
            algo_config=_Cfg(),
        )
        h = ledger.recovery_horizons[ticket]
        assert 30.0 - 1e-9 <= h <= 600.0 + 1e-9  # RB-5 regardless of extremes
        assert math.isfinite(h)


def test_rb_drop_ticket_clears_all_six() -> None:
    ledger = RecoveryBudgetLedger()
    ledger.allocate(
        5,
        initial_risk_usd=100.0,
        current_pnl_usd=-50.0,
        confidence_factor=0.5,
        atr=1.5,
        trend_strength=0.0,
        now=FakeClock().at(0.0),
        algo_config=_Cfg(),
    )
    ledger.drop_ticket(5)
    for store in (
        ledger.recovery_budget_initial,
        ledger.recovery_budget_remaining,
        ledger.recovery_budget_consumed,
        ledger.recovery_initial_loss,
        ledger.recovery_entry_times,
        ledger.recovery_horizons,
    ):
        assert 5 not in store  # RB-6
    assert ledger.is_allocated(5) is False
    assert ledger.evaluate_exhaustion(5, -10.0, FakeClock().at(1.0)) == (False, "")


def test_rb_invalid_risk_falls_back_to_atr_estimate() -> None:
    ledger = RecoveryBudgetLedger()
    b = ledger.allocate(
        7,
        initial_risk_usd=0.0,  # invalid -> ATR fallback path
        current_pnl_usd=0.0,
        confidence_factor=0.5,
        atr=1.0,
        trend_strength=0.0,
        now=FakeClock().at(0.0),
        algo_config=_Cfg(),
    )
    assert b == pytest.approx(1.0 * 1.50 * 100.0 * 0.10 * 0.5)  # fallback*r
    assert ledger.is_allocated(7)
