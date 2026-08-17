"""
Exit-behavior forensic regression suite (Phase 15: Exit-Behavior Audit & Repair).

Behavioral fixtures reproducing the eight canonical exit-management scenarios
required by the audit specification, plus execution-integrity regressions for
the concrete defects found in live forensics:

  D1. Strong reversal while BUY -> exit evaluation must CLOSE (bounded evidence).
  D2. Long drawdown with repeated BE opportunities -> thesis-invalidated exit.
  D3. AI probability flips against position -> evidence scores reflect it.
  D4. Regime invalidation -> thesis-invalidated exit path.
  D5. Fast MFE followed by large giveback -> giveback protection closes.
  D6. Early BE followed by full stop -> BE lock must remain active (safety).
  D7. Healthy continuation -> HOLD.
  D8. One isolated liquidity sweep -> no panic-close (noise guard).

Execution-integrity regressions (from live artifacts/audit.db forensics):
  R1. A losing position with hold_score < 30 and >60s age MUST dispatch a
      broker close (the live log showed state=LOSS_HARD_EXIT with NO close).
  R2. Giveback close must never be suppressed by the VOLATILITY_EXPANSION +
      breakeven-locked guard when the position has NO locked SL (the live
      log showed PROFIT_GIVEBACK_PROTECTION TRIGGERED followed by
      "Ticket not found" - close attempted but broker said ticket gone).
  R3. Time-in-trade decay: a position held beyond max_holding_seconds with
      no progress must produce an exit (S21/S22 path).
  R4. Min-loss EV must fire on deep drawdown with weak recovery (and must NOT
      fire when recovery evidence is strong) — regression for the EV inversion
      where recovery payoff grew with the loss and exit could never trigger.
"""

from datetime import UTC, datetime, timedelta

import pytest
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.configuration.config import AlgoConfig
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData
from nexus_scalp.execution.order_manager import (
    ExitMechanism,
    OrderLifecycleManager,
    PositionState,
)


def make_tick(bid, ask=None, ts=None):
    return TickData(
        symbol="XAUUSD",
        timestamp=ts or datetime.now(UTC),
        bid=bid,
        ask=ask if ask is not None else bid + 0.20,
        volume=1.0,
    )


def make_pos(ticket, order_type, entry, sl, tp, profit, volume=0.10):
    return Position(
        ticket=ticket,
        symbol="XAUUSD",
        type=order_type,
        volume=volume,
        price_open=entry,
        sl=sl,
        tp=tp,
        profit=profit,
        magic=888101,
    )


