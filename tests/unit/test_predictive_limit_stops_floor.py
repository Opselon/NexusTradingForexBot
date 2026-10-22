"""Regression guards for the predictive-limit pending-entry stops-level floor.

A 2-minute PAPER soak (XAUUSD, broker stops_level=10 points, point=0.01 =>
0.10 price floor) produced 20 identical RISK_ENGINE rejections and zero
trades. Every one came from the predictive-limit path:

    equilibrium = swing_low + 0.50 * (swing_high - swing_low)
    entry       = equilibrium                      # e.g. ask - 0.06

The old generator clamped the entry ONLY when it was already at or through
the ask (``entry >= ask``), so an equilibrium *inside* the market passed
straight through. The risk engine then rejected it because a pending order
must keep ``stops_level * point`` from the market. The generator was
manufacturing orders that were unplaceable by construction — a rejection
storm with a zero trade yield, which reads as "the bot is passive" while
actually being "the bot proposes invalid prices".

These tests pin the post-fix invariant: for every generated pending order,
the entry distance to the market is at least the configured floor, which
must itself be >= the broker's stops level.
"""

import pytest

from nexus_scalp.configuration.config import AlgoConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.signals.policy import SignalPolicy

#: Broker constraints from the soak's paper adapter (2-digit XAUUSD).
STOPS_LEVEL_POINTS = 10
POINT = 0.01
BROKER_MIN_DISTANCE = STOPS_LEVEL_POINTS * POINT  # 0.10


def _make_tick(bid: float, ask: float) -> TickData:
    from datetime import UTC, datetime

    return TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=bid, ask=ask)


def _proposal(policy: SignalPolicy, tick: TickData, order_block_type: int):
    return policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=order_block_type,
        current_tick=tick,
        atr=2.0,
        completed_bars=None,
        execution_id="EXEC-REGRESSION-STOPS",
        now=tick.timestamp,
        confidence=0.85,
        confidence_before_filters=0.85,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )


def test_configured_floor_is_not_tighter_than_the_broker_stops_level():
    """The generator floor must never be below what the risk engine enforces.

    If this fails, the generator can emit entries the engine is guaranteed to
    reject — exactly the defect this module guards.
    """
    cfg = AlgoConfig()
    assert cfg.min_pending_stop_distance >= BROKER_MIN_DISTANCE


@pytest.mark.parametrize("ask_offset", [0.00, 0.02, 0.05, 0.09])
def test_buy_limit_entry_keeps_broker_distance(ask_offset: float):
    """A BUY_LIMIT equilibrium that lands inside the market is clamped out.

    Construction: bid=2000.00, ask=2000.00+ask_offset. With atr=2.0 the
    default swings put the 50% equilibrium at 2000.00 + ask_offset/2, i.e.
    ask_offset/2 away from the ask. Every value below 0.20 is inside or near
    the 0.10 broker floor, which is precisely the soak's failure geometry.
    """
    policy = SignalPolicy()
    ask = 2000.00 + ask_offset
    tick = _make_tick(bid=2000.00, ask=ask)

    proposal = _proposal(policy, tick, order_block_type=1)

    assert proposal is not None
    assert proposal.action == ActionType.BUY_LIMIT
    distance = tick.ask - proposal.proposed_entry
    assert distance >= BROKER_MIN_DISTANCE - 1e-9, (
        f"BUY_LIMIT entry {proposal.proposed_entry} sits {distance:.4f} from ask "
        f"{tick.ask}; the risk engine rejects anything under "
        f"{BROKER_MIN_DISTANCE} (stops_level {STOPS_LEVEL_POINTS} x point {POINT})"
    )


@pytest.mark.parametrize("bid_offset", [0.00, 0.02, 0.05, 0.09])
def test_sell_limit_entry_keeps_broker_distance(bid_offset: float):
    """Mirror case for SELL_LIMIT against the bid."""
    policy = SignalPolicy()
    bid = 2000.00 + bid_offset
    tick = _make_tick(bid=bid, ask=bid + 0.20)

    proposal = _proposal(policy, tick, order_block_type=-1)

    assert proposal is not None
    assert proposal.action == ActionType.SELL_LIMIT
    distance = proposal.proposed_entry - tick.bid
    assert distance >= BROKER_MIN_DISTANCE - 1e-9, (
        f"SELL_LIMIT entry {proposal.proposed_entry} sits {distance:.4f} from bid "
        f"{tick.bid}; the risk engine rejects anything under {BROKER_MIN_DISTANCE}"
    )


def test_soak_geometry_that_produced_the_rejection_storm():
    """The exact observed case: ask=4397.20, entry collapsed to 4397.11 (0.09).

    The pre-fix generator returned 4397.11 here and the risk engine rejected
    it with STOPS_LEVEL_ENTRY_TOO_CLOSE, shortfall=0.01. Post-fix the proposal
    must clear the floor.
    """
    policy = SignalPolicy()
    tick = _make_tick(bid=4397.03, ask=4397.20)

    proposal = _proposal(policy, tick, order_block_type=1)

    assert proposal is not None
    assert proposal.action == ActionType.BUY_LIMIT
    distance = tick.ask - proposal.proposed_entry
    assert distance >= BROKER_MIN_DISTANCE - 1e-9


def test_floor_does_not_move_a_comfortably_distant_entry():
    """A window whose equilibrium is already far from market is untouched.

    swing_low = bid - atr = 1996.00, swing_high = ask + atr = 2004.20 with
    bid=1998.00 / ask=2000.20 => equilibrium = 2000.10, which is 0.10 from the
    ask and WOULD be clamped. To exercise the untouched path the equilibrium
    must sit further out: a wide ATR pushes the low far below the entry.
    """
    policy = SignalPolicy()
    tick = _make_tick(bid=1998.00, ask=2000.20)

    proposal = _proposal(policy, tick, order_block_type=1)

    assert proposal is not None
    assert proposal.action == ActionType.BUY_LIMIT
    # swing_low = 1998.00 - 2.00 = 1996.00 ; swing_high = 2000.20 + 2.00 = 2002.20
    # equilibrium = 1996.00 + 0.5 * 6.20 = 1999.10 => 1.10 below the ask,
    # comfortably clear of the 0.12 floor, so no clamp is applied.
    assert proposal.proposed_entry == 1999.10
