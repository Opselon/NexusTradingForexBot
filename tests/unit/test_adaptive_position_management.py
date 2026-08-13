import pytest
import sqlite3
import tempfile
import os
import time
from datetime import datetime, UTC, timedelta
import torch

from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData, TradeOrder, AccountInfo
from nexus_scalp.configuration.config import AlgoConfig, RiskConfig
from nexus_scalp.execution.order_manager import OrderLifecycleManager, PositionState, ExitMechanism
from nexus_scalp.adapters.database.audit_repository import AuditRepository


class MockMT5Adapter:
    def __init__(self):
        self.positions = []
        self.closed_tickets = []
        self.modifications = []

    def get_positions(self, symbol=None):
        return self.positions

    def get_closed_deals_history(self, symbol, hours_back):
        return []

    def close_position(self, ticket, volume=None):
        self.closed_tickets.append(ticket)
        self.positions = [p for p in self.positions if p.ticket != ticket]
        return True

    def modify_position(self, ticket, stop_loss, take_profit):
        self.modifications.append((ticket, stop_loss, take_profit))
        for i, p in enumerate(self.positions):
            if p.ticket == ticket:
                self.positions[i] = Position(
                    ticket=p.ticket,
                    symbol=p.symbol,
                    type=p.type,
                    volume=p.volume,
                    price_open=p.price_open,
                    sl=stop_loss,
                    tp=take_profit,
                    profit=p.profit,
                    magic=p.magic,
                )
        return True

    def get_symbol_info(self, symbol):
        return SymbolInfo(
            symbol=symbol,
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            volume_min=0.01,
            volume_max=50.0,
            volume_step=0.01,
            stops_level=10,
            freeze_level=0,
            trade_contract_size=100.0,
        )


def test_profit_giveback_failure_regression():
    """
    Requirement 27: Explicit regression test for the original failure pattern.
    A profitable trade (+30.74 PnL) is never protected, experiences rapid giveback,
    and turns negative while old hold_score remains artificially high (e.g. 90-100).
    The new engine must detect this deterioration and trigger protection (PROFIT_GIVEBACK_CRITICAL).
    """
    adapter = MockMT5Adapter()
    audit_repo = AuditRepository(db_url="sqlite:///:memory:")
    om = OrderLifecycleManager(adapter=adapter, audit_repo=audit_repo)

    pos = Position(
        ticket=777,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=1990.00,
        tp=2020.00,
        profit=30.74, # reached +$30.74 peak profit
        magic=888101,
    )
    adapter.positions = [pos]

    now = datetime.now(UTC)
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=2000.31, ask=2000.33, volume=1.0)

    # First evaluation at peak
    om.manage_active_positions("XAUUSD", tick)

    # Monotonic peak must be recorded as 30.74
    assert om.get_protection_state(777).peak_win_usd == 30.74

    # Rapid giveback occurs, price collapses back, PnL turns negative (Pydantic copy update)
    pos_giveback = Position(
        ticket=777,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=1990.00,
        tp=2020.00,
        profit=-5.00, # negative PnL now
        magic=888101,
    )
    adapter.positions = [pos_giveback]
    tick_giveback = TickData(symbol="XAUUSD", timestamp=now + timedelta(seconds=1), bid=1999.95, ask=1999.97, volume=1.0)

    # Stale hold_score is mocked/set artificially high
    om._base_hold_score_tracker[777] = 95

    # Second evaluation during giveback
    om.manage_active_positions("XAUUSD", tick_giveback)

    # The new engine must override the stale high base score and trigger immediate cut
    assert 777 in adapter.closed_tickets
    assert om._position_states[777] == PositionState.PROFIT_GIVEBACK_CRITICAL


