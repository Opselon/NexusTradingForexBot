"""BUG-247 residual: hedge clamp + reversal SAFE_MODE gate.

Delegate sweeps of CHG-0058 found two remaining gate gaps inside the single
authority: the hedge entry (execute_order) had no last-defense HARD_MAX_LOTS
clamp, and the AI-flip fast-reversal direct pending bypassed the SAFE_MODE
circuit. Both fixed here — fail-closed, thresholds unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import TradeOrder
from nexus_scalp.execution.order_manager import AI_REVERSAL_REASON, OrderLifecycleManager


@pytest.fixture()
def paper_om():
    adapter = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
    adapter.connect()
    om = OrderLifecycleManager(adapter=adapter, audit_repo=None, risk_engine=None)
    return om, adapter


class TestBug247HedgeHardCap:
    def test_direct_100_is_clamped_to_10(self, paper_om):
        om, adapter = paper_om
        # AGENT-12 core-integrity repair (2026-09-11): the paper adapter's
        # margin realism (bd9f4bba: "G" margin check, contract 100 @ 100:1)
        # needs 44,000 free margin for the clamped 10.0-lot order at 4400 —
        # the original 10,000 balance made the paper broker REFUSE the fill
        # after the clamp, turning the clamp assertion into a margin
        # rejection (deterministically RED since 09-06). BUG-247's contract
        # is the VOLUME CLAMP, not account size: fund the account so the
        # clamped order is fillable and the clamp assertion stays real.
        adapter.balance = 100000.0
        adapter.equity = 100000.0
        om.global_state = "NORMAL"
        order = TradeOrder(
            order_id="bug247-h1",
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=100.0,
            price=4400.0,
            stop_loss=4390.0,
            take_profit=4420.0,
            magic_number=888101,
        )
        assert om.execute_order(order) is True
        assert adapter.get_positions()[0].volume <= 10.0

    def test_clamped_zero_is_refused(self, paper_om):
        om, adapter = paper_om
        om.global_state = "NORMAL"
        om._clamp_dispatch_volume = lambda v, symbol=None: 0.0  # type: ignore[method-assign]
        order = TradeOrder(
            order_id="bug247-h2",
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=0.10,
            price=4400.0,
            stop_loss=4390.0,
            take_profit=4420.0,
            magic_number=888101,
        )
        assert om.execute_order(order) is False
        assert not adapter.get_positions()

    def test_failed_close_never_stacks_a_followup(self, paper_om):
        """BUG-247 gate, stated behaviorally: the AI-reversal protocol may
        never stack an opposing order on a position the broker refused to
        close. Earlier audits checked this via a source-string probe
        ("AI REVERSAL follow-up blocked"); that message was superseded by a
        structured self-audit path, so the contract is asserted on real
        behavior instead — the follow-up dispatch is unreachable when the
        protective close fails, and unreachable while the flip is suspended."""
        om, adapter = paper_om

        class _Spy:
            def __init__(self) -> None:
                self.calls: list[float] = []

        dispatch_calls = _Spy()

        class _Pos:
            ticket = 42
            symbol = "XAUUSD"
            type = OrderType.BUY
            volume = 0.10
            profit = -5.0

        def _collect(_decision: object, volume: float) -> bool:
            dispatch_calls.calls.append(volume)
            return True

        adapter.get_positions = lambda symbol=None: [_Pos()]  # type: ignore[method-assign]
        adapter.close_position = lambda ticket, volume=None, **kw: False  # type: ignore[method-assign]
        om.dispatch_order = _collect  # type: ignore[method-assign]

        decision = SimpleNamespace(
            ticket=42,
            symbol="XAUUSD",
            action=ActionType.CLOSE_POSITION,
            reversal_action=ActionType.BUY_STOP,
            reason_code=AI_REVERSAL_REASON,
        )

        # 1. Suspension gate (operator flag defaults to False): no follow-up.
        om.algo_config = SimpleNamespace(ai_flip_exit_enabled=False)
        assert om.execute_ai_reversal(decision=decision, volume=0.5) is False
        assert dispatch_calls.calls == []

        # 2. Even with the operator flag on, a failed close refuses the flip.
        om.algo_config = SimpleNamespace(ai_flip_exit_enabled=True)
        assert om.execute_ai_reversal(decision=decision, volume=0.5) is False
        assert dispatch_calls.calls == []
