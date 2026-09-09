"""TASK-EXIT-SEPARATION (A8, wave 3): exit-policy separation regression suite.

Pins four behaviors WITHOUT tuning any threshold:

(a) BE lock may ONLY move the SL — no close dispatch can originate from the
    breakeven path (ProtectionEngine.apply_breakeven_lock, the router
    BREAK_EVEN branch), even when the broker REJECTS the modification.
(b) TIERED_GIVEBACK_ARM_R (0.50) becomes configurable via
    AlgoConfig.giveback_arm_r; the module constant stays the fallback so the
    default behavior is bit-identical (default-equivalence, not a retune).
(c) The AI direction-flip exit (close+fast-reversal) is SUSPENDED by default
    behind AlgoConfig.ai_flip_exit_enabled=False; each suppressed flip emits
    ONE rate-limited structured WARNING per ticket; the flag is read LIVE so
    an operator re-enables without a code change. The deterministic
    protection chain (giveback -> breakeven -> trailing) stays the sole exit
    authority and is untouched by the suspension.
(d) ATR_TRAILING_MULTIPLIER (1.15) becomes configurable via
    AlgoConfig.trail_atr_multiplier with the default preserved.

Project test conventions: monkeypatched module logger instead of caplog
(BUG-112/118), real OrderLifecycleManager + mock adapter, behavior asserts.
"""

import math
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.configuration.config import AlgoConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData
from nexus_scalp.execution import order_manager as om_module
from nexus_scalp.execution.execution_plan import ExecutionPlan
from nexus_scalp.execution.lifecycle import protection as protection_module
from nexus_scalp.execution.order_manager import (
    ATR_TRAILING_MULTIPLIER,
    TIERED_GIVEBACK_ARM_R,
    OrderLifecycleManager,
)
from nexus_scalp.execution.position_states import PositionState
from nexus_scalp.signals.policy import AI_REVERSAL_REASON

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class SpyMT5Adapter:
    """Records every broker call; can poison close so a violation fails loudly."""

    def __init__(self, close_poison: bool = False) -> None:
        self.close_poison = close_poison
        self.positions: list[Position] = []
        self.closed_tickets: list[int] = []
        self.modifications: list[tuple[int, float, float]] = []
        self.pending_orders: list[dict] = []
        self.calls: list[str] = []
        self.get_positions_calls = 0
        self.modify_result = True
        self.close_result = True

    def get_positions(self, symbol=None):
        self.calls.append("get_positions")
        self.get_positions_calls += 1
        return list(self.positions)

    def get_pending_orders(self, symbol=None):
        self.calls.append("get_pending_orders")
        return []

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

    def get_closed_deals_history(self, symbol, hours_back):
        return []

    def close_position(self, ticket, volume=None):
        self.calls.append("close_position")
        if self.close_poison:
            raise AssertionError("CLOSE DISPATCHED FROM A PATH THAT MUST NOT CLOSE")
        if not self.close_result:
            return False
        self.closed_tickets.append(ticket)
        self.positions = [p for p in self.positions if p.ticket != ticket]
        return True

    def modify_position(self, ticket, stop_loss, take_profit):
        self.calls.append("modify_position")
        if not self.modify_result:
            return False
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

    def place_pending_order(self, **kwargs):
        self.calls.append("place_pending_order")
        self.pending_orders.append(kwargs)
        return True


def _manager(algo_config=None, adapter=None):
    return OrderLifecycleManager(
        adapter=adapter if adapter is not None else SpyMT5Adapter(),
        audit_repo=AuditRepository(db_url="sqlite:///:memory:"),
        algo_config=algo_config,
    )


def _buy(ticket, profit=8.0, entry=2000.00, sl=1995.00, volume=0.10):
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


def _tick(bid, ask=None):
    return TickData(
        symbol="XAUUSD",
        timestamp=datetime.now(UTC),
        bid=bid,
        ask=ask if ask is not None else bid + 0.20,
        volume=1.0,
    )


def _assert_broker_calls_modify_only(adapter):
    """Shared BE-path detector: close must never appear in the broker call log."""
    assert "close_position" not in adapter.calls, (
        f"close dispatch originated from the breakeven path: {adapter.calls}"
    )


class _Probs:
    """Duck-typed model probability container: squeeze().tolist() contract."""

    def __init__(self, values):
        self._values = list(values)

    def squeeze(self):
        return self

    def tolist(self):
        return list(self._values)