def test_hysteresis_state_debouncing():
    """
    Requirement 5 & 6: Verify normal state transitions are debounced,
    while safety/catastrophic states transition instantly with zero latency.
    """
    adapter = MockMT5Adapter()
    audit_repo = AuditRepository(db_url="sqlite:///:memory:")
    om = OrderLifecycleManager(adapter=adapter, audit_repo=audit_repo)

    pos = Position(
        ticket=888,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=1999.50, # Tight SL ($0.50 risk price * 100 contract_size * 1.0 volume = $50.00 risk)
        tp=2020.00,
        profit=-1.00, # Tight loss of $1.00
        magic=888101,
    )
    adapter.positions = [pos]

    now = datetime.now(UTC)
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=1999.95, ask=1999.97, volume=1.0)

    # Initial state transition (LOSS_RECOVERY_CANDIDATE, needs prob >= 0.45 and small adverse score to prevent EV breach)
    probs = torch.tensor([[0.01, 0.65, 0.01]])
    om.manage_active_positions("XAUUSD", tick, probs=probs)
    assert om._position_states[888] == PositionState.LOSS_RECOVERY_CANDIDATE

    # Transition to LOSS_RECOVERY_CONFIRMED (normal state, needs debouncing)
    probs_conf = torch.tensor([[0.01, 0.95, 0.04]]) # high buy prob

    # 1. First attempt should not transition yet (state remains candidate)
    om.manage_active_positions("XAUUSD", tick, probs=probs_conf)
    assert om._position_states[888] == PositionState.LOSS_RECOVERY_CANDIDATE

    # 2. Safety bypass transition: Force critical giveback state or budget exhausted
    # Should transition instantly with zero latency
    pos_peak = Position(
        ticket=888,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=1999.50,
        tp=2020.00,
        profit=35.00, # peak profit
        magic=888101,
    )
    adapter.positions = [pos_peak]
    om.manage_active_positions("XAUUSD", tick) # peak recorded

    pos_fail = Position(
        ticket=888,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=1999.50,
        tp=2020.00,
        profit=-5.00, # complete erosion
        magic=888101,
    )
    adapter.positions = [pos_fail]

    om.manage_active_positions("XAUUSD", tick)
    assert om._position_states[888] == PositionState.PROFIT_GIVEBACK_CRITICAL


def test_immutable_recovery_budget():
    """
    Requirement 13 & 14: Recovery budget is immutable and strictly bounded by remaining risk.
    Once exhausted, the position is closed.
    """
    adapter = MockMT5Adapter()
    audit_repo = AuditRepository(db_url="sqlite:///:memory:")
    om = OrderLifecycleManager(adapter=adapter, audit_repo=audit_repo)

    pos = Position(
        ticket=999,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=1995.00, # Initial risk = $5.00 * 100.0 * 1.0 = $500.00
        tp=2020.00,
        profit=-10.00, # Initial loss at recovery entry = $10.00
        magic=888101,
    )
    adapter.positions = [pos]

    now = datetime.now(UTC)
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=1999.90, ask=1999.92, volume=1.0)

    # Initialize recovery mode
    probs = torch.tensor([[0.01, 0.98, 0.01]])
    om.manage_active_positions("XAUUSD", tick, probs=probs)

    initial_budget = om._recovery_budget_initial[999]
    # Default is 50% of R = $250.00. Remaining risk is $490.00, so budget is $250.00.
    assert initial_budget == 250.00

    # Drawdown widens, consuming the budget (Reconstruct position)
    pos_deep_loss = Position(
        ticket=999,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=1995.00,
        tp=2020.00,
        profit=-270.00, # Consumes $260.00 of budget (> $250.00)
        magic=888101,
    )
    adapter.positions = [pos_deep_loss]

    tick_loss = TickData(symbol="XAUUSD", timestamp=now + timedelta(seconds=1), bid=1997.30, ask=1997.32, volume=1.0)
    om.manage_active_positions("XAUUSD", tick_loss, probs=probs)

    # Budget exhausted -> Position closed immediately!
    assert 999 in adapter.closed_tickets
    assert om._position_states[999] == PositionState.LOSS_HARD_EXIT
