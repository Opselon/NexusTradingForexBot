"""
Unit tests for `_evaluate_exposure_limits` in `SignalPolicy` (`src/nexus_scalp/signals/policy.py`).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.signals.policy import SignalPolicy


@dataclass
class DummyBar:
    low: float
    high: float


class DummyOrderManager:
    def __init__(self, live_tickets=None):
        self.live_tickets = live_tickets or []

    def get_active_live_tickets(self):
        return self.live_tickets


def _make_tick(symbol: str = "XAUUSD", bid: float = 2000.0, ask: float = 2000.2) -> TickData:
    return TickData(
        symbol=symbol,
        timestamp=datetime.now(UTC),
        bid=bid,
        ask=ask,
        volume=1.0,
    )


def test_exposure_limits_total_exposure_zero_returns_none() -> None:
    """When total exposure is 0 (< MAX_TOTAL_EXPOSURE), exposure check passes (returns None)."""
    policy = SignalPolicy()
    tick = _make_tick()
    now = tick.timestamp

    proposal = policy._evaluate_exposure_limits(
        total_exposure=0,
        active_positions_count=0,
        active_pending_count=0,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2000.2,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
        execution_id="EXEC-TEST-001",
    )

    assert proposal is None


def test_exposure_limits_same_level_reentry_blocked() -> None:
    """When total exposure >= 1 and an existing order is within $0.50 of target_entry_price, SAME_LEVEL_REENTRY_BLOCKED is returned."""
    policy = SignalPolicy()
    tick = _make_tick()
    now = tick.timestamp
    live_tickets = [{"symbol": "XAUUSD", "magic": 888101, "price": 2000.00, "type": "POSITION"}]
    om = DummyOrderManager(live_tickets=live_tickets)

    proposal = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=1,
        active_pending_count=0,
        order_manager=om,
        live_tickets=live_tickets,
        target_entry_price=2000.20,  # Proximity diff = 0.20 < 0.50
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
        execution_id="EXEC-TEST-SAME-LEVEL",
    )

    assert proposal is not None
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "SAME_LEVEL_REENTRY_BLOCKED"
    assert proposal.execution_id == "EXEC-TEST-SAME-LEVEL"
    assert proposal.confidence == 0.0


def test_exposure_limits_max_exposure_reached_active_position() -> None:
    """When total exposure >= 1 with active_positions_count >= 1 (not same level), MAX_EXPOSURE_REACHED is returned."""
    policy = SignalPolicy()
    tick = _make_tick()
    now = tick.timestamp
    live_tickets = [{"symbol": "XAUUSD", "magic": 888101, "price": 1980.00, "type": "POSITION"}]
    om = DummyOrderManager(live_tickets=live_tickets)

    proposal = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=1,
        active_pending_count=0,
        order_manager=om,
        live_tickets=live_tickets,
        target_entry_price=2000.20,  # Proximity diff = 20.20 >= 0.50
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
        execution_id="EXEC-TEST-MAX-EXPOSURE",
    )

    assert proposal is not None
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "MAX_EXPOSURE_REACHED"
    assert proposal.blocked_by == "EXECUTION_STATE_BLOCK"
    assert proposal.decision_stage == "EXPOSURE_GATE"
    assert proposal.execution_id == "EXEC-TEST-MAX-EXPOSURE"


def test_exposure_limits_pending_order_locked_due_to_time() -> None:
    """When active_pending_count >= 1 and locked pending order is within 30s lock window, PENDING_ORDER_LOCKED is returned."""
    policy = SignalPolicy()
    tick = _make_tick(bid=2000.0, ask=2000.2)
    now = tick.timestamp

    # Lock created 10 seconds ago (<= 30.0s lock window)
    policy._locked_pending_time = now - timedelta(seconds=10)
    policy._locked_pending_price = 2000.00

    proposal = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2005.00,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
        execution_id="EXEC-TEST-PENDING-TIME-LOCK",
    )

    assert proposal is not None
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "PENDING_ORDER_LOCKED"
    assert proposal.blocked_by == "EXECUTION_STATE_BLOCK"
    assert proposal.decision_stage == "EXPOSURE_GATE"
    assert proposal.execution_id == "EXEC-TEST-PENDING-TIME-LOCK"


def test_exposure_limits_pending_order_locked_due_to_small_price_drift() -> None:
    """When active_pending_count >= 1, time lock expired (> 30s) but price drift < 1.0 * ATR, PENDING_ORDER_LOCKED is returned."""
    policy = SignalPolicy()
    tick = _make_tick(bid=2000.0, ask=2000.2)
    now = tick.timestamp

    # Lock created 45 seconds ago (> 30.0s)
    policy._locked_pending_time = now - timedelta(seconds=45)
    # Default eq price = round((2000-2.0) + 0.5 * ((2000.2+2.0) - (2000.0-2.0)), 2) = 2000.10
    # Set locked price close to 2000.10 so drift = abs(2000.10 - 2000.00) = 0.10 < 1.0 * ATR (2.0)
    policy._locked_pending_price = 2000.00

    proposal = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2005.00,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
        execution_id="EXEC-TEST-PENDING-DRIFT-LOCK",
    )

    assert proposal is not None
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "PENDING_ORDER_LOCKED"


def test_exposure_limits_pending_order_unlocked_when_time_and_drift_exceeded() -> None:
    """When active_pending_count >= 1, time lock expired (> 30s) AND price drift >= 1.0 * ATR, exposure gate unlocks (returns None)."""
    policy = SignalPolicy()
    tick = _make_tick(bid=2000.0, ask=2000.2)
    now = tick.timestamp

    # Lock created 45 seconds ago (> 30.0s)
    policy._locked_pending_time = now - timedelta(seconds=45)
    # Eq price = 2000.10. Set locked price = 1990.00 so drift = 10.10 >= 2.0 (1.0 * ATR)
    policy._locked_pending_price = 1990.00

    proposal = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2005.00,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
        execution_id="EXEC-TEST-PENDING-UNLOCKED",
    )

    assert proposal is None


def test_exposure_limits_equilibrium_calculation_with_completed_bars() -> None:
    """When completed_bars with >= 20 bars is provided, equilibrium price is derived from bar highs and lows."""
    policy = SignalPolicy()
    tick = _make_tick(bid=2000.0, ask=2000.2)
    now = tick.timestamp

    # 20 bars with lows ranging down to 1980.0 and highs ranging up to 2020.0
    # Eq price = 1980.0 + 0.5 * (2020.0 - 1980.0) = 2000.00
    bars = [DummyBar(low=1980.0, high=2020.0) for _ in range(20)]

    policy._locked_pending_time = now - timedelta(seconds=45)  # > 30s
    policy._locked_pending_price = 2000.00  # Eq price is 2000.00, drift = 0.0 < 2.0 ATR

    proposal_locked = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2005.00,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=bars,
        now=now,
        execution_id="EXEC-TEST-BARS-LOCK",
    )
    assert proposal_locked is not None
    assert proposal_locked.reason_code == "PENDING_ORDER_LOCKED"

    # Now change locked price to 1990.00 (drift = 10.0 >= 2.0 ATR -> unlocked)
    policy._locked_pending_price = 1990.00
    proposal_unlocked = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2005.00,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=bars,
        now=now,
        execution_id="EXEC-TEST-BARS-UNLOCK",
    )
    assert proposal_unlocked is None


def test_exposure_limits_symbol_and_magic_filtering() -> None:
    """Live tickets with non-matching symbol or magic number are ignored during same-level re-entry checks."""
    policy = SignalPolicy()
    tick = _make_tick(symbol="XAUUSD")
    now = tick.timestamp

    # Live tickets on different symbol or magic
    live_tickets = [
        {"symbol": "EURUSD", "magic": 888101, "price": 2000.00, "type": "POSITION"},
        {"symbol": "XAUUSD", "magic": 999999, "price": 2000.00, "type": "POSITION"},
    ]
    om = DummyOrderManager(live_tickets=live_tickets)

    proposal = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=1,
        active_pending_count=0,
        order_manager=om,
        live_tickets=live_tickets,
        target_entry_price=2000.10,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
        expected_symbol="XAUUSD",
        expected_magic=888101,
        execution_id="EXEC-TEST-SYMBOL-MAGIC",
    )

    # Since neither ticket matched symbol=XAUUSD AND magic=888101, is_same_level is False.
    # It proceeds to active_positions_count >= 1 -> returns MAX_EXPOSURE_REACHED (NOT SAME_LEVEL_REENTRY_BLOCKED).
    assert proposal is not None
    assert proposal.reason_code == "MAX_EXPOSURE_REACHED"


def test_exposure_limits_explicit_custom_symbol_and_magic() -> None:
    """When custom expected_symbol and expected_magic are passed, proximity check matches them correctly."""
    policy = SignalPolicy()
    tick = _make_tick(symbol="EURUSD")
    now = tick.timestamp

    live_tickets = [{"symbol": "EURUSD", "magic": 123456, "price": 1.1000, "type": "POSITION"}]
    om = DummyOrderManager(live_tickets=live_tickets)

    proposal = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=1,
        active_pending_count=0,
        order_manager=om,
        live_tickets=live_tickets,
        target_entry_price=1.1002,  # Proximity diff = 0.0002 < 0.50
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=0.0020,
        completed_bars=None,
        now=now,
        expected_symbol="EURUSD",
        expected_magic=123456,
        execution_id="EXEC-TEST-CUSTOM-IDENTITY",
    )

    assert proposal is not None
    assert proposal.reason_code == "SAME_LEVEL_REENTRY_BLOCKED"