class _WarnProbe:
    """Structured-logger probe (project convention: no caplog, BUG-112/118)."""

    def __init__(self):
        self.warnings: list[tuple] = []

    def warning(self, *args, **kwargs):
        self.warnings.append(args)

    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


def _prime_trackers(om, ticket, peak=5.0):
    """Seed the per-ticket tracking views so the protection chain can run."""
    om._peak_profit_usd[ticket] = peak
    om._peak_drawdown_usd[ticket] = 0.0
    om._time_in_profit_sec[ticket] = 0.0
    om._time_in_drawdown_sec[ticket] = 0.0


def _run_chain(om, pos, adapter, tick, probs, holding=30.0, atr=0.0):
    """Drive the S6 STEP-C protection/AI-flip chain stage for one position."""
    ticket = pos.ticket
    _prime_trackers(om, ticket, peak=min(max(pos.profit, 0.0), 5.0))
    return om._run_protection_chain(
        pos=pos,
        ticket=ticket,
        now=datetime.now(UTC),
        current_time=1000.0,
        protection=om.get_protection_state(ticket),
        price_current=tick.bid,
        hold_score=40,
        base_hold_score=40,
        invalidate_reasons=[],
        atr=atr,
        spread=0.20,
        min_stop_gap=0.0,
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        smart_metrics={},
        evidence={},
        pnl_features={},
        probs=probs,
        feature_vector=None,
        regime_state=None,
        current_tick=tick,
        holding_duration=holding,
        debounced_state=PositionState.PROFIT_UNPROTECTED,
        prot_score=0.0,
    )


# ---------------------------------------------------------------------------
# (a) BE lock moves the SL ONLY — close dispatch is impossible from that path
# ---------------------------------------------------------------------------


def test_be_lock_usd_trigger_never_closes():
    """Flat $15 trigger path: the only broker interaction is modify_position."""
    adapter = SpyMT5Adapter(close_poison=True)
    om = _manager(adapter=adapter)
    pos = _buy(9101, profit=20.0)
    adapter.positions = [pos]
    om.refresh_protection_state(pos, adapter.get_symbol_info("XAUUSD"))
    applied = om.apply_breakeven_lock(
        pos=pos,
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        atr=0.0,
        min_stop_gap=0.0,
        current_tick=_tick(2000.55, 2000.57),
    )
    assert applied
    assert om.get_protection_state(9101).was_sl_modified
    assert [c for c in adapter.modifications if c[0] == 9101]
    _assert_broker_calls_modify_only(adapter)


def test_be_lock_r_and_atr_trigger_paths_never_close():
    """R-floor and ATR trigger flavors are equally close-free."""
    adapter = SpyMT5Adapter(close_poison=True)
    om = _manager(adapter=adapter)
    om._initial_risks[9102] = 250.0  # 0.15R floor = $37.5
    pos = _buy(9102, profit=50.0, volume=0.5)
    adapter.positions = [pos]
    om.refresh_protection_state(pos, adapter.get_symbol_info("XAUUSD"))
    assert om.apply_breakeven_lock(
        pos=pos,
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        atr=0.0,
        min_stop_gap=0.0,
        current_tick=_tick(2000.55, 2000.57),
    )
    # ATR alternative trigger on a second ticket (1.5*ATR path).
    pos2 = _buy(9103, profit=18.0, volume=1.0)
    adapter.positions = [pos, pos2]
    om._initial_risks[9103] = 100_000.0  # absurd R floor: ATR path must arm
    om.refresh_protection_state(pos2, adapter.get_symbol_info("XAUUSD"))
    assert om.apply_breakeven_lock(
        pos=pos2,
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        atr=0.12,
        min_stop_gap=0.0,
        current_tick=_tick(2000.55, 2000.57),
    )
    assert len(adapter.modifications) == 2
    _assert_broker_calls_modify_only(adapter)


