"""Position state machine (Agent-5 P0 seam S2).

Extracted VERBATIM from execution/order_manager.py (behavior-preserving
decomposition). Owns the per-ticket in-trade lifecycle state:

    current state      : dict[int, PositionState]
    debounce candidate : dict[int, (target, first_attempt_time, count)]

Transition rules (verbatim):
    * first observation of a ticket seeds a SAFE neutral state
      (PROFIT_UNPROTECTED for profit-side targets, else LOSS_RECOVERY_CANDIDATE),
      staging the requested target as a candidate — EXCEPT emergency targets
      (LOSS_HARD_EXIT / PROFIT_GIVEBACK_CRITICAL), which bypass with zero latency
      (a restart where a leg is already past its budget must be honored at once).
    * same-state observation cancels any staged candidate.
    * emergency targets transition immediately (logged, zero latency).
    * normal transitions require BOTH min_confirmation_duration (time, window
      never resets) AND min_observation_count (sightings) — either alone would
      let a tick burst confirm instantly.

Hysteresis parameters come from the caller's algo_config via an injected
getter (dependency boundary: the machine owns state + rules, not config or
broker). NO broker I/O, NO risk decisions, NO SL/TP geometry.

USED BY: execution/order_manager.py (facade: transition_state_with_hysteresis
delegates; compatibility properties expose the live dicts; cleanup calls
drop_ticket). External readers (live_engine bool check, tests) keep working
through the facade properties.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from nexus_scalp.execution.position_states import PositionState


class PositionStateMachine:
    """Owns per-ticket lifecycle state + hysteresis transition rules."""

    #: Emergency/safety states bypass debouncing with zero latency.
    BYPASS_STATES = frozenset(
        {PositionState.PROFIT_GIVEBACK_CRITICAL, PositionState.LOSS_HARD_EXIT}
    )

    def __init__(self, hysteresis_getter: Callable[[], tuple[float, float]]) -> None:
        self._states: dict[int, PositionState] = {}
        self._candidates: dict[int, tuple[PositionState, datetime, int]] = {}
        self._hysteresis_getter = hysteresis_getter

    def get(self, ticket: int) -> PositionState | None:
        return self._states.get(ticket)

    def has_state(self, ticket: int) -> bool:
        return ticket in self._states

    def drop_ticket(self, ticket: int) -> None:
        """Releases both dicts for one ticket (cleanup-bundle participation)."""
        self._states.pop(ticket, None)
        self._candidates.pop(ticket, None)

    def transition_with_hysteresis(
        self,
        ticket: int,
        target_state: PositionState,
        now: datetime,
    ) -> PositionState:
        """
        Manages state transitions with count-based and time-based hysteresis debouncing.
        Emergency/safety/catastrophic giveback states bypass debouncing with zero latency.
        """
        current_state = self._states.get(ticket)
        if current_state is None:
            # BUGFIX: First initialization - Default to a safe neutral state.
            # NEVER allow a critical exit state on tick 1 to bypass hysteresis debounce,
            # EXCEPT the emergency bypass states: a LOSS_HARD_EXIT / PROFIT_GIVEBACK_CRITICAL
            # verdict on the very first observation (e.g. a restart where a split leg is
            # already deep past its recovery budget) must still be honored immediately
            # rather than reset to a "hold" state that silently keeps trading a
            # position that has already exhausted its protection.
            if target_state in (
                PositionState.LOSS_HARD_EXIT,
                PositionState.PROFIT_GIVEBACK_CRITICAL,
            ):
                self._states[ticket] = target_state
                self._candidates.pop(ticket, None)
                return target_state

            safe_initial_state = (
                PositionState.PROFIT_UNPROTECTED
                if target_state
                in (
                    PositionState.PROFIT_PROTECTED,
                    PositionState.PROFIT_TRAILING,
                    PositionState.PROFIT_UNPROTECTED,
                )
                else PositionState.LOSS_RECOVERY_CANDIDATE
            )

            self._states[ticket] = safe_initial_state
            self._candidates[ticket] = (target_state, now, 1)
            return safe_initial_state

        if current_state == target_state:
            self._candidates.pop(ticket, None)
            return current_state

        # SAFETY IMMEDIATE BYPASS STATES
        # Catastrophic drawdowns, critical givebacks, hard exits transition immediately with zero latency.
        if target_state in self.BYPASS_STATES:
            from nexus_scalp.observability.logging import get_logger

            logger = get_logger("nexus_scalp.execution.order_manager")
            logger.info(
                "[HYSTERESIS BYPASS - EMERGENCY TRANSITION]",
                ticket=ticket,
                from_state=current_state.value,
                to_state=target_state.value,
            )
            self._states[ticket] = target_state
            self._candidates.pop(ticket, None)
            return target_state

        # DEBOUNCING FOR NORMAL TRANSITIONS (Requirement 5)
        cand_info = self._candidates.get(ticket)
        min_dur, min_cnt = self._hysteresis_getter()

        if cand_info is None or cand_info[0] != target_state:
            # First sighting of a target state starts (or restarts) the debounce
            # window: a transition applies only after the candidate holds the target
            # for min_confirmation_duration AND min_observation_count sightings.
            # Emergency transitions bypass this debounce (handled above).
            self._candidates[ticket] = (target_state, now, 1)
            return current_state

        # Repeat sightings increment the counter only; the window timer is never
        # reset, so a flapping candidate cannot delay a genuine transition forever.
        cand_state, first_attempt_time, count = cand_info
        new_count = count + 1
        self._candidates[ticket] = (cand_state, first_attempt_time, new_count)

        elapsed = (now - first_attempt_time).total_seconds()

        # Both the time AND observation-count thresholds must be met; requiring
        # either alone would let a burst of ticks confirm a transition instantly.
        if elapsed >= min_dur and new_count >= min_cnt:
            from nexus_scalp.observability.logging import get_logger

            logger = get_logger("nexus_scalp.execution.order_manager")
            logger.info(
                "[STATE MACHINE TRANSITIONED]",
                ticket=ticket,
                from_state=current_state.value,
                to_state=target_state.value,
                elapsed_sec=round(elapsed, 1),
                observations=new_count,
            )
            self._states[ticket] = target_state
            self._candidates.pop(ticket, None)
            return target_state

        return current_state
