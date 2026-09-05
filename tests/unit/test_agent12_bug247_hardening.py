"""BUG-247 residual: hedge clamp + reversal SAFE_MODE gate.

Delegate sweeps of CHG-0058 found two remaining gate gaps inside the single
authority: the hedge entry (execute_order) had no last-defense HARD_MAX_LOTS
clamp, and the AI-flip fast-reversal direct pending bypassed the SAFE_MODE
circuit. Both fixed here — fail-closed, thresholds unchanged.
"""

from __future__ import annotations

import pytest

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import TradeOrder
from nexus_scalp.execution.order_manager import OrderLifecycleManager


@pytest.fixture()
def paper_om():
    adapter = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
    adapter.connect()
    om = OrderLifecycleManager(adapter=adapter, audit_repo=None, risk_engine=None)
    return om, adapter


class TestBug247HedgeHardCap:
    def test_direct_100_is_clamped_to_10(self, paper_om):
        om, adapter = paper_om
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

    def test_source_gate_present(self, paper_om):
        om, _ = paper_om
        import inspect

        assert "AI REVERSAL follow-up blocked" in inspect.getsource(om._run_protection_chain)