def test_be_lock_rejected_modify_still_never_closes():
    """Broker rejects the BE modification: retry stays possible, NO close
    escalation may appear — the BE path's only authority is the SL."""
    adapter = SpyMT5Adapter(close_poison=True)
    adapter.modify_result = False
    om = _manager(adapter=adapter)
    pos = _buy(9104, profit=20.0)
    adapter.positions = [pos]
    om.refresh_protection_state(pos, adapter.get_symbol_info("XAUUSD"))
    assert not om.apply_breakeven_lock(
        pos=pos,
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        atr=0.0,
        min_stop_gap=0.0,
        current_tick=_tick(2000.55),
    )
    assert not om.get_protection_state(9104).was_sl_modified
    assert adapter.modifications == []
    _assert_broker_calls_modify_only(adapter)


def test_router_break_even_branch_never_closes():
    """The decision-router BREAK_EVEN dispatch (order_manager) is also
    SL-only: poison close proves no close can leak from that branch."""
    adapter = SpyMT5Adapter(close_poison=True)
    om = _manager(adapter=adapter)
    pos = _buy(9105, profit=20.0)
    om.refresh_protection_state(pos, adapter.get_symbol_info("XAUUSD"))
    plan = ExecutionPlan(
        action="BREAK_EVEN",
        scenario="S47_STANDARD_BREAK_EVEN_LOCK",
        ticket=9105,
        symbol="XAUUSD",
    )
    om._execute_position_action(
        plan=plan,
        pos=pos,
        ticket=9105,
        now=datetime.now(UTC),
        atr=0.0,
        spread=0.20,
        min_stop_gap=0.0,
        price_current=2000.55,
        rule_target_sl=0.0,
        hold_score=40,
        protection=om.get_protection_state(9105),
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        current_tick=_tick(2000.55, 2000.57),
        scenario="S47_STANDARD_BREAK_EVEN_LOCK",
        action="BREAK_EVEN",
    )
    assert any(c[0] == 9105 for c in adapter.modifications)
    _assert_broker_calls_modify_only(adapter)


def test_be_no_close_detector_is_not_vacuous():
    """The pin's detector actually fires on a close call (guards a no-op pin)."""
    adapter = SpyMT5Adapter()
    adapter.calls.append("modify_position")
    _assert_broker_calls_modify_only(adapter)  # modify-only passes
    adapter.calls.append("close_position")
    with pytest.raises(AssertionError, match="close dispatch"):
        _assert_broker_calls_modify_only(adapter)


# ---------------------------------------------------------------------------
# (b) giveback_arm_r: configurable arm threshold, default-equivalent
# ---------------------------------------------------------------------------


def test_giveback_arm_default_matches_module_constant():
    """With default AlgoConfig the tiered arm behaves EXACTLY like HEAD."""
    om = _manager(AlgoConfig())
    om._initial_risks[1] = 250.0
    min_retention = om_module.PROFIT_GIVEBACK_MIN_RETENTION
    # 0.4R peak -> DISARMED (micro-profit noise zone).
    assert om._tiered_giveback_floor(1, 100.0) == (min_retention, False)
    # 0.5R peak -> armed, first tier floor.
    assert om._tiered_giveback_floor(1, 125.0) == (0.60, True)
    # 1.2R -> second tier; 2.0R -> third tier.
    assert om._tiered_giveback_floor(1, 300.0) == (0.70, True)
    assert om._tiered_giveback_floor(1, 500.0) == (0.80, True)
    # No risk provenance -> fallback absolute floor, armed.
    assert om._tiered_giveback_floor(2, 100.0) == (min_retention, True)


def test_giveback_arm_r_override_disarms_and_arms():
    """A config override moves ONLY the arm point, never the tier floors."""
    disarmed_cfg = AlgoConfig(giveback_arm_r=0.80)
    om = _manager(disarmed_cfg)
    om._initial_risks[1] = 250.0
    min_retention = om_module.PROFIT_GIVEBACK_MIN_RETENTION
    assert om._tiered_giveback_floor(1, 150.0) == (min_retention, False)  # 0.6R
    assert om._tiered_giveback_floor(1, 225.0) == (0.60, True)  # 0.9R armed

    armed_cfg = AlgoConfig(giveback_arm_r=0.30)
    om2 = _manager(armed_cfg)
    om2._initial_risks[1] = 250.0
    # 0.4R peak is now ARMED (was disarmed at HEAD); it sits BELOW the first
    # 0.50R tier, so the floor is the absolute minimum retention — correct.
    assert om2._tiered_giveback_floor(1, 100.0) == (min_retention, True)
    # Crossing the first tier keeps the tier floor.
    assert om2._tiered_giveback_floor(1, 125.0) == (0.60, True)


