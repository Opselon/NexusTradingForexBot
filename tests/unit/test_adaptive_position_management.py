from datetime import UTC, datetime, timedelta

import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData
from nexus_scalp.execution.order_manager import OrderLifecycleManager, PositionState


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
        profit=30.74,  # reached +$30.74 peak profit
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
        profit=-5.00,  # negative PnL now
        magic=888101,
    )
    adapter.positions = [pos_giveback]
    tick_giveback = TickData(
        symbol="XAUUSD", timestamp=now + timedelta(seconds=1), bid=1999.95, ask=1999.97, volume=1.0
    )

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
        sl=1999.50,  # Tight SL ($0.50 risk price * 100 contract_size * 1.0 volume = $50.00 risk)
        tp=2020.00,
        profit=-1.00,  # Tight loss of $1.00
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
    probs_conf = torch.tensor([[0.01, 0.95, 0.04]])  # high buy prob

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
        profit=35.00,  # peak profit
        magic=888101,
    )
    adapter.positions = [pos_peak]
    om.manage_active_positions("XAUUSD", tick)  # peak recorded

    pos_fail = Position(
        ticket=888,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=1999.50,
        tp=2020.00,
        profit=-5.00,  # complete erosion
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
        sl=1995.00,  # Initial risk = $5.00 * 100.0 * 1.0 = $500.00
        tp=2020.00,
        profit=-10.00,  # Initial loss at recovery entry = $10.00
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
        profit=-270.00,  # Consumes $260.00 of budget (> $250.00)
        magic=888101,
    )
    adapter.positions = [pos_deep_loss]

    tick_loss = TickData(
        symbol="XAUUSD", timestamp=now + timedelta(seconds=1), bid=1997.30, ask=1997.32, volume=1.0
    )
    om.manage_active_positions("XAUUSD", tick_loss, probs=probs)

    # Budget exhausted -> Position closed immediately!
    assert 999 in adapter.closed_tickets
    assert om._position_states[999] == PositionState.LOSS_HARD_EXIT


def test_bug313_flat_model_recovery_not_pinned_to_exit_pressure():
    """BUG-313 (2026-09-22, live forensics): with a near-uniform serving head
    (p_buy mean 0.391, p_sell 0.358, p_no_trade 0.251 — buy/sell spread std
    0.042) the recovery_score fed to _evaluate_candidate_state is
    0.70*continuation + 0.30*recovery_velocity, arithmetically pinned near
    0.25-0.28 regardless of market action while the position is underwater.
    The < 0.30 LOSS_EXIT_PRESSURE threshold was therefore always true:
    46/46 live losing trades exited at exactly 60-65s with 8/14 showing
    MFE $0.00 (the reward leg never developed). The state verdict must now
    blend the realized pnl slope so a genuinely bouncing position is not
    classified as exit pressure purely because the classifier is
    uninformative, and a deteriorating one still is."""
    from nexus_scalp.domain.enums import OrderType
    from nexus_scalp.domain.models import Position
    from nexus_scalp.execution.order_manager import PositionState

    adapter = MockMT5Adapter()
    audit_repo = AuditRepository(db_url="sqlite:///:memory:")
    om = OrderLifecycleManager(adapter=adapter, audit_repo=audit_repo)

    pos = Position(
        ticket=3130,
        symbol="XAUUSD",
        type=OrderType.SELL,
        volume=0.07,
        price_open=4346.28,
        sl=4349.63,
        tp=0.0,
        profit=-9.0,
        magic=888101,
    )
    adapter.positions = [pos]
    om._entry_timestamps[3130] = datetime.now(UTC) - timedelta(seconds=90)

    # Live evidence shape: continuation == OWN-side raw prob for a SELL,
    # recovery_velocity ~ 0 while underwater. recovery_score ~ 0.251.
    flat_evidence = {
        "continuation_score": 0.358,
        "adverse_score": 0.391,
        "recovery_score": 0.70 * 0.358 + 0.30 * 0.0,
    }
    assert flat_evidence["recovery_score"] < 0.30, (
        "precondition: flat-head recovery is below the pressure line"
    )

    # A position that is flat underwater must still read as exit pressure:
    # no model signal, no realized bounce -> no reason to keep holding.
    flat = om._evaluate_candidate_state(3130, pos, flat_evidence, {"pnl_slope": 0.0})
    assert flat == PositionState.LOSS_EXIT_PRESSURE

    # The SAME flat model read, but the position is bouncing back in price:
    # the trajectory term must lift it out of exit pressure.
    bouncing = om._evaluate_candidate_state(3130, pos, flat_evidence, {"pnl_slope": 0.25})
    assert bouncing != PositionState.LOSS_EXIT_PRESSURE
    assert bouncing in (PositionState.LOSS_RECOVERY_CANDIDATE, PositionState.LOSS_RECOVERY_FAILING)

    # A deteriorating position (negative slope) stays under pressure and
    # never gets promoted by the trajectory term.
    deteriorating = om._evaluate_candidate_state(3130, pos, flat_evidence, {"pnl_slope": -0.5})
    assert deteriorating == PositionState.LOSS_EXIT_PRESSURE
