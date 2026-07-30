"""
Unit Tests - Domain Models & Invariants
=======================================
Verifies business logic rules on value objects.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal


def test_tick_data_valid_instantiation() -> None:
    """Verifies that a valid tick snapshot is initialized correctly."""
    now = datetime.now(timezone.utc)
    tick = TickData(
        symbol="EURUSD",
        timestamp=now,
        bid=1.08500,
        ask=1.08515,
        last=1.08510,
        volume=100.0,
    )
    assert tick.symbol == "EURUSD"
    assert tick.spread_points == 0.00015


def test_tick_data_invalid_spread_raises_validation_error() -> None:
    """Ensures that Bid > Ask triggers a validation failure (negative spread guard)."""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        TickData(
            symbol="EURUSD",
            timestamp=now,
            bid=1.08550,
            ask=1.08515,
            last=1.08510,
            volume=100.0,
        )


def test_trade_proposal_buy_invariants() -> None:
    """Ensures invalid stop loss placement on Buy actions triggers validation error."""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        TradeProposal(
            request_id="test-uuid-1",
            symbol="EURUSD",
            generated_at=now,
            action=ActionType.BUY_MARKET,
            confidence=0.92,
            proposed_entry=1.08500,
            stop_loss=1.08600,
            take_profit=1.09000,
            risk_reward_ratio=2.0,
        )