def test_giveback_arm_r_is_read_live():
    """The threshold is re-read from config every evaluation: an operator can
    change it without reconstructing the manager."""
    cfg = AlgoConfig()
    om = _manager(cfg)
    om._initial_risks[1] = 250.0
    assert om._tiered_giveback_floor(1, 100.0) == (
        om_module.PROFIT_GIVEBACK_MIN_RETENTION,
        False,
    )
    cfg.giveback_arm_r = 0.30
    # 0.4R peak: ARMED now (was disarmed at HEAD); below the first 0.50R tier
    # so the floor is the absolute minimum retention (0.30), not a tier floor.
    assert om._tiered_giveback_floor(1, 100.0) == (
        om_module.PROFIT_GIVEBACK_MIN_RETENTION,
        True,
    )
    # Crossing the first tier (0.50R) yields the first-tier floor 0.60.
    assert om._tiered_giveback_floor(1, 125.0) == (0.60, True)


@pytest.mark.parametrize("bad", [0.0, -0.5, float("nan"), float("inf")])
def test_giveback_arm_r_invalid_value_falls_back_to_constant(bad):
    """A broken override (0/negative/non-finite) must fall back to 0.50, not
    disable or permanently arm the protection."""
    cfg = AlgoConfig()
    cfg.giveback_arm_r = bad
    om = _manager(cfg)
    om._initial_risks[1] = 250.0
    if math.isnan(bad) or math.isinf(bad) or bad <= 0.0:
        # 0.4R peak with fallback 0.50 arm -> disarmed exactly like HEAD.
        assert om._tiered_giveback_floor(1, 100.0) == (
            om_module.PROFIT_GIVEBACK_MIN_RETENTION,
            False,
        )


# ---------------------------------------------------------------------------
# (d) trail_atr_multiplier: configurable trail distance, default-equivalent
# ---------------------------------------------------------------------------


def test_trail_default_multiplier_matches_constant():
    """Default trail distance is price -/+ ATR * 1.15 exactly like HEAD."""
    adapter = SpyMT5Adapter()
    om = _manager(AlgoConfig(), adapter=adapter)
    pos = _buy(9201, profit=30.0)
    adapter.positions = [pos]
    applied = om.apply_atr_trailing_stop(
        pos=pos,
        price_current=2005.0,
        atr=2.0,
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        min_stop_gap=0.0,
        current_tick=None,
    )
    assert applied
    assert ATR_TRAILING_MULTIPLIER == pytest.approx(1.15)
    ticket, new_sl, _tp = adapter.modifications[0]
    assert ticket == 9201
    assert new_sl == pytest.approx(2005.0 - 2.0 * 1.15, abs=1e-9)


def test_trail_atr_multiplier_override_and_live_read():
    """Override changes ONLY the distance; it is read live per modification."""
    cfg = AlgoConfig()
    adapter = SpyMT5Adapter()
    om = _manager(cfg, adapter=adapter)
    pos = _buy(9202, profit=30.0)
    adapter.positions = [pos]
    om.apply_atr_trailing_stop(
        pos=pos,
        price_current=2005.0,
        atr=2.0,
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        min_stop_gap=0.0,
        current_tick=None,
    )
    assert adapter.modifications[0][1] == pytest.approx(2005.0 - 2.0 * 1.15, abs=1e-9)

    cfg.trail_atr_multiplier = 2.5  # live re-read on the NEXT pass
    pos2 = _buy(9203, profit=30.0)
    adapter.positions = [pos, pos2]
    om.apply_atr_trailing_stop(
        pos=pos2,
        price_current=2005.0,
        atr=2.0,
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        min_stop_gap=0.0,
        current_tick=None,
    )
    assert adapter.modifications[1][1] == pytest.approx(2005.0 - 2.0 * 2.5, abs=1e-9)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan")])
def test_trail_atr_multiplier_invalid_falls_back(bad):
    """A broken multiplier must fall back to the 1.15 constant, never widen to
    zero distance or NaN."""
    cfg = AlgoConfig()
    cfg.trail_atr_multiplier = bad
    adapter = SpyMT5Adapter()
    om = _manager(cfg, adapter=adapter)
    pos = _buy(9204, profit=30.0)
    adapter.positions = [pos]
    applied = om.apply_atr_trailing_stop(
        pos=pos,
        price_current=2005.0,
        atr=2.0,
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        min_stop_gap=0.0,
        current_tick=None,
    )
    assert applied
    assert adapter.modifications[0][1] == pytest.approx(2005.0 - 2.0 * 1.15, abs=1e-9)


