"""AI reversal close-then-evaluate deadlock (fail-closed regression battery).

THE DEFECT (live money loss, 18:18:34-18:18:39 in the production log):
DecisionExecutor ran ``risk_engine.evaluate_proposal`` on the DIRECTIONAL
flip proposal BEFORE calling ``execute_ai_reversal``. With
``risk.max_concurrent_positions=1`` (the canonical default) the position
being closed still counted as active, so the count gate ALWAYS rejected the
flip ("Active position count limit reached"), ``reversal_volume`` stayed
0.0, and the flip was refused as "volume not risk-approved". The protective
close was then executed with volume 0 — i.e. the close ran but the engine
never flipped — and the losing position rode to a 60s HOLD_SCORE_DECAY hard
exit while gold moved against it. Four consecutive reversals died this way.

THE FIX (two-phase close-then-evaluate, fail-closed):
  1. ``execute_ai_reversal`` runs FIRST on a pure-close payload (closes every
     conflicting ticket, stamps exit_mechanism=AI_REVERSAL_EXIT, drops the
     ticket from _live_tickets_cache).
  2. Only after the close is confirmed does ``evaluate_proposal`` size the
     directional flip against the now-freed slot.
  3. The flip dispatches only when the risk gate passes; on a post-close
     rejection the position is ALREADY safely closed, a structured WARNING
     (reason=AI_REVERSAL_RISK_REJECTED_POST_CLOSE) is emitted, and no flip is
     stacked.

Tests drive the REAL OrderLifecycleManager composition root (real
DispatchEngine, real RiskEngine, real gate stack) through a thin engine shim
that mirrors LiveEngine's attribute surface; only the broker adapter is a
spy, because the broker is the one component that must not decide risk.

A test that passes with AND without the fix pins nothing: the battery is
mutation-verified against the pre-fix source (revert -> RED -> restore).

Offline, deterministic: no MT5, no network, no model artifacts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.application.live.decision_executor import DecisionExecutor
from nexus_scalp.configuration.config import AlgoConfig, RiskConfig
from nexus_scalp.domain.enums import ActionType, ExecutionMode, OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeProposal,
)
from nexus_scalp.execution.order_manager import OrderLifecycleManager
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

    def get_pending_orders_snapshot(self, symbol: str | None = None) -> list:
        return []


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
    return TickData(
        symbol="XAUUSD", timestamp=outside_maintenance_utc(datetime.now(UTC)), bid=bid, ask=ask
    )


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


class _EngineShim:
    """Mirrors the LiveEngine attribute surface DecisionExecutor reads:
    order_manager, risk_engine, _symbol_info, _peak_equity, config, audit,
    notifier, signal_policy. Composition stays REAL everywhere except the
    broker adapter (the spy above)."""

    def __init__(self, adapter: _SpyMT5Adapter, *, risk_config: RiskConfig | None = None) -> None:
        self.adapter = adapter
        self.config = SimpleNamespace(
            execution=SimpleNamespace(mode=ExecutionMode.PAPER),
            risk=risk_config or RiskConfig(max_concurrent_positions=1),
            algo=AlgoConfig(),
        )
        self._symbol_info: SymbolInfo | None = _symbol_info()
        self._peak_equity: float = 100_000.0
        self.risk_engine = RiskEngine(
            config=self.config.risk,
            max_margin_usage_pct=self.config.risk.max_margin_usage_pct,
            max_allowed_lots=self.config.risk.max_allowed_lots,
            magic_number=888101,
        )
        self.order_manager = OrderLifecycleManager(
            adapter=adapter,  # type: ignore[arg-type]
            audit_repo=SimpleNamespace(
                log_order=lambda **kw: None,
                log_execution=lambda *a, **k: None,
            ),
            notifier=None,
            algo_config=AlgoConfig(ai_flip_exit_enabled=True),
            risk_engine=self.risk_engine,
            experience_engine=None,
        )
        self.audit = SimpleNamespace(log_account_snapshot=lambda **kw: None)
        self.notifier = None
        self.signal_policy = SimpleNamespace(
            last_order_price=None,
            last_order_time=None,
            _last_active_direction=None,
            _last_active_direction_time=None,
            _last_executed_price=0.0,
        )

    def _evaluate_hedging_policy(self, **kw) -> None:
        pass

    def _update_survival_state(self, **kw) -> None:
        pass


def _run_reversal(adapter: _SpyMT5Adapter, *, risk_config: RiskConfig | None = None):
    """Runs execute_decision_stage for one AI-reversal decision with one
    active BUY position — the exact production deadlock shape."""
    engine = _EngineShim(adapter, risk_config=risk_config)
    tick = _tick()
    decision = _reversal_decision()
    positions = [_position()]
    adapter.positions = list(positions)

    DecisionExecutor(engine).execute_decision_stage(
        tick=tick,
        account=_account(),
        fv=SimpleNamespace(atr_m1=1.5),
        probs=SimpleNamespace(),
        regime_state=None,
        proposal=decision,
        policy_decision=decision,
        active_positions=positions,
        current_pos_count=len(positions),
    )
    return engine, tick, decision


# ---------------------------------------------------------------------------
# 1. The deadlock is broken: close happens, then the flip is risk-evaluated
#    against the FREED slot, then dispatched when the gate passes.
# ---------------------------------------------------------------------------


class TestCloseThenEvaluateDeadlock:
    def test_max_concurrent_positions_one_still_closes_and_flips(self) -> None:
        """THE regression: max_concurrent_positions=1 with one active
        position. Pre-fix the count gate rejected the flip BEFORE the close
        (the closed position still counted), so reversal_volume=0.0 and the
        losing position rode to a hard decay exit. Post-fix the close runs
        first and the flip is sized against the freed slot."""
        adapter = _SpyMT5Adapter()
        engine, _tick_used, decision = _run_reversal(adapter)

        # (a) the protective close happened under the reversal protocol.
        assert adapter.closed_tickets == [42]
        # the ticket dropped from the live-tickets cache (exposure freed in
        # the same tick, the whole point of the fix).
        assert 42 not in engine.order_manager._live_tickets_cache

        # (b)+(c) the flip was risk-evaluated AFTER the close and dispatched
        # with a risk-approved volume on the freed slot.
        assert len(adapter.market_orders) == 1
        flip = adapter.market_orders[0]
        assert flip["order_type"] == OrderType.SELL
        assert flip["volume"] > 0.0
        # The flip volume is the canonical risk-engine sizing for the
        # directional proposal against the freed slot (not the closed 0.5).
        directional = DecisionExecutor._build_directional_reversal_proposal(decision)
        assert directional is not None
        canonical = engine.risk_engine.evaluate_proposal(
            proposal=directional,
            account=_account(),
            symbol_info=_symbol_info(),
            active_positions=[],
            current_tick=_tick_used,
        )
        assert canonical is not None
        assert flip["volume"] == pytest.approx(canonical.volume)

    def test_evaluate_proposal_runs_after_close_not_before(self) -> None:
        """The risk evaluation sees a position list that no longer contains
        the ticket being reversed. The pre-fix ordering evaluated against the
        still-open position and ALWAYS hit the count gate."""
        adapter = _SpyMT5Adapter()
        calls: list[list[Position]] = []

        engine = _EngineShim(adapter)
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        real_evaluate = engine.risk_engine.evaluate_proposal

        def _spy_evaluate(**kw):
            calls.append(list(kw.get("active_positions") or []))
            return real_evaluate(**kw)

        engine.risk_engine.evaluate_proposal = _spy_evaluate  # type: ignore[method-assign]

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

        assert len(calls) == 1, "evaluate_proposal must run exactly once, after the close"
        # The close already removed the position from the broker spy; the
        # evaluation list must reflect the FREED slot (post-close state), not
        # the pre-close snapshot — that is exactly the ordering that lets the
        # flip past the max_concurrent_positions gate instead of deadlocking.
        assert calls[0] == [], (
            "evaluate_proposal must see the post-close broker state with the "
            "reversed ticket gone; a list still containing it would trip the "
            "count gate all over again"
        )
        assert adapter.closed_tickets == [42]
        assert len(adapter.market_orders) == 1

    def test_flip_order_never_dispatched_before_close(self) -> None:
        """No opposing order can land while the position is still open: the
        close confirmation gates the flip dispatch."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        tick = _tick()
        decision = _reversal_decision()
        adapter.positions = [_position()]
        state = {"positions_snapshot": list(adapter.positions)}

        # Close succeeds only after we record the dispatch attempt order.
        order_of_events: list[str] = []
        real_close = adapter.close_position
        real_market = adapter.execute_market_order

        def _record_close(ticket, volume=None):
            order_of_events.append(f"close:{ticket}")
            return real_close(ticket, volume)

        def _record_market(**kw):
            order_of_events.append(f"market:{kw.get('order_type')}")
            return real_market(**kw)

        adapter.close_position = _record_close  # type: ignore[method-assign]
        adapter.execute_market_order = _record_market  # type: ignore[method-assign]

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=state["positions_snapshot"],
            current_pos_count=1,
        )

        assert order_of_events[0].startswith("close:"), "the protective close must precede the flip"
        assert sum(1 for e in order_of_events if e.startswith("market:")) == 1


