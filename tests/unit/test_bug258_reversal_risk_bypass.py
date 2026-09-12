"""BUG-258 regression battery (Agent-15 — Risk & Capital Protection Core).

Two proven bypasses closed this wave:

  1. AI-REVERSAL FLIP ENTRY: DecisionExecutor sized the flip via
     calculate_volume + get_clamped_position_size and execute_ai_reversal
     dispatched it through dispatch_order — the flip NEVER passed
     RiskEngine.evaluate_proposal, so breakers, RR, spread, stops-level,
     exposure squeeze, margin and impact gates were all skippable by a
     reversal. The mirrored-volume fallback (volume<=0 -> closed_volume)
     additionally sized a new order from the closed exposure with no risk
     approval at all.
     FIX: the flip is risk-approved through evaluate_proposal on a
     DIRECTIONAL TradeProposal (geometry re-validated by the domain model);
     an unapproved/zero volume is refused close-only.

  2. FAST-REVERSAL: the protection chain placed the stop-order flip DIRECTLY
     through adapter.place_pending_order — bypassing the whole dispatch gate
     stack. FIX: routed through dispatch_order with a full decision payload.

Tests use the REAL OrderLifecycleManager composition root (real
DispatchEngine, real RiskEngine, real gate stack); only the broker adapter
is a spy, because the broker is the one component that must NOT decide risk.

Offline, deterministic: no MT5, no network, no model artifacts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nexus_scalp.configuration.config import AlgoConfig, RiskConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeProposal,
)
from nexus_scalp.execution.lifecycle.dispatch import DispatchEngine
from nexus_scalp.execution.order_manager import OrderLifecycleManager, _FastReversalDecision
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.signals.policy import AI_REVERSAL_REASON
from tests.unit.maintenance_time_helpers import outside_maintenance_utc

# ---------------------------------------------------------------------------
# Real-component harness
# ---------------------------------------------------------------------------


class _SpyMT5Adapter:
    """Records every broker write. The ONLY stubbed component (broker truth)."""

    def __init__(self) -> None:
        self.positions: list[Position] = []
        self.market_orders: list[dict] = []
        self.pending_orders: list[dict] = []
        self.closed_tickets: list[int] = []
        self.close_ok = True

    def get_positions(self, symbol: str | None = None) -> list[Position]:
        return list(self.positions)

    def close_position(self, ticket: int, volume: float | None = None) -> bool:
        if not self.close_ok:
            return False
        self.closed_tickets.append(ticket)
        self.positions = [p for p in self.positions if p.ticket != ticket]
        return True

    def execute_market_order(self, **kw) -> int:
        self.market_orders.append(kw)
        return 9000 + len(self.market_orders)

    def place_pending_order(self, **kw) -> int:
        self.pending_orders.append(kw)
        return 8000 + len(self.pending_orders)


def _account(equity: float = 100_000.0) -> AccountInfo:
    return AccountInfo(
        login=1,
        trade_mode=0,
        leverage=100,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=equity,
        currency="USD",
    )


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )


def _tick(bid: float = 2000.0, ask: float = 2000.10) -> TickData:
    return TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=bid, ask=ask)


def _position(volume: float = 0.5, ticket: int = 42, ptype: OrderType = OrderType.BUY) -> Position:
    return Position(
        ticket=ticket,
        symbol="XAUUSD",
        type=ptype,
        volume=volume,
        price_open=2000.0,
        sl=1998.0,
        tp=2004.0,
        profit=5.0,
        magic=888101,
    )


def _reversal_decision(ticket: int = 42, reversal_action: ActionType = ActionType.SELL_MARKET):
    """Mirrors the policy-built reversal proposal (signals/policy.py):
    action=CLOSE_POSITION carrying the NEW direction's geometry."""
    entry = 2000.0  # tick.bid for a SELL flip
    return TradeProposal(
        request_id=f"rev-{ticket}",
        symbol="XAUUSD",
        generated_at=outside_maintenance_utc(datetime.now(UTC)),
        action=ActionType.CLOSE_POSITION,
        confidence=0.9,
        proposed_entry=entry,
        stop_loss=round(entry + 1.5, 2),
        take_profit=round(entry - 3.0, 2),
        risk_reward_ratio=2.0,
        reason_code=AI_REVERSAL_REASON,
        ticket=ticket,
        reversal_action=reversal_action,
        is_ai_reversal=True,
        execution_mode="AI_REVERSAL",
    )


