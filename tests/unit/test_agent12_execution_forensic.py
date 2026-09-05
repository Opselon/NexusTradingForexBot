"""Agent-12 execution pipeline forensic regression (CHG-0058).

Pins the hard invariants the brief asked to be proven. All tests are
offline/deterministic (PaperMT5Adapter) and mirror the manual forensic
probe that already passed on this checkout. No broker calls.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.configuration.config import AlgoConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import TickData, TradeProposal
from nexus_scalp.execution.order_manager import (
    HARD_MAX_LOTS,
    MAX_TOTAL_EXPOSURE,
    OrderLifecycleManager,
)
from nexus_scalp.execution.position_states import PositionState
from nexus_scalp.execution.recovery_budget import RecoveryBudgetLedger


def _proposal(action: ActionType, rid: str, now: datetime) -> TradeProposal:
    is_buy = action in (
        ActionType.BUY,
        ActionType.BUY_MARKET,
        ActionType.BUY_LIMIT,
        ActionType.BUY_STOP,
    )
    return TradeProposal(
        request_id=rid,
        execution_id=f"EXEC-TEST-{rid}",
        symbol="XAUUSD",
        generated_at=now,
        action=action,
        confidence=0.9,
        proposed_entry=4400.30,
        stop_loss=4390.0 if is_buy else 4410.0,
        take_profit=4420.0 if is_buy else 4390.0,
        risk_reward_ratio=2.0,
    )


@pytest.fixture()
def paper_om():
    adapter = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
    adapter.connect()
    om = OrderLifecycleManager(adapter=adapter, audit_repo=None, risk_engine=None)
    return om, adapter


class TestAgent12Clamps:
    def test_hard_max_lots_unconditional(self, paper_om):
        om, _ = paper_om
        assert om._clamp_dispatch_volume(999.0, symbol="XAUUSD") <= HARD_MAX_LOTS
        assert om._clamp_dispatch_volume(-3.0) == 0.0
        assert om._clamp_dispatch_volume(0.0) == 0.0

    def test_max_total_exposure_blocks_second_dispatch(self, paper_om):
        om, adapter = paper_om
        now = datetime.now(UTC)
        tick = TickData(symbol="XAUUSD", timestamp=now, bid=4400.0, ask=4400.30, volume=10)
        assert om.dispatch_order(_proposal(ActionType.BUY, "RID-A", now), 0.10) is True
        adapter.get_positions()
        om.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=tick)
        assert om.dispatch_order(_proposal(ActionType.SELL, "RID-B", now), 0.10) is False
        assert MAX_TOTAL_EXPOSURE == 1

    def test_clamp_before_exposure(self, paper_om):
        om, _ = paper_om
        # volume 0 after clamp is rejected before any broker call
        now = datetime.now(UTC)
        assert om.dispatch_order(_proposal(ActionType.BUY, "RID-ZERO", now), 0.0) is False


class TestAgent12Idempotency:
    def test_duplicate_request_id_never_reaches_broker(self, paper_om):
        om, adapter = paper_om
        now = datetime.now(UTC)
        tick = TickData(symbol="XAUUSD", timestamp=now, bid=4400.0, ask=4400.30, volume=10)
        assert om.dispatch_order(_proposal(ActionType.BUY, "RID-IDEM", now), 0.10) is True
        adapter.close_position(ticket=adapter.get_positions()[0].ticket)
        om.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=tick)
        # same request_id again - must be blocked even though exposure is free
        assert om.dispatch_order(_proposal(ActionType.BUY, "RID-IDEM", now), 0.10) is False
        assert len(adapter.get_positions()) == 0


class TestAgent12SafeMode:
    def test_three_refusals_trip_safe_mode(self, paper_om):
        om, adapter = paper_om
        om._consecutive_failures = 0
        om.global_state = "NORMAL"
        now = datetime.now(UTC)
        orig = om.mt5_adapter.execute_market_order
        called: list[str] = []

        def _refuse(**kw):  # type: ignore[no-untyped-def]
            called.append("refuse")
            return 0

        om.mt5_adapter.execute_market_order = _refuse  # type: ignore[method-assign]
        try:
            om.dispatch_order(_proposal(ActionType.BUY, "RID-F1", now), 0.10)
            om.dispatch_order(_proposal(ActionType.SELL, "RID-F2", now), 0.10)
            om.dispatch_order(_proposal(ActionType.BUY, "RID-F3", now), 0.10)
            assert om.global_state == "SAFE_MODE"
            assert om.dispatch_order(_proposal(ActionType.SELL, "RID-F4", now), 0.10) is False
        finally:
            om.mt5_adapter.execute_market_order = orig  # type: ignore[method-assign]

    def test_success_resets_breaker(self, paper_om):
        om, _ = paper_om
        now = datetime.now(UTC)
        om.global_state = "NORMAL"
        om._consecutive_failures = 2
        assert om.dispatch_order(_proposal(ActionType.BUY, "RID-OK", now), 0.10) is True
        assert om._consecutive_failures == 0


class TestAgent12StateMachine:
    def test_emergency_bypass_immediate(self, paper_om):
        om, _ = paper_om
        t0 = datetime.now(UTC)
        s = om.transition_state_with_hysteresis(777001, PositionState.LOSS_HARD_EXIT, t0)
        assert s == PositionState.LOSS_HARD_EXIT

    def test_first_non_emergency_seeds_safe_state(self, paper_om):
        om, _ = paper_om
        t0 = datetime.now(UTC)
        s = om.transition_state_with_hysteresis(777002, PositionState.PROFIT_TRAILING, t0)
        assert s == PositionState.PROFIT_UNPROTECTED

    def test_hysteresis_requires_both_time_and_count(self, paper_om):
        om, _ = paper_om
        t0 = datetime.now(UTC)
        om.transition_state_with_hysteresis(777003, PositionState.PROFIT_TRAILING, t0)
        om.transition_state_with_hysteresis(777003, PositionState.PROFIT_PROTECTED, t0)
        s_fast = om.transition_state_with_hysteresis(
            777003, PositionState.PROFIT_PROTECTED, t0 + timedelta(seconds=0.5)
        )
        assert s_fast == PositionState.PROFIT_UNPROTECTED
        s_ok = om.transition_state_with_hysteresis(
            777003, PositionState.PROFIT_PROTECTED, t0 + timedelta(seconds=3.0)
        )
        # needs min_observation_count too (default 10) - only 3 sightings, still NOT confirmed
        assert s_ok == PositionState.PROFIT_UNPROTECTED


class TestAgent12ProtectionLedger:
    def test_monotonic_peak(self, paper_om):
        om, _ = paper_om
        ps = om.get_protection_state(777004)
        ps.update_peak(12.5)
        ps.update_peak(9.0)
        assert ps.peak_win_usd == 12.5

    def test_nan_inf_guarded(self, paper_om):
        om, _ = paper_om
        ps = om.get_protection_state(777005)
        ps.update_peak(5.0)
        ps.update_peak(float("nan"))
        ps.update_peak(float("inf"))
        assert ps.peak_win_usd == 5.0

    def test_retention_one_when_no_positive_peak(self, paper_om):
        om, _ = paper_om
        assert om.get_protection_state(777006).retention_ratio(-5.0) == 1.0


class TestAgent12RecoveryBudget:
    def test_allocation_and_idempotent_realloc(self):
        cfg = AlgoConfig()
        rb = RecoveryBudgetLedger()
        t0 = datetime.now(UTC)
        b = rb.allocate(
            888001,
            initial_risk_usd=100.0,
            current_pnl_usd=-10.0,
            confidence_factor=1.0,
            atr=1.5,
            trend_strength=0.0,
            now=t0,
            algo_config=cfg,
        )
        assert abs(b - 50.0) < 1e-9
        assert (
            rb.allocate(
                888001,
                initial_risk_usd=999.0,
                current_pnl_usd=-90.0,
                confidence_factor=0.0,
                atr=99.0,
                trend_strength=-1.0,
                now=t0,
                algo_config=cfg,
            )
            == b
        )

    def test_exhaustion_and_horizon_clamp(self):
        cfg = AlgoConfig()
        rb = RecoveryBudgetLedger()
        t0 = datetime.now(UTC)
        rb.allocate(
            888002,
            initial_risk_usd=100.0,
            current_pnl_usd=-10.0,
            confidence_factor=1.0,
            atr=1.5,
            trend_strength=0.0,
            now=t0,
            algo_config=cfg,
        )
        ex, _ = rb.evaluate_exhaustion(888002, -70.0, t0 + timedelta(seconds=10))
        assert ex is True
        h = rb.recovery_horizons[888002]
        assert cfg.min_recovery_horizon_sec <= h <= cfg.max_recovery_horizon_sec

    def test_per_ticket_isolation_and_drop(self):
        cfg = AlgoConfig()
        rb = RecoveryBudgetLedger()
        t0 = datetime.now(UTC)
        rb.allocate(
            888003,
            initial_risk_usd=100.0,
            current_pnl_usd=-1.0,
            confidence_factor=1.0,
            atr=1.5,
            trend_strength=0.0,
            now=t0,
            algo_config=cfg,
        )
        assert 888004 not in rb.recovery_budget_initial
        rb.drop_ticket(888003)
        assert not rb.is_allocated(888003)


class TestAgent12Teardown:
    def test_cleanup_bundle_covers_expected_surfaces(self):
        import inspect

        src = inspect.getsource(OrderLifecycleManager._cleanup_ticket_state)
        need = [
            "_partial_closed_tickets",
            "_mfe_tracker",
            "_mae_tracker",
            "_entry_timestamps",
            "_entry_prices",
            "_closed_tickets",
            "_exit_pending_final_reason",
            "_recovery_ledger.drop_ticket",
            "_state_machine.drop_ticket",
            "_tickets_cache.pop_ticket",
        ]
        missing = [n for n in need if n not in src]
        assert not missing, f"teardown missing: {missing}"


class TestAgent12SingleAuthority:
    def test_no_second_execution_path_outside_manager(self):
        import pathlib
        import re

        repo = pathlib.Path("src/nexus_scalp")
        pat = re.compile(
            r"(self\.mt5_adapter|self\.adapter)\.(execute_market_order|place_pending_order|close_position|modify_position|send_order|modify_order|cancel_pending_order)\("
        )
        outside: list[str] = []
        for f in repo.rglob("*.py"):
            if f.as_posix().endswith("execution/order_manager.py"):
                continue
            if "adapters/" in f.as_posix() or "ports/" in f.as_posix():
                continue
            t = f.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(t.splitlines(), 1):
                if pat.search(line):
                    outside.append(f"{f.as_posix()}:{i}:{line.strip()[:90]}")
        assert not outside, f"second-path violations: {outside}"

    def test_ai_reversal_routes_via_dispatch(self):
        import inspect

        assert "dispatch_order" in inspect.getsource(OrderLifecycleManager.execute_ai_reversal)