# ---------------------------------------------------------------------------
# (c) AI-flip exit suspension (default OFF) + rate-limited WARNING
# ---------------------------------------------------------------------------


_FLIP_PROBS = _Probs([0.05, 0.10, 0.85])  # strong SELL bias against a BUY


def _ai_reversal_decision(ticket=42):
    return SimpleNamespace(
        action=ActionType.CLOSE_POSITION,
        reason_code=AI_REVERSAL_REASON,
        ticket=ticket,
        symbol="XAUUSD",
        reversal_action=None,
        volume=0.0,
    )


def test_ai_flip_exit_suspended_by_default_in_protection_chain(
    monkeypatch: pytest.MonkeyPatch,
):
    """Default config: a detected AI direction flip must NOT close the position
    and must NOT place the reversal order; the position falls through to the
    deterministic protection chain. Exactly ONE warning per ticket per interval."""
    probe = _WarnProbe()
    monkeypatch.setattr(om_module, "logger", probe)
    clock = [1000.0]
    monkeypatch.setattr(om_module.time, "monotonic", lambda: clock[0])

    adapter = SpyMT5Adapter()
    om = _manager(AlgoConfig(), adapter=adapter)
    pos = _buy(9301, profit=8.0)
    adapter.positions = [pos]
    tick = _tick(2000.30)

    handled = _run_chain(om, pos, adapter, tick, _FLIP_PROBS)
    assert handled is False  # falls through to giveback -> BE -> trailing
    assert adapter.calls == []  # zero broker interactions while suspended
    assert adapter.closed_tickets == []
    assert adapter.pending_orders == []
    assert om._forced_exit_mechanisms.get(9301) is None  # autopsy not mislabelled

    # Repeated pass within the interval: still exactly ONE warning.
    _run_chain(om, pos, adapter, tick, _FLIP_PROBS)
    assert len(probe.warnings) == 1
    assert "SUPPRESSED" in str(probe.warnings[0][0])

    # After the interval the ticket is warned once again (not silenced forever).
    clock[0] += 11.0
    _run_chain(om, pos, adapter, tick, _FLIP_PROBS)
    assert len(probe.warnings) == 2


def test_ai_flip_exit_flag_re_enables_chain_flip(monkeypatch: pytest.MonkeyPatch):
    """ai_flip_exit_enabled=True restores the exact close-then-reverse behavior
    (no permanent suspension, no suppressed warning)."""
    probe = _WarnProbe()
    monkeypatch.setattr(om_module, "logger", probe)

    adapter = SpyMT5Adapter()
    om = _manager(AlgoConfig(ai_flip_exit_enabled=True), adapter=adapter)
    pos = _buy(9302, profit=8.0)
    adapter.positions = [pos]

    handled = _run_chain(om, pos, adapter, _tick(2000.30), _FLIP_PROBS)
    assert handled is True
    assert "close_position" in adapter.calls
    assert 9302 in adapter.closed_tickets
    assert adapter.pending_orders, "reversal stop order must be dispatched"
    assert om._forced_exit_mechanisms.get(9302) == om_module.ExitMechanism.AI_REVERSAL_EXIT
    assert probe.warnings == []


def test_execute_ai_reversal_suspended_default_via_lifecycle_intercept(
    monkeypatch: pytest.MonkeyPatch,
):
    """The CLOSE_POSITION/AI_REVERSAL intercept must not reach the broker while
    suspended: no close, no broker I/O at all (INV-001 friendly), autopsy tag
    not set, ONE rate-limited warning per ticket."""
    probe = _WarnProbe()
    monkeypatch.setattr(om_module, "logger", probe)
    clock = [1000.0]
    monkeypatch.setattr(om_module.time, "monotonic", lambda: clock[0])

    adapter = SpyMT5Adapter()
    om = _manager(AlgoConfig(), adapter=adapter)
    pos = _buy(42, profit=8.0)
    adapter.positions = [pos]

    assert om.execute_lifecycle_action(_ai_reversal_decision(42)) is False
    assert adapter.calls == []  # suppressed BEFORE any broker query
    assert adapter.closed_tickets == []
    assert om._forced_exit_mechanisms.get(42) is None
    assert len(probe.warnings) == 1

    # Same ticket again inside the interval: no duplicate warning.
    assert om.execute_lifecycle_action(_ai_reversal_decision(42)) is False
    assert len(probe.warnings) == 1

    clock[0] += 11.0
    assert om.execute_lifecycle_action(_ai_reversal_decision(42)) is False
    assert len(probe.warnings) == 2