class MockMT5Adapter:
    """Mirrors tests/unit/test_adaptive_position_management.py harness."""

    def __init__(self):
        self.positions = []
        self.closed_tickets = []
        self.modifications = []
        self.deals = []

    def get_positions(self, symbol=None):
        return self.positions

    def get_pending_orders(self, symbol=None):
        return []

    def get_closed_deals_history(self, symbol, hours_back):
        return self.deals

    def close_position(self, ticket, volume=None):
        self.closed_tickets.append(ticket)
        self.positions = [p for p in self.positions if p.ticket != ticket]
        return True

    def modify_position(self, ticket, stop_loss, take_profit):
        self.modifications.append((ticket, stop_loss, take_profit))
        for i, p in enumerate(self.positions):
            if p.ticket == ticket:
                self.positions[i] = make_pos(
                    ticket=p.ticket,
                    order_type=p.type,
                    entry=p.price_open,
                    sl=stop_loss,
                    tp=take_profit,
                    profit=p.profit,
                    volume=p.volume,
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

    def get_account_info(self):
        return None

    def place_pending_order(self, symbol, order_type, volume, price, stop_loss, take_profit):
        self.modifications.append(("PENDING", symbol, volume, price))
        return 1


class FakeProbs:
    def __init__(self, buy, sell, no_trade=0.0, wait=0.0):
        self._v = [no_trade, buy, sell, wait]

    def squeeze(self):
        return self

    def tolist(self):
        return list(self._v)


def _manager(adapter):
    return OrderLifecycleManager(
        adapter=adapter,
        audit_repo=AuditRepository(db_url="sqlite:///:memory:"),
        algo_config=AlgoConfig(),
    )


def _prime(om, adapter, pos, tick):
    """First pass: register entry context and bootstrap state."""
    om.manage_active_positions("XAUUSD", tick)
    assert pos.ticket in om._entry_timestamps


# ---------------------------------------------------------------------------
# D1. Strong reversal while BUY -> exit evaluation must produce a close
# ---------------------------------------------------------------------------
def test_d1_strong_reversal_while_buy_exits():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(1, OrderType.BUY, 2000.00, 1995.00, 2010.00, -8.0)
    adapter.positions = [pos]
    tick = make_tick(1998.00)
    _prime(om, adapter, pos, tick)

    # AI flips hard to SELL: rel_sell_bias = 0.62, prob_sell > prob_buy + 0.10
    probs = FakeProbs(buy=0.25, sell=0.62, no_trade=0.13)
    tick2 = make_tick(1997.50)
    om.manage_active_positions("XAUUSD", tick2, probs=probs)
    assert pos.ticket in adapter.closed_tickets, "strong reversal must close BUY"
    assert om._forced_exit_mechanisms.get(pos.ticket) == ExitMechanism.AI_REVERSAL_EXIT


# ---------------------------------------------------------------------------
# D2. Long drawdown with repeated BE opportunities -> thesis-invalidated exit
# ---------------------------------------------------------------------------
def test_d2_long_drawdown_be_opportunities_exits():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(2, OrderType.BUY, 2000.00, 1994.00, 2012.00, -25.0, volume=0.10)
    adapter.positions = [pos]
    t0 = datetime.now(UTC)
    _prime(om, adapter, pos, make_tick(1997.00, ts=t0))

    # Hold for > max_holding_seconds (1800s default) at a loss with weak recovery
    t1 = t0 + timedelta(seconds=1900)
    probs = FakeProbs(buy=0.35, sell=0.45, no_trade=0.20)  # adverse
    om.manage_active_positions("XAUUSD", make_tick(1995.00, ts=t1), probs=probs)
    assert pos.ticket in adapter.closed_tickets, "prolonged drawdown must exit"


def _advance_pos(adapter, ticket, profit=None, bid=None, sl=None):
    """Update the mock position's floating pnl (and/or price) between passes."""
    for i, p in enumerate(adapter.positions):
        if p.ticket == ticket:
            kw = {}
            if profit is not None:
                kw["profit"] = profit
            if sl is not None:
                kw["sl"] = sl
            adapter.positions[i] = make_pos(
                ticket=p.ticket,
                order_type=p.type,
                entry=p.price_open,
                sl=kw.get("sl", p.sl),
                tp=p.tp,
                profit=kw.get("profit", p.profit),
                volume=p.volume,
            )
            return
    raise AssertionError(f"ticket {ticket} not in mock positions")


# ---------------------------------------------------------------------------
# D3. AI probability flips against position -> evidence reflects it
# ---------------------------------------------------------------------------
def test_d3_ai_probability_flip_visible_in_evidence():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(3, OrderType.BUY, 2000.00, 1994.00, 2012.00, -15.0)
    adapter.positions = [pos]
    _prime(om, adapter, pos, make_tick(1998.00))

    probs = FakeProbs(buy=0.20, sell=0.65, no_trade=0.15)
    ev = om._calculate_adaptive_evidence_scores(pos.ticket, adapter.positions[0], probs, None)
    assert ev["adverse_score"] > 0.6, "adverse_score must reflect sell dominance for a BUY"
    assert ev["continuation_score"] < 0.3

    # And the direction-flip path must close the BUY when the model turns hard SELL
    om.manage_active_positions("XAUUSD", make_tick(1997.00), probs=probs)
    assert 3 in adapter.closed_tickets, "hard model flip must close the position"
    assert om._forced_exit_mechanisms.get(3) == ExitMechanism.AI_REVERSAL_EXIT


# ---------------------------------------------------------------------------
# D4. Regime invalidation -> thesis-invalidated exit path
# ---------------------------------------------------------------------------
def test_d4_regime_invalidation_exit():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(4, OrderType.BUY, 2000.00, 1994.00, 2012.00, -30.0)
    adapter.positions = [pos]
    t0 = datetime.now(UTC)
    _prime(om, adapter, pos, make_tick(1997.00, ts=t0))

    # Position entered TRENDING (per entry_regimes) but market turned adverse:
    # regime change alone must not close, but combined with adverse excursion it must.
    t1 = t0 + timedelta(seconds=700)
    probs = FakeProbs(buy=0.25, sell=0.55, no_trade=0.20)
    om.manage_active_positions("XAUUSD", make_tick(1995.00, ts=t1), probs=probs)
    assert pos.ticket in adapter.closed_tickets


# ---------------------------------------------------------------------------
# D5. Fast MFE followed by large giveback -> giveback protection closes
# ---------------------------------------------------------------------------
def test_d5_fast_mfe_then_large_giveback_closes():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(5, OrderType.BUY, 2000.00, 1995.00, 2015.00, 30.0)
    adapter.positions = [pos]
    _prime(om, adapter, pos, make_tick(2000.30))

    # Peak $30 recorded
    assert om.get_protection_state(5).peak_win_usd >= 30.0

    # Giveback: now only $2.39 profit (retention ~8% < 30% floor)
    pos2 = make_pos(5, OrderType.BUY, 2000.00, 1995.00, 2015.00, 2.39)
    adapter.positions = [pos2]
    om.manage_active_positions("XAUUSD", make_tick(2000.02))
    assert 5 in adapter.closed_tickets, "giveback protection must close"
    assert om._forced_exit_mechanisms.get(5) == ExitMechanism.PROFIT_GIVEBACK_PROTECTION


# ---------------------------------------------------------------------------
# D6. Early BE followed by full stop -> BE lock must remain active (safety)
# ---------------------------------------------------------------------------
def test_d6_early_be_then_full_stop_be_lock_active():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(6, OrderType.BUY, 2000.00, 1996.00, 2015.00, 18.0)
    adapter.positions = [pos]
    _prime(om, adapter, pos, make_tick(2000.18))

    # BE lock applies: pnl $18 >= $15, price well above breakeven target so the
    # freeze-gap guard does not defer the modification.
    _advance_pos(adapter, 6, profit=18.0)
    om.manage_active_positions("XAUUSD", make_tick(2001.50))
    assert om._sl_modified_flags.get(6, False), "BE lock must have been applied"
    assert om._last_modify_sl.get(6, 0.0) > 2000.0, "BE lock must sit above entry"

    # Price collapses toward original SL: protective SL must NOT be loosened.
    # Snapshot the broker SL before the pass (the engine may close the position
    # on the collapse, which is correct; the invariant we assert is that a
    # protective move can never regress behind the confirmed BE lock).
    pos_collapsed = make_pos(6, OrderType.BUY, 2000.00, 2000.10, 2015.00, -8.0)
    assert om.is_sl_improvement(pos_collapsed, 1996.00) is False
    _advance_pos(adapter, 6, profit=-8.0, sl=2000.10)  # broker SL already at BE
    om.manage_active_positions("XAUUSD", make_tick(1999.80))
    # is_sl_improvement must never regress behind the confirmed BE lock
    assert om.is_sl_improvement(pos_collapsed, 1996.00) is False


# ---------------------------------------------------------------------------
# D7. Healthy continuation -> HOLD
# ---------------------------------------------------------------------------
def test_d7_healthy_continuation_holds():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(7, OrderType.BUY, 2000.00, 1996.00, 2015.00, 25.0)
    adapter.positions = [pos]
    _prime(om, adapter, pos, make_tick(2000.25))

    probs = FakeProbs(buy=0.60, sell=0.20, no_trade=0.20)
    om.manage_active_positions("XAUUSD", make_tick(2000.30), probs=probs)
    assert 7 not in adapter.closed_tickets, "healthy continuation must HOLD"
    assert om.get_protection_state(7).close_requested is False


# ---------------------------------------------------------------------------
# D8. One isolated liquidity sweep -> no panic-close (noise guard)
# ---------------------------------------------------------------------------
def test_d8_isolated_liquidity_sweep_no_panic_close():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(8, OrderType.BUY, 2000.00, 1995.00, 2015.00, 12.0)
    adapter.positions = [pos]
    _prime(om, adapter, pos, make_tick(2000.12))

    # A single sweep signal with healthy continuation must NOT close
    probs = FakeProbs(buy=0.55, sell=0.25, no_trade=0.20)
    om.manage_active_positions("XAUUSD", make_tick(2000.10), probs=probs)
    assert 8 not in adapter.closed_tickets, "one isolated sweep must not panic-close"


# ---------------------------------------------------------------------------
# R1. Losing position, hold_score < 30, age > 60s -> broker close dispatched
#     (the live log defect: state=LOSS_HARD_EXIT with NO close ever dispatched)
# ---------------------------------------------------------------------------
def test_r1_critical_hold_score_dispatch_close():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(9, OrderType.BUY, 2000.00, 1994.00, 2012.00, -60.0, volume=0.10)
    adapter.positions = [pos]
    t0 = datetime.now(UTC)
    _prime(om, adapter, pos, make_tick(1996.00, ts=t0))

    # Deep adverse excursion + weak recovery + age > 60s
    t1 = t0 + timedelta(seconds=90)
    probs = FakeProbs(buy=0.20, sell=0.60, no_trade=0.20)
    om.manage_active_positions("XAUUSD", make_tick(1994.50, ts=t1), probs=probs)
    assert 9 in adapter.closed_tickets, "hold_score < 30 with age>60s MUST close"


# ---------------------------------------------------------------------------
# R2. Giveback close must dispatch even when the VOLATILITY_EXPANSION +
#     breakeven-locked guard applies but NO locked SL exists.
#     (the live log: PROFIT GIVEBACK PROTECTION TRIGGERED, then broker
#      "Ticket not found" — the close was ATTEMPTED; the guard must not
#      suppress it when there is no protective SL on the broker side)
# ---------------------------------------------------------------------------
def test_r2_giveback_close_not_suppressed_without_locked_sl():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(10, OrderType.BUY, 2000.00, 1995.00, 2015.00, 40.0)
    adapter.positions = [pos]
    _prime(om, adapter, pos, make_tick(2000.40))

    assert om.get_protection_state(10).peak_win_usd >= 40.0
    # NO breakeven lock has been confirmed yet (was_sl_modified False)
    assert om.get_protection_state(10).was_sl_modified is False

    # Giveback in VOLATILITY_EXPANSION: without a locked SL the close MUST fire
    om._entry_regimes[10] = "VOLATILITY_EXPANSION"
    pos2 = make_pos(10, OrderType.BUY, 2000.00, 1995.00, 2015.00, 5.0)
    adapter.positions = [pos2]
    om.manage_active_positions("XAUUSD", make_tick(2000.05))
    assert 10 in adapter.closed_tickets, "giveback close must dispatch without locked SL"


# ---------------------------------------------------------------------------
# R4. Min-loss EV must fire on deep drawdown with weak recovery.
#     (The live defect: EV inversion — expected_recovery grew with the loss while
#      expected_loss shrank, so EV became MORE positive as the drawdown deepened.
#      Flagship ticket 152488669567: pnl -171.12, initial_risk 196.88,
#      rec 0.204, adv 0.542 → EV computed +55.86 vs threshold -29.53 → never
#      exited; closed at full SL with MAE -$180.78.)
#     The unit test targets the EV math directly (the arbitration layer applying
#     it is covered by R1); this pins the corrected payoff anchoring.
# ---------------------------------------------------------------------------
def test_r4_min_loss_ev_fires_on_deep_drawdown():
    adapter = MockMT5Adapter()
    om = _manager(adapter)

    # Seed the entry timestamp so the 60s spread-overcome grace is satisfied
    # (the function derives age from _entry_timestamps, not the clock).
    t_entry = datetime.now(UTC) - timedelta(seconds=61)
    om._entry_timestamps[12] = t_entry

    # Direct call with the exact flagship numbers.
    # The exit may fire via EV_BREACH or the DEEP_DRAWDOWN guard (both are the
    # corrected BUG-056 behavior); either is a valid "should exit" verdict.
    should, reason = om._evaluate_minimum_loss_optimization(
        12,
        -171.12,
        196.88,
        {"recovery_score": 0.204, "adverse_score": 0.542},
        now=datetime.now(UTC),
    )
    assert should, f"EV breach must fire on deep drawdown, got reason={reason!r}"
    assert "EV_BREACH" in reason or "DEEP_DRAWDOWN" in reason


def test_r4b_ev_does_not_fire_on_deep_drawdown_with_strong_recovery():
    """Healthy recovery evidence must NOT trigger the EV exit (no false panic)."""
    adapter = MockMT5Adapter()
    om = _manager(adapter)

    om._entry_timestamps[13] = datetime.now(UTC) - timedelta(seconds=61)

    should, _ = om._evaluate_minimum_loss_optimization(
        13,
        -171.12,
        196.88,
        {"recovery_score": 0.75, "adverse_score": 0.15},
        now=datetime.now(UTC),
    )
    assert should is False, "strong recovery must hold (no EV exit)"


# ---------------------------------------------------------------------------
# R5. Rule-matrix CLOSE verdict (RULE_* reason) must be honored by arbitration.
#     (Phase 15 discovery: a rule-matrix CLOSE — e.g. RULE_TIME_DECAY_CHOP_EXIT
#      after 240s at a loss — was silently swallowed by _arbitrate_decision's
#      hardcoded S-code emergency list and fell through to S60 HOLD. The old
#      min-loss EV masked this by firing early; the corrected EV exposed it.)
# ---------------------------------------------------------------------------
def test_r5_rule_matrix_close_honored_by_arbitration():
    adapter = MockMT5Adapter()
    om = _manager(adapter)

    # Direct arbitration call: rule-matrix CLOSE past the 60s grace must be honored
    t0 = datetime.now(UTC) - timedelta(seconds=300)
    om._entry_timestamps[14] = t0
    action, scenario = om._arbitrate_decision(
        ticket=14,
        pos=make_pos(14, OrderType.BUY, 2000.00, 1995.00, 2015.00, -50.0, volume=1.0),
        legacy_action="CLOSE",
        legacy_scenario="RULE_TIME_DECAY_CHOP_EXIT",
        adaptive_state=PositionState.LOSS_RECOVERY_CANDIDATE,
        current_pnl_usd=-50.0,
        evidence={"recovery_score": 0.28, "adverse_score": 0.40},
        now=datetime.now(UTC),
    )
    assert action == "CLOSE"
    assert "RULE_TIME_DECAY_CHOP_EXIT" in scenario


def test_r5b_rule_matrix_close_still_grace_period_protected():
    """Even a rule-matrix CLOSE must respect the 60s spread-overcome grace."""
    adapter = MockMT5Adapter()
    om = _manager(adapter)

    t0 = datetime.now(UTC) - timedelta(seconds=10)  # inside the 60s grace
    om._entry_timestamps[15] = t0
    action, scenario = om._arbitrate_decision(
        ticket=15,
        pos=make_pos(15, OrderType.BUY, 2000.00, 1995.00, 2015.00, -50.0, volume=1.0),
        legacy_action="CLOSE",
        legacy_scenario="RULE_TIME_DECAY_CHOP_EXIT",
        adaptive_state=PositionState.LOSS_RECOVERY_CANDIDATE,
        current_pnl_usd=-50.0,
        evidence={"recovery_score": 0.28, "adverse_score": 0.40},
        now=datetime.now(UTC),
    )
    assert action == "HOLD", "60s grace must still suppress a fresh rule-matrix close"


# ---------------------------------------------------------------------------
# R3. Time-in-trade decay exit (S21/S22 path)
# ---------------------------------------------------------------------------
def test_r3_time_in_trade_decay_exit():
    adapter = MockMT5Adapter()
    om = _manager(adapter)
    pos = make_pos(11, OrderType.BUY, 2000.00, 1995.00, 2015.00, -3.0, volume=0.10)
    adapter.positions = [pos]
    t0 = datetime.now(UTC)
    _prime(om, adapter, pos, make_tick(2000.00, ts=t0))

    # Stagnant and underwater far beyond max_holding_seconds (1800s)
    t1 = t0 + timedelta(seconds=2800)
    _advance_pos(adapter, 11, profit=-3.0)
    om.manage_active_positions("XAUUSD", make_tick(1999.70, ts=t1))
    assert 11 in adapter.closed_tickets, "time-in-trade decay must exit a stagnant trade"
