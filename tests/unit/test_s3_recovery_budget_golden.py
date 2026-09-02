"""Agent-5 S3 characterization baseline (brief #7/#12): recovery-budget
lifecycle semantics captured BEFORE the RecoveryBudgetLedger extraction.

Fixtures mirror test_adaptive_position_management.py (MockMT5Adapter + real
AccountingCore-free AuditRepository) and validate actual state transitions
and mutation rules: idempotent allocation, budget math, exhaustion (USD +
time horizon), recompute monotonicity, cross-ticket isolation, cleanup.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, TickData
from nexus_scalp.execution.order_manager import OrderLifecycleManager

try:
    from tests.unit.test_adaptive_position_management import MockMT5Adapter
except Exception:  # pragma: no cover - local fallback
    from unit.test_adaptive_position_management import MockMT5Adapter  # type: ignore


@pytest.fixture()
def om(tmp_path):
    adapter = MockMT5Adapter()
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'audit.db'}")
    manager = OrderLifecycleManager(adapter=adapter, audit_repo=repo)
    yield manager
    repo.close()


def _pos(ticket: int, profit: float, sl: float = 1995.00) -> Position:
    return Position(
        ticket=ticket,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=sl,
        tp=2020.00,
        profit=profit,
        magic=888101,
    )


def _tick(now: datetime) -> TickData:
    return TickData(symbol="XAUUSD", timestamp=now, bid=1999.90, ask=1999.92, volume=1.0)


def _run_pass(om, positions, now, probs=None, bid=1999.90, ask=1999.92):
    om.adapter.positions = positions
    probs = probs if probs is not None else torch.tensor([[0.01, 0.98, 0.01]])
    om.manage_active_positions(
        "XAUUSD",
        TickData(symbol="XAUUSD", timestamp=now, bid=bid, ask=ask, volume=1.0),
        probs=probs,
    )


class TestS3RecoveryBudgetCharacterization:
    def test_allocation_once_and_budget_math(self, om):
        """I1 idempotent allocation; budget = min(0.5*R, remaining risk)."""
        now = datetime.now(UTC)
        # R = $5 price risk * 100 * 1.0 = $500 -> budget = min(250, 490) = 250
        _run_pass(om, [_pos(999, -10.00)], now)
        assert om._recovery_budget_initial[999] == 250.00
        assert om._recovery_budget_remaining[999] == 250.00
        assert om._recovery_budget_consumed[999] == 0.0
        assert om._recovery_initial_loss[999] == 10.00

        # second pass with a SMALLER loss must NOT re-allocate
        _run_pass(om, [_pos(999, -8.00)], now + timedelta(seconds=1))
        assert om._recovery_budget_initial[999] == 250.00

    def test_consumption_recomputed_from_absolutes(self, om):
        """I3/I4: consumed = max(0, current_loss - initial_loss); remaining >= 0."""
        now = datetime.now(UTC)
        _run_pass(om, [_pos(999, -10.00)], now)  # initial_loss = 10

        om._evaluate_recovery_budget_and_horizon(999, -60.00, now)
        assert om._recovery_budget_consumed[999] == 50.00
        assert om._recovery_budget_remaining[999] == 200.00

        # loss shrinks back: consumed recomputes to 0 (never drifts)
        om._evaluate_recovery_budget_and_horizon(999, -5.00, now)
        assert om._recovery_budget_consumed[999] == 0.0
        assert om._recovery_budget_remaining[999] == 250.00

        # profit (positive pnl): current_loss = 0
        om._evaluate_recovery_budget_and_horizon(999, 12.00, now)
        assert om._recovery_budget_consumed[999] == 0.0
        assert om._recovery_budget_remaining[999] == 250.00

    def test_time_horizon_exhaustion(self, om):
        """elapsed > horizon => exhausted even with USD budget intact."""
        now = datetime.now(UTC)
        _run_pass(om, [_pos(999, -10.00)], now)
        horizon = om._recovery_horizons[999]
        assert 30.0 <= horizon <= 600.0  # bounded
        # fabricate elapsed beyond horizon
        om._recovery_entry_times[999] = now - timedelta(seconds=horizon + 5)
        exhausted, reason = om._evaluate_recovery_budget_and_horizon(
            999, -10.00, now + timedelta(seconds=horizon + 5)
        )
        assert exhausted is True
        assert "RECOVERY_TIME_EXHAUSTED" in reason

    def test_not_in_recovery_returns_false(self, om):
        """Uninitialized ticket: evaluate returns (False, '') — no crash."""
        now = datetime.now(UTC)
        exhausted, reason = om._evaluate_recovery_budget_and_horizon(4242, -50.0, now)
        assert exhausted is False and reason == ""

    def test_cross_ticket_isolation(self, om):
        """I5: one ticket's consumption cannot touch another ticket's budget."""
        now = datetime.now(UTC)
        _run_pass(om, [_pos(301, -10.00), _pos(302, -10.00)], now)
        om._evaluate_recovery_budget_and_horizon(301, -260.00, now)
        assert om._recovery_budget_remaining[301] == 0.0
        assert om._recovery_budget_remaining[302] == 250.00

    def test_cleanup_clears_all_six(self, om):
        """I6: _cleanup_ticket_state removes every recovery dict entry."""
        now = datetime.now(UTC)
        _run_pass(om, [_pos(999, -10.00)], now)
        for d in (
            om._recovery_budget_initial,
            om._recovery_budget_remaining,
            om._recovery_budget_consumed,
            om._recovery_initial_loss,
            om._recovery_entry_times,
            om._recovery_horizons,
        ):
            assert 999 in d
        om._cleanup_ticket_state(999)
        for d in (
            om._recovery_budget_initial,
            om._recovery_budget_remaining,
            om._recovery_budget_consumed,
            om._recovery_initial_loss,
            om._recovery_entry_times,
            om._recovery_horizons,
        ):
            assert 999 not in d

    def test_horizon_scaling_bounded(self, om):
        """Horizon derives from ATR/confidence/trend and is clamped 30..600."""
        now = datetime.now(UTC)
        om._initial_risks[555] = 500.0
        om._initialize_recovery_mode(555, -10.0, 1.0, 0.5, 0.0, now)
        assert 30.0 <= om._recovery_horizons[555] <= 600.0