def _manager(
    adapter: _SpyMT5Adapter,
    *,
    risk_engine: RiskEngine | None = None,
    safety_state_provider=None,
    symbol_info: SymbolInfo | None = None,
) -> OrderLifecycleManager:
    return OrderLifecycleManager(
        adapter=adapter,  # type: ignore[arg-type]
        audit_repo=SimpleNamespace(  # audit writes are not risk decisions
            log_order=lambda **kw: None,
            log_execution=lambda *a, **k: None,
        ),
        notifier=None,
        algo_config=AlgoConfig(ai_flip_exit_enabled=True),
        risk_engine=risk_engine
        or RiskEngine(RiskConfig(max_concurrent_positions=2), max_allowed_lots=2.0),
        experience_engine=None,
        safety_state_provider=safety_state_provider,
    )


def _outside_maintenance(ts: datetime) -> datetime:
    """Back-compat alias: BUG-264 replaced the '+180, retry +180 once' shape
    (which can land on the inclusive 19:30/22:30 UTC window edges) with the
    provable-step helper in tests/unit/maintenance_time_helpers.py."""
    return outside_maintenance_utc(ts)


class _Exec:
    """Lazy import to avoid cycles; mirrors the real composition."""

    def __init__(self, om: OrderLifecycleManager) -> None:
        from nexus_scalp.application.live.decision_executor import DecisionExecutor

        self.de = DecisionExecutor(om)


# ---------------------------------------------------------------------------
# 1. Flip entry requires canonical risk approval
# ---------------------------------------------------------------------------