# ---------------------------------------------------------------------------
# 2. Fail-closed: a post-close risk rejection still closes, never stacks.
# ---------------------------------------------------------------------------


class TestFailClosedPostCloseRejection:
    def test_risk_rejected_post_close_is_close_only(self) -> None:
        """The flip gate fails AFTER the close (here: a kill switch armed
        between the close and the evaluation). The position is already
        safely closed; no opposing order is stacked on the exit."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        real_evaluate = engine.risk_engine.evaluate_proposal

        def _rejecting_evaluate(**kw):
            # Emulate a post-close gate refusal (kill switch / breaker /
            # margin refusal): the position is already gone from the broker.
            assert adapter.closed_tickets == [42], (
                "evaluate_proposal must only run after the close confirmed"
            )
            # The real gate is exercised for its side effects; its return is
            # discarded to emulate a post-close refusal at this layer.
            real_evaluate(**kw)

        engine.risk_engine.evaluate_proposal = _rejecting_evaluate  # type: ignore[method-assign]

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

        # (d) fail-closed: closed, no flip, no stacking.
        assert adapter.closed_tickets == [42]
        assert adapter.market_orders == []

    def test_post_close_rejection_logs_distinct_reason(
        self, decision_logs: list[tuple[str, tuple, dict]]
    ) -> None:
        """Observability contract: the post-close risk rejection emits
        AI_REVERSAL_RISK_REJECTED_POST_CLOSE (distinct from the legacy
        pre-close AI_REVERSAL_RISK_REJECTED) with ticket + request_id +
        freed_active_count."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        engine.risk_engine.evaluate_proposal = lambda **kw: None  # type: ignore[method-assign]

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

        warnings = [k for lvl, a, k in decision_logs if lvl == "warning"]
        messages = [str(a[0]) if a else "" for lvl, a, _k in decision_logs if lvl == "warning"]
        blob = " ".join(messages)
        assert "AI_REVERSAL_RISK_REJECTED_POST_CLOSE" in blob
        # The legacy pre-close reason is gone: the risk evaluation never runs
        # before the close anymore.
        assert not any("AI_REVERSAL_RISK_REJECTED " in m for m in messages)
        # Ticket + request_id travel with the warning.
        assert any(k.get("ticket") == 42 for k in warnings)
        assert any(k.get("request_id") == decision.request_id for k in warnings)
        # The freed active count is recorded (the observability requirement).
        assert any(k.get("freed_active_count") is not None for k in warnings)

    def test_close_failure_refuses_flip_never_stacks(self) -> None:
        """If the broker refuses the protective close there is NO flip —
        opposing orders are never stacked on an unclosed position."""
        adapter = _SpyMT5Adapter()
        adapter.close_ok = False
        engine = _EngineShim(adapter)
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        evaluated: list[Any] = []

        def _must_not_run(**kw):
            evaluated.append(kw)
            raise AssertionError("flip must not be risk-evaluated when the close failed")

        engine.risk_engine.evaluate_proposal = _must_not_run  # type: ignore[method-assign]

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

        assert adapter.market_orders == []
        assert evaluated == []

    def test_flip_suppressed_by_operator_flag_does_not_close(self) -> None:
        """algo.ai_flip_exit_enabled=False (default, out of scope for this
        fix) means execute_ai_reversal returns False and BOTH the close and
        the flip are suppressed — the existing designed behavior. Nothing
        changes here: the flag stays default False in production config."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        engine.order_manager.algo_config = AlgoConfig(ai_flip_exit_enabled=False)
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        evaluated: list[Any] = []
        real_evaluate = engine.risk_engine.evaluate_proposal

        def _spy(**kw):
            evaluated.append(kw)
            return real_evaluate(**kw)

        engine.risk_engine.evaluate_proposal = _spy  # type: ignore[method-assign]

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

        assert adapter.closed_tickets == []  # the close is suppressed too
        assert adapter.market_orders == []
        assert evaluated == [], "no risk evaluation when the flip path is operator-disabled"


# ---------------------------------------------------------------------------
# 3. Preserved guards (regression net for the surrounding contract)
# ---------------------------------------------------------------------------


class TestPreservedGuards:
    def test_geometry_unavailable_is_close_only(self) -> None:
        """AI_REVERSAL_GEOMETRY_UNAVAILABLE: a reversal whose directional
        rebuild fails (inverted geometry) still closes, no flip."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        tick = _tick()
        decision = _reversal_decision().model_copy(
            update={"proposed_entry": 2000.0, "stop_loss": 1990.0, "take_profit": 2010.0}
        )
        positions = [_position()]
        adapter.positions = list(positions)

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

        assert DecisionExecutor._build_directional_reversal_proposal(decision) is None
        assert adapter.closed_tickets == [42]
        assert adapter.market_orders == []

    def test_missing_symbol_info_is_close_only(self) -> None:
        """AI_REVERSAL_NO_SYMBOL_INFO: without symbol_info the close still
        happens and the flip is refused (no sizing source)."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        engine._symbol_info = None
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

        assert adapter.closed_tickets == [42]
        assert adapter.market_orders == []

    def test_no_reversal_action_is_close_only(self) -> None:
        """A CLOSE_POSITION decision with reason AI_REVERSAL_SIGNAL but no
        directional follow-up closes and never tries to flip."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        tick = _tick()
        decision = _reversal_decision().model_copy(update={"reversal_action": None})
        positions = [_position()]
        adapter.positions = list(positions)

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

        assert adapter.closed_tickets == [42]
        assert adapter.market_orders == []

    def test_shadow_boundary_suppresses_reversal(self) -> None:
        """SHADOW mode is observation-only: neither the close nor the flip
        reaches the broker (BUG-212 boundary preserved)."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        engine.config.execution.mode = ExecutionMode.SHADOW
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

        assert adapter.closed_tickets == []
        assert adapter.market_orders == []

    def test_dedup_gate_suppresses_replayed_reversal(self) -> None:
        """A DEDUP_GATE decision_stage is the BUG-169 duplicate re-surface:
        it must never execute, even for a reversal payload."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        tick = _tick()
        decision = _reversal_decision().model_copy(update={"decision_stage": "DEDUP_GATE"})
        positions = [_position()]
        adapter.positions = list(positions)

        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

        assert adapter.closed_tickets == []
        assert adapter.market_orders == []


