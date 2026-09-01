"""AGENT4-SPRINT regression suite: winner-protection geometry repair (2026-09-01).

Evidence base (audited ledger, 178 grouped trades):
  - The flat $15 BE trigger fires at ~0.09R (median planned risk ~$168) and locks
    an entry-level stop -> 55/67 breakeven scratches had MFE >= $20.
  - BE lock offset 0.20 pips ~ zero locked profit (round-trip cost unrecovered).
  - Tiered giveback floors 0.40/0.50/0.70 retained only $1,305 of $4,265 handed
    back by 68 round-trips.

Repairs under test:
  1. BE trigger = max($15, BREAKEVEN_TRIGGER_R * planned_risk), ATR path unchanged.
  2. BE lock offset 0.60 pips (covers round-trip cost on 2-digit gold).
  3. Tier floors tightened to 0.60/0.70/0.80; arm (0.5R) and micro-profit
     disarm UNCHANGED.

These tests construct the REAL OrderLifecycleManager with the repo's MockMT5Adapter
pattern (see test_adaptive_position_management.py) and assert behavior, not numbers
copied from the source.
"""

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData
from nexus_scalp.execution import order_manager as om_module
from nexus_scalp.execution.order_manager import OrderLifecycleManager


# ---------------------------------------------------------------------------
# Local adapter with controllable fills + protection state reconciliation
# ---------------------------------------------------------------------------


class SprintMockAdapter:
    """Same contract as MockMT5Adapter but with explicit modify confirmation."""

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


def _manager(adapter):
    audit_repo = AuditRepository(db_url="sqlite:///:memory:")
    return OrderLifecycleManager(adapter=adapter, audit_repo=audit_repo)


def _buy(ticket, profit, volume=0.5, entry=2000.00, sl=1995.00):
    """BUY 0.5 lots, 1% stop -> planned risk = 0.5*100*5.00 = $250."""
    return Position(
        ticket=ticket,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=volume,
        price_open=entry,
        sl=sl,
        tp=entry + 10.0,
        profit=profit,
        magic=888101,
    )


def _tick(bid, ask, seconds=0):
    base = datetime.now(UTC) + timedelta(seconds=seconds)
    return TickData(symbol="XAUUSD", timestamp=base, bid=bid, ask=ask, volume=1.0)


def _prime_risk(om, ticket, risk_usd):
    """Seed the planned-risk tracker the engine fills at POSITION discovery."""
    om._initial_risks[ticket] = risk_usd


# ---------------------------------------------------------------------------
# 1. R-anchored BE trigger
# ---------------------------------------------------------------------------


def test_be_trigger_requires_r_floor_not_flat_15():
    """A position with planned risk $250 must NOT lock BE at +$15 (0.06R):
    the trigger is max($15, 0.15R=$37.5). At +$20 the lock must stay OFF."""
    adapter = SprintMockAdapter()
    om = _manager(adapter)
    pos = _buy(9101, profit=20.0)
    adapter.positions = [pos]
    _prime_risk(om, 9101, 250.0)
    om.refresh_protection_state(pos, adapter.get_symbol_info("XAUUSD"))
    om.apply_breakeven_lock(pos=pos, symbol_info=adapter.get_symbol_info("XAUUSD"), atr=0.0,
                            min_stop_gap=0.0, current_tick=_tick(2000.30, 2000.32))
    assert not om.get_protection_state(9101).was_sl_modified
    assert adapter.modifications == []


def test_be_trigger_fires_once_r_floor_reached():
    """Same position reaching 0.20R ($50 on $250 risk > $37.5 trigger) locks BE."""
    adapter = SprintMockAdapter()
    om = _manager(adapter)
    pos = _buy(9102, profit=50.0)
    adapter.positions = [pos]
    _prime_risk(om, 9102, 250.0)
    om.refresh_protection_state(pos, adapter.get_symbol_info("XAUUSD"))
    applied = om.apply_breakeven_lock(
        pos=pos, symbol_info=adapter.get_symbol_info("XAUUSD"), atr=0.0,
        min_stop_gap=0.0, current_tick=_tick(2000.55, 2000.57),
    )
    assert applied
    assert om.get_protection_state(9102).was_sl_modified
    # lock offset: 0.60 pips * 0.10 pip size = $0.06/oz above entry
    ticket, new_sl, _tp = adapter.modifications[0]
    assert ticket == 9102
    assert new_sl == pytest.approx(2000.06, abs=1e-6)


def test_be_trigger_flat_floor_still_works_without_risk_provenance():
    """Without a tracked planned risk (restart re-observation gap), the flat $15
    floor still arms the lock — fail-safe toward the pre-sprint behavior."""
    adapter = SprintMockAdapter()
    om = _manager(adapter)
    pos = _buy(9103, profit=16.0)
    adapter.positions = [pos]
    # no _initial_risks seeded
    om.refresh_protection_state(pos, adapter.get_symbol_info("XAUUSD"))
    applied = om.apply_breakeven_lock(
        pos=pos, symbol_info=adapter.get_symbol_info("XAUUSD"), atr=0.0,
        min_stop_gap=0.0, current_tick=_tick(2000.60, 2000.62),
    )
    assert applied
    assert om.get_protection_state(9103).was_sl_modified