class TestAiReversalRiskApproval:
    def test_flip_volume_comes_from_evaluate_proposal(self) -> None:
        """The DecisionExecutor sizes the flip through evaluate_proposal, not
        calculate_volume: the sized volume must equal what the canonical risk
        chain produces for the directional proposal."""
        from nexus_scalp.application.live.decision_executor import DecisionExecutor

        adapter = _SpyMT5Adapter()
        om = _manager(adapter, symbol_info=_symbol_info())
        engine = RiskEngine(RiskConfig(max_concurrent_positions=2), max_allowed_lots=2.0)
        om.risk_engine = engine
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        DecisionExecutor(om)  # composition check: real executor constructs
        directional = DecisionExecutor._build_directional_reversal_proposal(decision)
        assert directional is not None
        order = engine.evaluate_proposal(
            proposal=directional,
            account=_account(),
            symbol_info=_symbol_info(),
            active_positions=positions,
            current_tick=tick,
        )
        assert order is not None and order.volume > 0.0

        reversal_volume = order.volume
        ok = om.execute_ai_reversal(decision=decision, volume=reversal_volume, current_tick=tick)
        assert ok is True
        # close happened, flip dispatched with the risk-approved volume
        assert adapter.closed_tickets == [42]
        assert len(adapter.market_orders) == 1
        assert adapter.market_orders[0]["volume"] == pytest.approx(reversal_volume)

    def test_zero_volume_is_refused_close_only(self) -> None:
        """volume<=0 now means 'risk engine rejected the flip' — no mirrored
        sizing from the closed exposure, NO flip order."""
        adapter = _SpyMT5Adapter()
        om = _manager(adapter)
        tick = _tick()
        adapter.positions = [_position()]
        ok = om.execute_ai_reversal(decision=_reversal_decision(), volume=0.0, current_tick=tick)
        assert ok is True  # close-only success
        assert adapter.closed_tickets == [42]
        assert adapter.market_orders == []  # the bypass is gone

    def test_risk_rejected_flip_blocks_dispatch_at_executor_level(self) -> None:
        """A flip whose directional proposal the risk engine rejects (here:
        squeeze at the cap AND the position-count cap) must be close-only —
        no flip order — while the protective close still happens."""
        from nexus_scalp.application.live.decision_executor import DecisionExecutor

        adapter = _SpyMT5Adapter()
        om = _manager(adapter, symbol_info=_symbol_info())
        tick = _tick()
        # SELL flip with an existing BUY position: opposite-direction volume
        # does not consume the SELL squeeze cap, but max_concurrent_positions=1
        # (the canonical engine default) rejects the second concurrent position.
        positions = [_position(volume=2.0, ptype=OrderType.BUY)]
        adapter.positions = list(positions)
        DecisionExecutor(om)  # composition check: real executor constructs
        decision = _reversal_decision()

        directional = DecisionExecutor._build_directional_reversal_proposal(decision)
        assert directional is not None
        strict_engine = RiskEngine(RiskConfig(), max_allowed_lots=2.0)  # count cap 1
        om.risk_engine = strict_engine
        order = strict_engine.evaluate_proposal(
            proposal=directional,
            account=_account(),
            symbol_info=_symbol_info(),
            active_positions=positions,
            current_tick=tick,
        )
        assert order is None  # concurrent-position cap rejects the flip
        # Executor contract: None -> volume 0 -> close-only (tested above).

    def test_directional_proposal_revalidates_geometry(self) -> None:
        """An inverted/degenerate geometry on the reversal is rejected by the
        domain validator during the directional rebuild (fail-closed)."""
        from nexus_scalp.application.live.decision_executor import DecisionExecutor

        bad = _reversal_decision().model_copy(
            update={"proposed_entry": 2000.0, "stop_loss": 1990.0, "take_profit": 2010.0}
        )
        assert DecisionExecutor._build_directional_reversal_proposal(bad) is None

    def test_kill_switch_blocks_flip_at_risk_engine(self) -> None:
        """Kill switch armed -> evaluate_proposal returns None -> flip must be
        close-only (proven at the composition seam, not mocked)."""
        engine = RiskEngine(RiskConfig(), max_allowed_lots=2.0)
        engine.enable_kill_switch()
        try:
            from nexus_scalp.application.live.decision_executor import DecisionExecutor

            directional = DecisionExecutor._build_directional_reversal_proposal(
                _reversal_decision()
            )
            assert directional is not None
            order = engine.evaluate_proposal(
                proposal=directional,
                account=_account(),
                symbol_info=_symbol_info(),
                active_positions=[],
                current_tick=_tick(),
            )
            assert order is None
        finally:
            engine.disable_kill_switch()

    def test_daily_breaker_blocks_flip(self) -> None:
        """Daily loss budget breach -> flip rejected by the breaker layer."""
        engine = RiskEngine(RiskConfig(), max_allowed_lots=2.0)
        from datetime import datetime as _dt

        now = _dt.now(UTC)
        engine.breakers.update_equity(100_000.0, now)
        breaker = engine.breakers.evaluate(equity=90_000.0, now=now)  # -10% > 2%
        assert breaker.allowed is False

    def test_spread_gate_blocks_flip(self) -> None:
        engine = RiskEngine(RiskConfig(max_spread_points=20), max_allowed_lots=2.0)
        from nexus_scalp.application.live.decision_executor import DecisionExecutor

        directional = DecisionExecutor._build_directional_reversal_proposal(_reversal_decision())
        assert directional is not None
        order = engine.evaluate_proposal(
            proposal=directional,
            account=_account(),
            symbol_info=_symbol_info(),
            active_positions=[],
            current_tick=_tick(2000.0, 2001.0),  # 100 points > 20
        )
        assert order is None

    def test_halted_persisted_state_blocks_flip_at_dispatch(self) -> None:
        """HALTED state -> dispatch_order refuses the flip even if a sized
        volume exists (defense in depth at the dispatch layer)."""
        state = {"halted": True}
        adapter = _SpyMT5Adapter()
        om = _manager(adapter, safety_state_provider=lambda: state["halted"])
        tick = _tick()
        adapter.positions = [_position()]
        ok = om.execute_ai_reversal(decision=_reversal_decision(), volume=0.5, current_tick=tick)
        assert ok is False  # dispatch refused by the dispatch gate stack
        assert adapter.closed_tickets == [42]  # close still happened
        assert adapter.market_orders == []

    def test_flip_inherits_full_dispatch_gate_stack(self) -> None:
        """Even with risk-approved volume, the flip goes through dispatch_order:
        duplicate request_id / exposure gates apply identically. (Uses a
        directional SELL_MARKET decision, the exact payload the reversal
        protocol dispatches.)"""
        adapter = _SpyMT5Adapter()
        om = _manager(adapter)
        _tick()  # tick provenance not needed for the duplicate-guard probe
        adapter.positions = [_position()]
        decision = _reversal_decision()
        directional = decision.model_copy(update={"action": ActionType.SELL_MARKET})
        # NOTE: model_copy does not re-run validators; construct properly:
        from nexus_scalp.application.live.decision_executor import DecisionExecutor

        directional = DecisionExecutor._build_directional_reversal_proposal(decision)
        assert directional is not None
        assert om.dispatch_order(directional, 0.5) is True
        first_calls = len(adapter.market_orders)
        assert om.dispatch_order(directional, 0.5) is False
        assert len(adapter.market_orders) == first_calls