# ---------------------------------------------------------------------------
# 4. Observability: the decision-boundary log lines exist
# (module logger monkeypatched — never caplog; structlog host routing, per
# the repo convention in tests/unit/test_bug274_wrapper_state_leak.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def decision_logs(monkeypatch) -> list[tuple[str, tuple, dict]]:
    from nexus_scalp.application.live import decision_executor as de_mod

    got: list[tuple[str, tuple, dict]] = []

    def _rec(level: str):
        def _fn(*args, **kwargs):
            got.append((level, args, kwargs))

        return _fn

    monkeypatch.setattr(
        de_mod,
        "logger",
        SimpleNamespace(
            critical=_rec("critical"),
            error=_rec("error"),
            warning=_rec("warning"),
            info=_rec("info"),
            debug=_rec("debug"),
        ),
    )
    return got


def _msgs(logs: list[tuple[str, tuple, dict]], level: str = "info") -> list[str]:
    return [str(a[0]) if a else "" for lvl, a, _k in logs if lvl == level]


class TestDecisionBoundaryObservability:
    def _run(self, engine: _EngineShim, decision, positions, tick) -> None:
        DecisionExecutor(engine).execute_decision_stage(
            tick=tick,
            account=_account(),
            fv=SimpleNamespace(atr_m1=1.5),
            probs=SimpleNamespace(),
            regime_state=None,
            proposal=decision,
            policy_decision=decision,
            active_positions=positions,
            current_pos_count=len(positions),
        )

    def test_close_phase_line_emitted_before_risk_evaluation(
        self, decision_logs: list[tuple[str, tuple, dict]]
    ) -> None:
        """(a) the close-phase result is logged at the decision boundary,
        before the risk evaluation runs."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        evaluated_at: list[int] = []
        real_evaluate = engine.risk_engine.evaluate_proposal

        def _spy(**kw):
            evaluated_at.append(len(decision_logs))
            return real_evaluate(**kw)

        engine.risk_engine.evaluate_proposal = _spy  # type: ignore[method-assign]

        self._run(engine, decision, positions, tick)

        info = _msgs(decision_logs)
        assert any("[AI_REVERSAL] close-phase done" in m for m in info), (
            "the close-phase result must be logged at the decision boundary"
        )
        close_idx = next(i for i, m in enumerate(info) if "close-phase done" in m)
        # The close-phase line precedes the risk evaluation.
        assert evaluated_at and evaluated_at[0] > 0
        assert close_idx < evaluated_at[0], "close-phase logging must precede the risk evaluation"
        # ...and it precedes the final state line.
        final_idx = next(i for i, m in enumerate(info) if "flip_dispatched=" in m)
        assert close_idx < final_idx

    def test_final_state_line_carries_the_contract_keys(
        self, decision_logs: list[tuple[str, tuple, dict]]
    ) -> None:
        """(c) the final state line records the full decision outcome."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        self._run(engine, decision, positions, tick)

        info_records = [
            k
            for lvl, a, k in decision_logs
            if lvl == "info" and a and "protocol-complete" in str(a[0])
        ]
        assert info_records, "the final state line must be emitted"
        keys = info_records[-1]
        assert keys["ticket"] == 42
        assert keys["close_ok"] is True
        assert keys["flip_dispatched"] is True
        assert keys["volume"] > 0.0
        assert keys["duration_ms"] >= 0.0

    def test_final_state_line_on_fail_closed_path(
        self, decision_logs: list[tuple[str, tuple, dict]]
    ) -> None:
        """The fail-closed path also emits the full state line so the
        deadlock class stays visible if it recurs."""
        adapter = _SpyMT5Adapter()
        engine = _EngineShim(adapter)
        engine.risk_engine.evaluate_proposal = lambda **kw: None  # type: ignore[method-assign]
        tick = _tick()
        decision = _reversal_decision()
        positions = [_position()]
        adapter.positions = list(positions)

        self._run(engine, decision, positions, tick)

        info_records = [
            k
            for lvl, a, k in decision_logs
            if lvl == "info" and a and "protocol-complete" in str(a[0])
        ]
        assert info_records
        keys = info_records[-1]
        assert keys["close_ok"] is True
        assert keys["flip_dispatched"] is False
        assert keys["volume"] == 0.0

    def test_no_per_tick_io_in_the_reversal_branch(self) -> None:
        """INV-001: the decision-boundary logging must stay in-memory (no
        per-tick file/network I/O). The only new calls are logger.info /
        logger.warning on the module logger."""
        import inspect

        from nexus_scalp.application.live import decision_executor as de_mod

        src = inspect.getsource(de_mod.DecisionExecutor.execute_decision_stage)
        # The reversal block is delimited by the DEADLOCK FIX marker.
        start = src.index("DEADLOCK FIX")
        end = src.index("# FOR NEW ENTRY SIGNALS")
        reversal_src = src[start:end]
        forbidden = ("open(", "sqlite3", "requests.", "urlopen", "json.dump", ".write(")
        assert not any(tok in reversal_src for tok in forbidden), (
            "the reversal branch must add no per-tick I/O"
        )
