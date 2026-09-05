"""Agent-11 execution/risk deep-forensic regression suite (BUG-239..242).

Covers the four confirmed defects fixed by the Agent-11 forensic pass:

- BUG-239: RiskEngine.evaluate_proposal crashed with UnboundLocalError
  (slippage_usd) on the micro-account + insufficient-margin path AND the
  trailing micro-account exception could resurrect a volume the free-margin
  guard had already zeroed.
- BUG-240: the MAX_TOTAL_EXPOSURE gate counted only symbol-scoped tickets
  although the contract (and the signals/policy.py gate) is engine-wide.
- BUG-241: the 3-rejection SAFE_MODE breaker existed only on the hedge path
  (execute_order); the primary dispatch path neither honored nor fed it.
- BUG-242: the web operator endpoints /api/positions/close and
  /api/positions/modify called the broker adapter directly, bypassing
  OrderLifecycleManager (INV-004).

All tests are deterministic fixtures (paper adapter / mock adapters / tmp
paths); no live broker, no network, xdist-safe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    SymbolInfo,
    TickData,
    TradeProposal,
)
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.risk.risk_engine import RiskEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _gold_symbol() -> SymbolInfo:
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


def _gold_tick(now: datetime) -> TickData:
    return TickData(symbol="XAUUSD", timestamp=now, bid=1999.9, ask=2000.1)


def _account(equity: float, margin_free: float | None = None) -> AccountInfo:
    return AccountInfo(
        login=1,
        trade_mode=0,
        leverage=100,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=margin_free if margin_free is not None else equity * 2.0,
        currency="USD",
    )


def _proposal(
    now: datetime,
    request_id: str = "agent11-req",
    confidence: float = 0.9,
) -> TradeProposal:
    return TradeProposal(
        request_id=request_id,
        symbol="XAUUSD",
        generated_at=now,
        action=ActionType.BUY_MARKET,
        confidence=confidence,
        proposed_entry=2000.0,
        stop_loss=1998.0,
        take_profit=2006.0,
        risk_reward_ratio=2.5,
        execution_mode="STANDARD",
    )


def _make_manager(adapter=None):
    paper = (
        adapter
        if adapter is not None
        else PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
    )
    if adapter is None:
        paper.connect()
    audit = AuditRepository(db_url="sqlite:///:memory:")
    manager = OrderLifecycleManager(
        adapter=paper,
        audit_repo=audit,
        notifier=None,
        be_trigger_usd=5.0,
        be_lock_usd=2.0,
        trailing_distance_usd=3.0,
        min_modify_step_usd=0.5,
        enable_partial_tp=True,
        partial_tp_ratio=0.5,
        max_holding_seconds=3600,
        risk_engine=RiskEngine(RiskConfig(risk_per_trade_pct=0.5)),
    )
    return paper, audit, manager


# ---------------------------------------------------------------------------
# BUG-239: micro-account + insufficient margin must reject, never crash
# ---------------------------------------------------------------------------


class TestBug239MicroAccountMarginHole:
    def test_no_unboundlocalerror_on_micro_no_margin(self) -> None:
        """FAIL-BEFORE: UnboundLocalError (slippage_usd) on this exact path."""
        engine = RiskEngine(RiskConfig(risk_per_trade_pct=0.5))
        now = datetime.now(UTC)
        proposal = _proposal(now)
        account = _account(equity=40.0, margin_free=0.001)
        order = engine.evaluate_proposal(
            proposal, account, _gold_symbol(), [], _gold_tick(now), atr=1.5
        )
        assert order is None, "insufficient-margin micro proposal must be rejected"

    def test_margin_zeroed_volume_never_resurrected(self) -> None:
        """The micro exception must not resurrect a margin-refused volume."""
        engine = RiskEngine(RiskConfig(risk_per_trade_pct=0.5))
        now = datetime.now(UTC)
        account = _account(equity=40.0, margin_free=0.001)
        _volume, reason = engine.calculate_dynamic_volume(
            entry=2000.0,
            sl=1998.0,
            account=account,
            symbol_info=_gold_symbol(),
            risk_pct=0.5,
        )
        # dynamic sizing itself reports the micro exception...
        assert reason in (
            "MICRO_ACCOUNT_MIN_LOT_EXCEPTION",
            "INSUFFICIENT_EQUITY_FOR_MIN_LOT",
        )
        # ...but evaluate_proposal must not return an order the margin guard refused
        order = engine.evaluate_proposal(
            _proposal(now), account, _gold_symbol(), [], _gold_tick(now), atr=1.5
        )
        assert order is None

    def test_micro_exception_still_works_with_margin(self) -> None:
        """Legitimate micro rescue (impact-reduced, margin affordable) survives."""
        engine = RiskEngine(RiskConfig(risk_per_trade_pct=0.5))
        now = datetime.now(UTC)
        account = _account(equity=40.0, margin_free=200000.0)
        order = engine.evaluate_proposal(
            _proposal(now, confidence=0.5),
            account,
            _gold_symbol(),
            [],
            _gold_tick(now),
            atr=1.5,
        )
        assert order is not None
        assert order.volume == pytest.approx(0.01)

    def test_adult_account_insufficient_margin_rejects(self) -> None:
        """equity>=50 + tiny free margin: clean None, no crash, no order."""
        engine = RiskEngine(RiskConfig(risk_per_trade_pct=0.5))
        now = datetime.now(UTC)
        account = _account(equity=100000.0, margin_free=0.001)
        order = engine.evaluate_proposal(
            _proposal(now), account, _gold_symbol(), [], _gold_tick(now), atr=1.5
        )
        assert order is None


# ---------------------------------------------------------------------------
# BUG-240: exposure gate is engine-wide
# ---------------------------------------------------------------------------


class TestBug240EngineWideExposure:
    def test_cross_symbol_position_blocks_dispatch(self) -> None:
        """FAIL-BEFORE: EURUSD position open -> XAUUSD dispatch succeeded."""
        paper, _audit, manager = _make_manager()
        paper._open_simulated_position(
            symbol="EURUSD",
            order_type=OrderType.BUY,
            volume=0.1,
            price=1.0850,
            stop_loss=1.0840,
            take_profit=1.0870,
            magic=888101,
        )
        # Sync the engine view from broker truth (as manage_active_positions does)
        with manager._live_tickets_lock:
            cache = manager._tickets_cache.rebuild(
                positions=paper.get_positions(),
                pending_lookup=None,
                pending_field=manager._pending_field,
                symbol="XAUUSD",
            )
            manager._tickets_cache.swap(cache)

        now = datetime.now(UTC)
        ok = manager.dispatch_order(_proposal(now, request_id="xau-block"), volume=0.5)
        assert ok is False, "engine-wide cap must block while ANY symbol is exposed"
        symbols = {p.symbol for p in paper.get_positions()}
        assert symbols == {"EURUSD"}, "no new position may appear"

    def test_same_symbol_position_blocks_dispatch(self) -> None:
        paper, _audit, manager = _make_manager()
        paper._open_simulated_position(
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=0.5,
            price=2000.0,
            stop_loss=1995.0,
            take_profit=2020.0,
            magic=888101,
        )
        with manager._live_tickets_lock:
            cache = manager._tickets_cache.rebuild(
                positions=paper.get_positions(),
                pending_lookup=None,
                pending_field=manager._pending_field,
                symbol="XAUUSD",
            )
            manager._tickets_cache.swap(cache)
        now = datetime.now(UTC)
        ok = manager.dispatch_order(_proposal(now, request_id="same-block"), volume=0.5)
        assert ok is False

    def test_exposure_free_when_flat(self) -> None:
        _paper, _audit, manager = _make_manager()
        assert manager._is_exposure_available(symbol="XAUUSD") is True


# ---------------------------------------------------------------------------
# BUG-241: SAFE_MODE honored and fed on the primary dispatch path
# ---------------------------------------------------------------------------


class _RefusingAdapter:
    """Adapter that refuses every dispatch (ticket 0 / False)."""

    def get_account_info(self):
        return _account(equity=100000.0)

    def get_symbol_info(self, symbol):
        return _gold_symbol()

    def execute_market_order(self, **kwargs):
        return 0

    def place_pending_order(self, **kwargs):
        return 0

    def get_positions(self, symbol=None):
        return []


class TestBug241SafeModePrimaryPath:
    def test_dispatch_blocked_when_safe_mode_active(self) -> None:
        _paper, _audit, manager = _make_manager()
        manager.global_state = "SAFE_MODE"
        now = datetime.now(UTC)
        ok = manager.dispatch_order(_proposal(now, request_id="sm-1"), volume=0.5)
        assert ok is False, "SAFE_MODE must block the PRIMARY dispatch path"

    def test_three_primary_refusals_open_the_breaker(self) -> None:
        _paper, _audit, manager = _make_manager(_RefusingAdapter())
        manager.global_state = "NORMAL"
        now = datetime.now(UTC)
        for i in range(3):
            ok = manager.dispatch_order(_proposal(now, request_id=f"brk-{i}"), volume=0.5)
            assert ok is False
        assert manager.global_state == "SAFE_MODE", (
            "3 consecutive primary-path refusals must trip the breaker"
        )

    def test_success_resets_failure_counter(self) -> None:
        paper, _audit, manager = _make_manager()
        manager.global_state = "NORMAL"
        manager._consecutive_failures = 2
        now = datetime.now(UTC)
        ok = manager.dispatch_order(_proposal(now, request_id="reset-1"), volume=0.5)
        assert ok is True
        assert manager._consecutive_failures == 0
        assert manager.global_state == "NORMAL"


# ---------------------------------------------------------------------------
# BUG-242: operator mutations route through the manager (INV-004)
# ---------------------------------------------------------------------------


class TestBug242ManualActionAuthority:
    def test_manual_close_tags_mechanism_and_releases_cache(self) -> None:
        paper, _audit, manager = _make_manager()
        ticket = paper._open_simulated_position(
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=0.5,
            price=2000.0,
            stop_loss=1995.0,
            take_profit=2020.0,
            magic=888101,
        )
        ok = manager.close_position_manual(ticket=ticket)
        assert ok is True
        assert paper.get_positions() == [], "position must be closed"
        from nexus_scalp.execution.order_manager import ExitMechanism

        assert manager._forced_exit_mechanisms.get(ticket) == ExitMechanism.MANUAL_CLOSE, (
            "operator close must be attributed as MANUAL_CLOSE evidence"
        )

    def test_manual_close_failure_rolls_back_mechanism(self) -> None:
        paper, _audit, manager = _make_manager()
        ok = manager.close_position_manual(ticket=424242)  # unknown ticket
        assert ok is False
        assert 424242 not in manager._forced_exit_mechanisms

    def test_manual_modify_routes_through_adapter(self) -> None:
        paper, _audit, manager = _make_manager()
        ticket = paper._open_simulated_position(
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=0.5,
            price=2000.0,
            stop_loss=1995.0,
            take_profit=2020.0,
            magic=888101,
        )
        ok = manager.modify_position_manual(ticket=ticket, stop_loss=1998.0, take_profit=2020.0)
        assert ok is True
        pos = next(p for p in paper.get_positions() if p.ticket == ticket)
        assert pos.sl == pytest.approx(1998.0)

    def test_web_layer_never_calls_adapter_directly(self) -> None:
        """Black-box: /api/positions/* reach the manager, never engine.adapter."""
        from fastapi.testclient import TestClient

        from nexus_scalp.web.server import create_app

        app = create_app()
        om = MagicMock()
        om.modify_position_manual.return_value = True
        om.close_position_manual.return_value = True
        engine = MagicMock()
        engine.order_manager = om
        app.state.engine = engine
        client = TestClient(app)

        r1 = client.post(
            "/api/positions/modify",
            json={"ticket": 7, "stop_loss": 1995.0, "take_profit": 2020.0},
        )
        r2 = client.post("/api/positions/close", json={"ticket": 7})
        assert r1.status_code == 200 and r1.json() == {"success": True}
        assert r2.status_code == 200 and r2.json() == {"success": True}
        assert om.modify_position_manual.called
        assert om.close_position_manual.called
        assert not engine.adapter.modify_position.called, (
            "web layer must not bypass OrderLifecycleManager (INV-004)"
        )
        assert not engine.adapter.close_position.called