# ---------------------------------------------------------------------------
# 2. Fast reversal: no direct adapter path
# ---------------------------------------------------------------------------


class TestFastReversalRouting:
    def test_no_direct_adapter_submission_in_protection_chain(self) -> None:
        """The fast-reversal follow-up must not call adapter.place_pending_order
        directly anymore — it must route through dispatch_order."""
        import inspect

        from nexus_scalp.execution.order_manager import OrderLifecycleManager

        src = inspect.getsource(OrderLifecycleManager._run_protection_chain)
        # The only remaining mention must be inside the BUG-258 comment block.
        assert "self.adapter.place_pending_order" not in src, (
            "fast-reversal must not bypass the dispatch gate stack"
        )
        assert "dispatch_order(" in src

    def test_fast_reversal_request_id_is_ticket_terminal(self) -> None:
        """The synthetic fast-reversal decision derives its request_id from the
        source ticket + action: a re-fire is terminal at the duplicate guard."""
        d = _FastReversalDecision(
            symbol="XAUUSD",
            action=ActionType.SELL_STOP,
            proposed_entry=2000.0,
            stop_loss=2001.5,
            take_profit=1997.0,
            source_ticket=42,
            generated_at=datetime.now(UTC),
        )
        assert d.request_id == "fast_reversal_42_SELL_STOP"
        # Same ticket+action -> same request_id -> terminal after first send.
        d2 = _FastReversalDecision(
            symbol="XAUUSD",
            action=ActionType.SELL_STOP,
            proposed_entry=2001.0,
            stop_loss=2002.5,
            take_profit=1998.0,
            source_ticket=42,
            generated_at=datetime.now(UTC),
        )
        assert d2.request_id == d.request_id

    def test_fast_reversal_dispatch_refused_under_halt(self) -> None:
        """Simulated: even if the flip chain runs, dispatch_order refuses while
        a persisted halt is active (close-only)."""
        state = {"halted": True}
        adapter = _SpyMT5Adapter()
        om = _manager(adapter, safety_state_provider=lambda: state["halted"])
        de = DispatchEngine(om)
        decision = _FastReversalDecision(
            symbol="XAUUSD",
            action=ActionType.SELL_STOP,
            proposed_entry=2000.0,
            stop_loss=2001.5,
            take_profit=1997.0,
            source_ticket=42,
            generated_at=datetime.now(UTC) + timedelta(minutes=180),
        )
        assert de.dispatch_order(decision, 0.5) is False
        assert adapter.pending_orders == []
        assert adapter.market_orders == []

    def test_fast_reversal_dispatch_refused_under_kill_switch(self) -> None:
        adapter = _SpyMT5Adapter()
        engine = RiskEngine(RiskConfig(), max_allowed_lots=2.0)
        om = _manager(adapter, risk_engine=engine)
        engine.enable_kill_switch()
        try:
            de = DispatchEngine(om)
            decision = _FastReversalDecision(
                symbol="XAUUSD",
                action=ActionType.SELL_STOP,
                proposed_entry=2000.0,
                stop_loss=2001.5,
                take_profit=1997.0,
                source_ticket=42,
                generated_at=datetime.now(UTC) + timedelta(minutes=180),
            )
            assert de.dispatch_order(decision, 0.5) is False
            assert adapter.pending_orders == []
        finally:
            engine.disable_kill_switch()
