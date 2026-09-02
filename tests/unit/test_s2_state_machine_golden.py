"""Agent-5 S2 golden characterization: position state machine semantics.

Written against PRE-extraction behavior (OrderLifecycleManager methods),
re-run post-extraction to prove parity. Covers: first-observation neutral
init, emergency first-observation bypass, same-state candidate cancellation,
emergency bypass with logging, debounce count+time (both required), candidate
restart on target change, window timer non-reset on repeat sightings,
cleanup participation, cross-ticket isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.execution.order_manager import OrderLifecycleManager, PositionState


@pytest.fixture()
def om():
    adapter = Mock()
    repo = AuditRepository(db_url="sqlite:///:memory:")
    manager = OrderLifecycleManager(adapter=adapter, audit_repo=repo)
    manager.algo_config = Mock(min_confirmation_duration=2.5, min_observation_count=10)
    yield manager
    repo.close()


NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


class TestS2StateMachineGolden:
    def test_first_observation_neutral_init(self, om):
        # non-emergency target on first observation -> safe neutral + candidate staged
        st = om.transition_state_with_hysteresis(1, PositionState.PROFIT_PROTECTED, NOW)
        assert st == PositionState.PROFIT_UNPROTECTED
        assert om._position_states[1] == PositionState.PROFIT_UNPROTECTED
        assert om._state_transition_candidates[1][0] == PositionState.PROFIT_PROTECTED
        assert om._state_transition_candidates[1][2] == 1

    def test_first_observation_loss_neutral(self, om):
        st = om.transition_state_with_hysteresis(1, PositionState.LOSS_EXIT_PRESSURE, NOW)
        assert st == PositionState.LOSS_RECOVERY_CANDIDATE

    def test_first_observation_emergency_bypass(self, om):
        st = om.transition_state_with_hysteresis(1, PositionState.LOSS_HARD_EXIT, NOW)
        assert st == PositionState.LOSS_HARD_EXIT
        assert om._position_states[1] == PositionState.LOSS_HARD_EXIT
        assert 1 not in om._state_transition_candidates

    def test_first_observation_critical_giveback_bypass(self, om):
        st = om.transition_state_with_hysteresis(1, PositionState.PROFIT_GIVEBACK_CRITICAL, NOW)
        assert st == PositionState.PROFIT_GIVEBACK_CRITICAL

    def test_same_state_cancels_candidate(self, om):
        om.transition_state_with_hysteresis(1, PositionState.PROFIT_PROTECTED, NOW)
        st = om.transition_state_with_hysteresis(
            1, PositionState.PROFIT_UNPROTECTED, NOW + timedelta(seconds=1)
        )
        assert st == PositionState.PROFIT_UNPROTECTED
        assert 1 not in om._state_transition_candidates

    def test_debounce_requires_count_and_time(self, om):
        om.transition_state_with_hysteresis(1, PositionState.PROFIT_PROTECTED, NOW)
        # 9 repeat sightings within window: no transition
        for i in range(1, 10):
            st = om.transition_state_with_hysteresis(
                1,
                PositionState.PROFIT_PROTECTED,
                NOW + timedelta(seconds=0.2 * i),
            )
            assert st == PositionState.PROFIT_UNPROTECTED
        # 10th sighting but time window still short: STILL no transition
        st = om.transition_state_with_hysteresis(
            1, PositionState.PROFIT_PROTECTED, NOW + timedelta(seconds=2.0)
        )
        assert st == PositionState.PROFIT_UNPROTECTED
        # time met (2.5s) AND count>=10: transition
        st = om.transition_state_with_hysteresis(
            1, PositionState.PROFIT_PROTECTED, NOW + timedelta(seconds=2.6)
        )
        assert st == PositionState.PROFIT_PROTECTED
        assert 1 not in om._state_transition_candidates

    def test_window_timer_never_resets(self, om):
        # flapping candidate restarts on target change, but repeats keep the
        # FIRST attempt time
        om.transition_state_with_hysteresis(1, PositionState.PROFIT_PROTECTED, NOW)
        om.transition_state_with_hysteresis(
            1, PositionState.PROFIT_TRAILING, NOW + timedelta(seconds=0.5)
        )
        cand = om._state_transition_candidates[1]
        assert cand[0] == PositionState.PROFIT_TRAILING
        assert cand[1] == NOW + timedelta(seconds=0.5)
        assert cand[2] == 1
        # repeat: first_attempt_time unchanged
        om.transition_state_with_hysteresis(
            1, PositionState.PROFIT_TRAILING, NOW + timedelta(seconds=1.0)
        )
        cand = om._state_transition_candidates[1]
        assert cand[1] == NOW + timedelta(seconds=0.5)
        assert cand[2] == 2

    def test_emergency_bypass_mid_debounce(self, om):
        om.transition_state_with_hysteresis(1, PositionState.PROFIT_PROTECTED, NOW)
        st = om.transition_state_with_hysteresis(
            1, PositionState.PROFIT_GIVEBACK_CRITICAL, NOW + timedelta(seconds=0.1)
        )
        assert st == PositionState.PROFIT_GIVEBACK_CRITICAL
        assert 1 not in om._state_transition_candidates

    def test_cross_ticket_isolation(self, om):
        om.transition_state_with_hysteresis(1, PositionState.PROFIT_PROTECTED, NOW)
        om.transition_state_with_hysteresis(2, PositionState.LOSS_EXIT_PRESSURE, NOW)
        assert om._position_states[1] == PositionState.PROFIT_UNPROTECTED
        assert om._position_states[2] == PositionState.LOSS_RECOVERY_CANDIDATE

    def test_cleanup_participation(self, om):
        om.transition_state_with_hysteresis(1, PositionState.PROFIT_PROTECTED, NOW)
        om._cleanup_ticket_state(1)
        assert 1 not in om._position_states
        assert 1 not in om._state_transition_candidates
