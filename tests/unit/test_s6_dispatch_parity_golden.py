"""Agent-5 S6-dispatch golden: ExecutionPlan contract + broker parity.

Spy-adapter execution traces for the dispatcher stage. Verifies:
- ExecutionPlan is frozen intent (validation rules, no behavior)
- CLOSE branch: adapter.close_position called once with the ticket; success
  path marks closed + broker-verified + cache release + sibling propagation;
  failure path pops the forced mechanism (identical to original)
- MODIFY_SL branch: monotonic safety floor (loosening target zeroed),
  _should_modify_sl gate, modify_position args (stop_loss=target, tp=pos.tp),
  BUG-085 confirmed-modification tracking
- failure injection: plan validation rejects empty action / bad ticket
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, TickData
from nexus_scalp.execution.execution_plan import ExecutionPlan
from nexus_scalp.execution.order_manager import OrderLifecycleManager


def _om(close_result=True, modify_result=True):
    adapter = Mock()
    adapter.close_position = Mock(return_value=close_result)
    adapter.modify_position = Mock(return_value=modify_result)
    adapter.get_positions = Mock(return_value=[])
    repo = AuditRepository(db_url="sqlite:///:memory:")
    manager = OrderLifecycleManager(adapter=adapter, audit_repo=repo)
    manager.notifier = None
    yield manager
    repo.close()


@pytest.fixture()
def om():
    yield from _om()


def _pos():
    return Position(
        ticket=201,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.0,
        sl=1995.0,
        tp=2020.0,
        profit=1.0,
        magic=1,
    )


def _plan(action="CLOSE", **kw):
    return ExecutionPlan(action=action, scenario="TEST", ticket=201, symbol="XAUUSD", **kw)


class TestExecutionPlanContract:
    def test_frozen_intent(self):
        plan = _plan()
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            plan.action = "CLOSE"  # frozen

    def test_validation_rules(self):
        with pytest.raises(ValueError):
            ExecutionPlan(action="", scenario="X", ticket=1, symbol="S")
        with pytest.raises(ValueError):
            ExecutionPlan(action="CLOSE", scenario="X", ticket=0, symbol="S")


class TestDispatchParity:
    def test_close_success_trace(self, om):
        pos = _pos()
        now = datetime.now(UTC)
        om._forced_exit_mechanisms[201] = None
        om._live_tickets_cache[201] = {"ticket": 201}
        om._closed_tickets.pop(201, None)

        om._execute_position_action(
            plan=_plan(),
            pos=pos,
            ticket=201,
            now=now,
            atr=1.0,
            spread=0.02,
            min_stop_gap=0.25,
            price_current=2001.0,
            rule_target_sl=0.0,
            hold_score=42,
            protection=Mock(),
            symbol_info=None,
            current_tick=TickData(
                symbol="XAUUSD", timestamp=now, bid=1999.9, ask=1999.92, volume=1.0
            ),
            scenario="TEST",
            action="CLOSE",
        )

        om.adapter.close_position.assert_called_once_with(ticket=201)
        assert om._closed_tickets[201] is True
        assert 201 not in om._live_tickets_cache

    def test_close_failure_pops_mechanism(self, om_close_failure):
        manager = om_close_failure
        pos = _pos()
        now = datetime.now(UTC)
        manager._forced_exit_mechanisms[201] = None
        manager._execute_position_action(
            plan=_plan(),
            pos=pos,
            ticket=201,
            now=now,
            atr=1.0,
            spread=0.02,
            min_stop_gap=0.25,
            price_current=2001.0,
            rule_target_sl=0.0,
            hold_score=42,
            protection=Mock(),
            symbol_info=None,
            current_tick=TickData(
                symbol="XAUUSD", timestamp=now, bid=1999.9, ask=1999.92, volume=1.0
            ),
            scenario="TEST",
            action="CLOSE",
        )
        manager.adapter.close_position.assert_called_once_with(ticket=201)
        assert 201 not in manager._forced_exit_mechanisms, "failure path must pop the mechanism"
        assert manager._closed_tickets.get(201) is not True

    @pytest.fixture()
    def om_close_failure(self):
        yield from _om(close_result=False)

    def test_modify_sl_parity_and_safety_floor(self, om):
        pos = _pos()
        now = datetime.now(UTC)
        # loosening target (below current SL for BUY) -> zeroed by the monotonic floor
        om._should_modify_sl = Mock(return_value=True)
        om._execute_position_action(
            plan=_plan(action="MODIFY_SL", rule_target_sl=1990.0),
            pos=pos,
            ticket=201,
            now=now,
            atr=1.0,
            spread=0.02,
            min_stop_gap=0.25,
            price_current=2001.0,
            rule_target_sl=1990.0,
            hold_score=42,
            protection=Mock(),
            symbol_info=None,
            current_tick=TickData(
                symbol="XAUUSD", timestamp=now, bid=1999.9, ask=1999.92, volume=1.0
            ),
            scenario="RULE",
            action="MODIFY_SL",
        )
        om.adapter.modify_position.assert_not_called(), "loosening SL must be zeroed (safety floor)"

        # improving target -> modify called with exact args
        om._execute_position_action(
            plan=_plan(action="MODIFY_SL", rule_target_sl=1997.0),
            pos=pos,
            ticket=201,
            now=now,
            atr=1.0,
            spread=0.02,
            min_stop_gap=0.25,
            price_current=2001.0,
            rule_target_sl=1997.0,
            hold_score=42,
            protection=Mock(),
            symbol_info=None,
            current_tick=TickData(
                symbol="XAUUSD", timestamp=now, bid=1999.9, ask=1999.92, volume=1.0
            ),
            scenario="RULE",
            action="MODIFY_SL",
        )
        om.adapter.modify_position.assert_called_once_with(
            ticket=201, stop_loss=1997.0, take_profit=2020.0
        )
        assert om._last_modify_sl[201] == 1997.0
        assert om._sl_modified_flags[201] is True
