import os
import sqlite3
import tempfile
from datetime import UTC, datetime

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import Position, TickData, TradeOrder, TradeProposal
from nexus_scalp.execution.order_manager import OrderLifecycleManager


class MockMT5Adapter:
    def __init__(self):
        self.positions = []
        self.pending_orders = []
        self.order_sends = []
        self.send_order_return = True

    def get_positions(self, symbol=None):
        return self.positions

    def get_pending_orders(self, symbol=None):
        return self.pending_orders

    def send_order(self, order: TradeOrder) -> bool:
        self.order_sends.append(order)
        return self.send_order_return


def test_order_manager_cache_synchronization():
    """Verify that the live tickets cache correctly synchronizes on every tick."""
    adapter = MockMT5Adapter()
    om = OrderLifecycleManager(adapter=adapter)

    # 1. Initially cache is empty
    assert len(om.get_active_live_tickets()) == 0

    # 2. Add a live position
    pos = Position(
        ticket=1001,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.0,
        sl=1990.0,
        tp=2020.0,
        profit=0.0,
        magic=888101,
    )
    adapter.positions = [pos]

    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2)
    om.manage_active_positions(symbol="XAUUSD", current_tick=tick)

    # Cache should be updated with the position
    tickets = om.get_active_live_tickets()
    assert len(tickets) == 1
    assert tickets[0]["ticket"] == 1001
    assert tickets[0]["magic"] == 888101


def test_order_manager_cache_cleanup_on_close():
    """Verify that live tickets are immediately purged from active tracking once closed."""
    adapter = MockMT5Adapter()
    om = OrderLifecycleManager(adapter=adapter)

    pos = Position(
        ticket=1002,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.0,
        sl=1990.0,
        tp=2020.0,
        profit=0.0,
        magic=888101,
    )
    adapter.positions = [pos]

    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2)
    om.manage_active_positions(symbol="XAUUSD", current_tick=tick)

    assert len(om.get_active_live_tickets()) == 1

    # Now close the position
    adapter.positions = []
    om.manage_active_positions(symbol="XAUUSD", current_tick=tick)

    # Cache should now be empty
    assert len(om.get_active_live_tickets()) == 0


def test_order_manager_unified_router():
    """Verify that dispatch_order and execute_lifecycle_action successfully route all action types."""

    class MockAdapterWithRouter:
        def __init__(self):
            self.market_orders = []
            self.pending_orders = []
            self.modifications = []
            self.cancellations = []
            self.closures = []

        def execute_market_order(self, symbol, order_type, volume, price, stop_loss, take_profit):
            self.market_orders.append((symbol, order_type, volume, price, stop_loss, take_profit))
            return 12345

        def place_pending_order(self, symbol, order_type, volume, price, stop_loss, take_profit):
            self.pending_orders.append((symbol, order_type, volume, price, stop_loss, take_profit))
            return 67890

        def modify_order(self, ticket, stop_loss, take_profit):
            self.modifications.append((ticket, stop_loss, take_profit))
            return True

        def cancel_pending_order(self, ticket):
            self.cancellations.append(ticket)
            return True

        def close_position(self, ticket, volume=None):
            self.closures.append((ticket, volume))
            return True

        def get_positions(self, symbol=None):
            return []

    adapter = MockAdapterWithRouter()
    om = OrderLifecycleManager(adapter=adapter)

    # 1. Test dispatch_order for BUY
    prop_buy = TradeProposal(
        request_id="req-buy",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY,
        confidence=0.8,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )
    assert om.dispatch_order(prop_buy, 1.5) is True
    assert len(adapter.market_orders) == 1
    assert adapter.market_orders[0] == ("XAUUSD", OrderType.BUY, 1.5, 2000.0, 1990.0, 2020.0)

    # 2. Test dispatch_order for BUY_LIMIT
    prop_limit = TradeProposal(
        request_id="req-limit",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY_LIMIT,
        confidence=0.8,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )
    assert om.dispatch_order(prop_limit, 2.0) is True
    assert len(adapter.pending_orders) == 1
    assert adapter.pending_orders[0] == ("XAUUSD", OrderType.BUY_LIMIT, 2.0, 2000.0, 1990.0, 2020.0)

    # 3. Test execute_lifecycle_action for CLOSE_POSITION
    prop_close = TradeProposal(
        request_id="req-close",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.CLOSE_POSITION,
        confidence=0.0,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=1.0,
        ticket=12345,
    )
    assert om.execute_lifecycle_action(prop_close) is True
    assert len(adapter.closures) == 1
    assert adapter.closures[0] == (12345, None)

    # 4. Test execute_lifecycle_action for MODIFY_SL_TP
    prop_modify = TradeProposal(
        request_id="req-modify",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.MODIFY_SL_TP,
        confidence=0.0,
        proposed_entry=2000.0,
        stop_loss=1995.0,
        take_profit=2015.0,
        risk_reward_ratio=1.0,
        ticket=12345,
    )
    assert om.execute_lifecycle_action(prop_modify) is True
    assert len(adapter.modifications) == 1
    assert adapter.modifications[0] == (12345, 1995.0, 2015.0)
