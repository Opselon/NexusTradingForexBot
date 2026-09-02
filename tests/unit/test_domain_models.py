"""
Unit Tests - Domain Models & Invariants
=======================================
Verifies business logic rules on value objects.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal


def test_tick_data_valid_instantiation() -> None:
    """Verifies that a valid tick snapshot is initialized correctly."""
    now = datetime.now(UTC)
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
    now = datetime.now(UTC)
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
    now = datetime.now(UTC)
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


def test_trade_proposal_execution_id_default_none():
    """PHASE 13 forensic contract: execution_id is optional (default None) so
    legacy construction sites and tests keep working; it only carries a value
    once the policy stamps it."""
    from datetime import UTC, datetime

    from nexus_scalp.domain.enums import ActionType
    from nexus_scalp.domain.models import TradeProposal

    p = TradeProposal(
        request_id="req-1",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY,
        confidence=0.5,
        proposed_entry=100.0,
        stop_loss=99.0,
        take_profit=101.0,
        risk_reward_ratio=2.0,
    )
    assert p.execution_id is None

    p2 = p.model_copy(update={"execution_id": "EXEC-20260820-010203-abc123"})
    assert p2.execution_id == "EXEC-20260820-010203-abc123"
    # frozen model: original untouched
    assert p.execution_id is None