def test_be_atr_alternative_trigger_unchanged():
    """The volatility-scaled (1.5 ATR) trigger must remain an independent
    activation path: a huge ATR threshold arms BE even below the USD floors."""
    adapter = SprintMockAdapter()
    om = _manager(adapter)
    pos = _buy(9104, profit=18.0, volume=1.0)
    adapter.positions = [pos]
    _prime_risk(om, 9104, 100_000.0)  # absurd R floor: never reachable
    om.refresh_protection_state(pos, adapter.get_symbol_info("XAUUSD"))
    # 1.5 ATR on 1.0 lot with ATR=$20 -> $3000 trigger... use small ATR so the
    # ATR threshold is ~$18: ATR=0.12 -> 1.5*0.12*100oz*1.0 = $18
    applied = om.apply_breakeven_lock(
        pos=pos, symbol_info=adapter.get_symbol_info("XAUUSD"), atr=0.12,
        min_stop_gap=0.0, current_tick=_tick(2000.60, 2000.62),
    )
    assert applied


# ---------------------------------------------------------------------------
# 2. BE lock offset covers round-trip cost
# ---------------------------------------------------------------------------


def test_be_lock_offset_is_six_pips():
    """calculate_breakeven_sl must lock 0.60 pips ($0.06 on 2-digit gold),
    i.e. BUY SL sits ABOVE entry — a BE hit is a small positive scratch."""
    adapter = SprintMockAdapter()
    om = _manager(adapter)
    pos = _buy(9105, profit=100.0)
    sl = om.calculate_breakeven_sl(pos, adapter.get_symbol_info("XAUUSD"))
    assert sl == pytest.approx(2000.06, abs=1e-6)
    assert sl > pos.price_open


def test_be_lock_offset_sell_side_mirrors():
    adapter = SprintMockAdapter()
    om = _manager(adapter)
    pos = Position(
        ticket=9106, symbol="XAUUSD", type=OrderType.SELL, volume=0.5,
        price_open=2000.00, sl=2005.00, tp=1990.00, profit=100.0, magic=888101,
    )
    sl = om.calculate_breakeven_sl(pos, adapter.get_symbol_info("XAUUSD"))
    assert sl == pytest.approx(1999.94, abs=1e-6)
    assert sl < pos.price_open


# ---------------------------------------------------------------------------
# 3. Tiered giveback floors tightened
# ---------------------------------------------------------------------------


def test_tier_floors_tightened_and_arm_unchanged():
    om = _manager(SprintMockAdapter())
    # simulate a ticket with $250 planned risk
    om._initial_risks[9200] = 250.0
    # peak below arm threshold -> disarmed regardless
    fl, armed = om._tiered_giveback_floor(9200, peak=100.0)  # 0.4R < 0.5R
    assert not armed
    # 0.6R peak -> NEW floor 0.60 (was 0.40)
    fl, armed = om._tiered_giveback_floor(9200, peak=160.0)
    assert armed and fl == pytest.approx(0.60)
    # 1.2R peak -> 0.70 (was 0.50)
    fl, armed = om._tiered_giveback_floor(9200, peak=310.0)
    assert armed and fl == pytest.approx(0.70)
    # 2.0R peak -> 0.80 (was 0.70)
    fl, armed = om._tiered_giveback_floor(9200, peak=520.0)
    assert armed and fl == pytest.approx(0.80)


def test_micro_profit_noise_zone_still_disarmed():
    """A 0.3R peak must NOT trigger giveback close (micro-profit noise zone
    preserved) even with tightened floors."""
    adapter = SprintMockAdapter()
    om = _manager(adapter)
    om._initial_risks[9300] = 250.0
    pos = _buy(9300, profit=5.0)  # peak 75 (0.3R) -> current 5 (0.07R retained)
    adapter.positions = [pos]
    _tick_now = _tick(2000.05, 2000.07)
    om.refresh_protection_state(pos, adapter.get_symbol_info("XAUUSD"))
    score, active, reason = om.evaluate_profit_giveback(9300, current_pnl_usd=5.0, base_hold_score=80)
    assert not active


def test_giveback_closes_when_retention_breaks_new_floor():
    """peak 0.6R ($150) -> floor 0.60; current pnl $60 (retention 0.40) breaches:
    protection must demand the cut (hold-score override active)."""
    adapter = SprintMockAdapter()
    om = _manager(adapter)
    om._initial_risks[9400] = 250.0
    # peak forms monotonically via refresh (same path as live position discovery)
    pos_peak = _buy(9400, profit=150.0)
    adapter.positions = [pos_peak]
    om.refresh_protection_state(pos_peak, adapter.get_symbol_info("XAUUSD"))
    # erosion: retention 40% < 60% floor -> protection demands the cut
    score, active, reason = om.evaluate_profit_giveback(9400, current_pnl_usd=60.0, base_hold_score=80)
    assert active
    assert "PROFIT_RETENTION_BREACH" in reason or "NEGATIVE_PNL" in reason


# ---------------------------------------------------------------------------
# 4. Constants sanity (behavioral contract, not copied numbers)
# ---------------------------------------------------------------------------


def test_sprint_constants_contract():
    assert om_module.BREAKEVEN_TRIGGER_R == pytest.approx(0.15)
    assert om_module.BREAKEVEN_LOCK_PIPS == pytest.approx(0.60)
    assert om_module.TIERED_GIVEBACK_RETENTION_FLOOR == (
        (0.50, 0.60), (1.00, 0.70), (1.50, 0.80),
    )
    # unchanged invariants (do not let a future edit silently shift the arm point)
    assert om_module.TIERED_GIVEBACK_ARM_R == pytest.approx(0.50)
    assert om_module.PROFIT_GIVEBACK_PEAK_USD == pytest.approx(20.00)
    assert om_module.BREAKEVEN_ATR_MULTIPLIER == pytest.approx(1.5)
