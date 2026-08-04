from datetime import datetime, UTC
import pytest
import sqlite3
import tempfile
import os

from nexus_scalp.domain.enums import OrderType, ActionType
from nexus_scalp.domain.models import Position, TickData, TradeOrder
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.adapters.database.audit_repository import AuditRepository


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