def test_execute_ai_reversal_reenabled_live_without_code_change(
    monkeypatch: pytest.MonkeyPatch,
):
    """The flag is read on EVERY call: flipping it on the live config object
    re-enables the reversal protocol (operator action, no code change)."""
    probe = _WarnProbe()
    monkeypatch.setattr(om_module, "logger", probe)

    adapter = SpyMT5Adapter()
    cfg = AlgoConfig()
    om = _manager(cfg, adapter=adapter)
    pos = _buy(43, profit=8.0)
    adapter.positions = [pos]

    assert om.execute_ai_reversal(decision=_ai_reversal_decision(43), volume=0.5) is False
    assert adapter.calls == []
    assert len(probe.warnings) == 1

    cfg.ai_flip_exit_enabled = True
    assert om.execute_ai_reversal(decision=_ai_reversal_decision(43), volume=0.5) is True
    assert 43 in adapter.closed_tickets
    assert len(probe.warnings) == 1  # the suppression warning, nothing new


def test_flip_suspension_does_not_touch_giveback_close_authority():
    """Separation contract: while AI flips are suspended the deterministic
    giveback protection STILL closes a winner that eroded past its floor."""
    adapter = SpyMT5Adapter()
    om = _manager(AlgoConfig(), adapter=adapter)
    pos = _buy(9401, profit=-10.0)
    adapter.positions = [pos]
    state = om.get_protection_state(9401)
    state.peak_win_usd = 100.0  # banked $100, now negative

    _score, active = om.enforce_profit_giveback_protection(pos, hold_score=90)
    assert active
    assert 9401 in adapter.closed_tickets
    assert state.close_requested


# ---------------------------------------------------------------------------
# Defaults preserved: constants and config fields
# ---------------------------------------------------------------------------


def test_exit_policy_config_defaults_are_preserved():
    """No threshold tuning: module constants untouched, config fields carry the
    exact HEAD values, AI-flip suspension is the default."""
    assert om_module.TIERED_GIVEBACK_ARM_R == pytest.approx(0.50)
    assert om_module.ATR_TRAILING_MULTIPLIER == pytest.approx(1.15)
    cfg = AlgoConfig()
    assert cfg.giveback_arm_r == pytest.approx(0.50)
    assert cfg.trail_atr_multiplier == pytest.approx(1.15)
    assert cfg.ai_flip_exit_enabled is False


def test_protection_engine_consumes_live_config_not_stale_snapshot():
    """The wiring flows through the _om_protection_symbols seam: the constants
    remain the fallback for foreign/duck-typed configs (no algo_config at all)."""
    adapter = SpyMT5Adapter()
    om = _manager(algo_config=None, adapter=adapter)
    pos = _buy(9501, profit=30.0)
    adapter.positions = [pos]
    applied = om.apply_atr_trailing_stop(
        pos=pos,
        price_current=2005.0,
        atr=2.0,
        symbol_info=adapter.get_symbol_info("XAUUSD"),
        min_stop_gap=0.0,
        current_tick=None,
    )
    assert applied
    assert adapter.modifications[0][1] == pytest.approx(2005.0 - 2.0 * 1.15, abs=1e-9)
    # The manager resolver is fallback-safe for duck-typed/absent configs.
    assert om._exit_policy_config_value("giveback_arm_r", 0.50) == 0.50
    assert om._exit_policy_config_value("trail_atr_multiplier", 1.15) == 1.15
    broken_cfg = SimpleNamespace(giveback_arm_r=float("nan"))
    om.algo_config = broken_cfg
    assert om._exit_policy_config_value("giveback_arm_r", 0.50) == 0.50
    om.algo_config = SimpleNamespace()  # no attribute at all -> constant
    assert om._exit_policy_config_value("giveback_arm_r", 0.50) == 0.50
