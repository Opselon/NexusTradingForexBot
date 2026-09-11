"""BUG-256 regression battery (Agent-15 — Risk & Capital Protection Core).

DEFECT (proven 2026-09-11): DispatchEngine's dispatch_order / execute_order
consult ``om._trading_blocked_by_safety_state`` as the fail-closed defense
against a persisted HALTED/KILL_SWITCH runtime_risk_state row. That method
lives on LiveEngine, but the composition root DispatchEngine actually reads
through is OrderLifecycleManager, which never carried the method — so the
``hasattr(om, ...)`` probe was always False on the real engine wiring and the
persisted-halt half of the dispatch gate was INERT. Only the RiskEngine
kill-switch flag remained (armed solely by the Telegram /halt intent, never
by the drawdown survival guard's trigger_runtime_halt(state="HALTED")).

FIX under test: OrderLifecycleManager accepts an optional
``safety_state_provider`` (the engine's own gate callable) and exposes
``_trading_blocked_by_safety_state`` delegating to it, fail-closed on
provider faults; LiveEngine registers itself at composition time.

Offline, deterministic: no MT5, no network, no model artifacts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.execution.lifecycle.dispatch import DispatchEngine
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.risk.risk_engine import RiskEngine


def _risk_engine() -> RiskEngine:
    return RiskEngine(RiskConfig(), max_allowed_lots=2.0)


def _decision(generated_at: datetime | None = None) -> MagicMock:
    d = MagicMock()
    d.request_id = f"BUG256-{id(d)}"
    d.symbol = "XAUUSD"
    d.action = ActionType.BUY_MARKET
    d.proposed_entry = 2000.5
    d.stop_loss = 1998.5
    d.take_profit = 2004.5
    d.confidence = 0.9
    # CI-DETERMINISM (a15 precedent, 6ee50b1d): pinned OUTSIDE the nightly
    # maintenance window (23:00-01:00 server = 20:00-22:00 UTC at the +180min
    # broker offset, +/-30m buffer). A wall-clock `now()` here made the first
    # dispatch hit the MAINTENANCE_WINDOW entry guard whenever the runner's
    # UTC clock sat in 19:30-22:30, failing the "proceeds when running" test
    # at that time of day on ANY machine (CI macOS 2026-09-11 16:52 UTC).
    # 2026-09-09 10:00 UTC -> server 13:00: mid-session, far from the window.
    d.generated_at = generated_at or datetime(2026, 9, 9, 10, 0, 0, tzinfo=UTC)
    return d


def _manager(state: dict) -> OrderLifecycleManager:
    """Real OrderLifecycleManager wired exactly like LiveEngine does."""

    def _provider() -> bool:
        return bool(state["halted"])

    return OrderLifecycleManager(
        adapter=MagicMock(),
        audit_repo=MagicMock(),
        notifier=None,
        risk_engine=_risk_engine(),
        experience_engine=None,
        safety_state_provider=_provider,
    )


class TestDispatchLayerSeesPersistedHalt:
    def test_olm_has_trading_blocked_method(self) -> None:
        state = {"halted": False}
        om = _manager(state)
        # The hasattr probe DispatchEngine performs must now succeed on the
        # real composition root (previously always False -> gate inert).
        assert hasattr(om, "_trading_blocked_by_safety_state")

    def test_persisted_halt_blocks_without_kill_switch_flag(self) -> None:
        state = {"halted": True}
        om = _manager(state)
        # Kill switch NOT armed — a drawdown trigger_runtime_halt(HALTED) only
        # sets the persisted state on the engine.
        assert om.risk_engine._kill_switch_active is False
        assert om._trading_blocked_by_safety_state() is True

    def test_running_state_does_not_block(self) -> None:
        state = {"halted": False}
        om = _manager(state)
        assert om._trading_blocked_by_safety_state() is False

    def test_provider_fault_is_fail_closed(self) -> None:
        def _boom() -> bool:
            raise RuntimeError("provider unavailable")

        om = OrderLifecycleManager(
            adapter=MagicMock(),
            audit_repo=MagicMock(),
            notifier=None,
            risk_engine=_risk_engine(),
            experience_engine=None,
            safety_state_provider=_boom,
        )
        assert om._trading_blocked_by_safety_state() is True

    def test_missing_provider_falls_back_to_kill_switch_chain(self) -> None:
        om = OrderLifecycleManager(
            adapter=MagicMock(),
            audit_repo=MagicMock(),
            notifier=None,
            risk_engine=_risk_engine(),
            experience_engine=None,
        )
        assert om._trading_blocked_by_safety_state() is False

    def test_live_engine_registers_itself_as_provider(self) -> None:
        """The composition root must wire the authority at construction."""
        import inspect

        from nexus_scalp.application.live_engine import LiveEngine

        src = inspect.getsource(LiveEngine.__init__)
        assert "safety_state_provider=self._trading_blocked_by_safety_state" in src, (
            "LiveEngine must register its persisted-safety-state gate on the "
            "OrderLifecycleManager — otherwise the dispatch-layer halt "
            "defense stays inert (BUG-256)"
        )

    def test_dispatch_order_refused_under_persisted_halt_via_real_manager(
        self,
    ) -> None:
        """End-to-end: persisted HALTED state blocks dispatch_order on the
        REAL OrderLifecycleManager -> DispatchEngine composition (no mocks
        on the gate itself)."""
        state = {"halted": True}
        om = _manager(state)
        om.mt5_adapter = MagicMock()
        om.mt5_adapter.execute_market_order.return_value = 777
        de = DispatchEngine(om)
        assert de.dispatch_order(_decision(), 0.05) is False
        om.mt5_adapter.execute_market_order.assert_not_called()

    def test_dispatch_order_proceeds_when_state_running(self) -> None:
        state = {"halted": False}
        om = _manager(state)
        om.mt5_adapter = MagicMock()
        om.mt5_adapter.execute_market_order.return_value = 777
        de = DispatchEngine(om)
        assert de.dispatch_order(_decision(), 0.05) is True
        om.mt5_adapter.execute_market_order.assert_called_once()
