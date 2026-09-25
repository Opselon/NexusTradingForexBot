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


# ---------------------------------------------------------------------------
# BUG-285 (wave 2026-09-14, input-validation lane L11-1): pydantic gt/ge
# accept infinity, so a malfunctioning terminal's inf quote produced
# spread=inf (bid finite, ask inf) or spread=NaN (both inf) INSIDE the
# validated boundary. Corrupted price is a MUST-FAIL-CLOSED input class.
# RED-before probes (VERIFIED in the lane report):
#   TickData(bid=1.0, ask=inf)      -> ACCEPTED, spread_points=inf
#   TickData(bid=inf, ask=inf)      -> ACCEPTED, spread_points=NaN
#   TickData(..., volume=inf)       -> ACCEPTED
#   TickData(..., last=inf)         -> ACCEPTED
# ---------------------------------------------------------------------------


def test_tick_data_rejects_infinite_ask() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        TickData(symbol="XAUUSD", timestamp=now, bid=1.0, ask=float("inf"))


def test_tick_data_rejects_infinite_bid_and_ask() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        TickData(symbol="XAUUSD", timestamp=now, bid=float("inf"), ask=float("inf"))


def test_tick_data_rejects_infinite_last_and_volume() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        TickData(symbol="XAUUSD", timestamp=now, bid=1.0, ask=1.1, last=float("inf"))
    with pytest.raises(ValidationError):
        TickData(symbol="XAUUSD", timestamp=now, bid=1.0, ask=1.1, volume=float("inf"))


def test_tick_data_rejects_nan_price() -> None:
    """NaN was already rejected as a side-effect of gt (nan > 0 is False);
    pin it so the explicit contract keeps covering it."""
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        TickData(symbol="XAUUSD", timestamp=now, bid=float("nan"), ask=1.1)


def test_tick_data_finite_prices_still_accepted() -> None:
    now = datetime.now(UTC)
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=4400.10, ask=4400.35)
    assert tick.spread_points == pytest.approx(0.25, abs=1e-6)


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
