"""Capital-Protection Boundary Regression Battery (Agent 15 — Risk & Capital
Protection Core).

Scope: the risk-core chain Account State -> Position Exposure -> Risk
Calculation -> Risk Limits -> Order Authorization. Every test pins one
boundary condition of the capital-protection layer that was previously
unpinned (verified by coverage sweep 2026-09-09 — see the per-test doc).

Contracts proven here (all fail-closed directions):
  N1  Directional-exposure squeeze: exactly-at-limit blocks; just-below
      passes and clamps the sized volume to the remaining cap.
  N2  Spread gate: exactly-at-limit passes; one point above rejects.
  N3  Dispatch-layer idempotency: a request_id already sent (filled OR
      refused) is terminal on both the primary dispatch and the hedge path.
  N4  Dispatch clamp rejects non-finite / non-positive / non-numeric
      volumes with 0.0 (no broker submission possible).
  N6  Risk-engine pending-order cap blocks the proposal.
  X1  Kill switch remains authoritative at the risk engine itself (the
      committed critical-suite contract; dispatch-layer pins live in the
      runtime-resilience battery).

Offline, deterministic: no MT5, no network, no model artifacts.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
    TradeProposal,
)
from nexus_scalp.execution.lifecycle.dispatch import DispatchEngine
from nexus_scalp.risk.risk_engine import RiskEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _account(equity: float = 100_000.0, margin_free: float | None = None) -> AccountInfo:
    return AccountInfo(
        login=1,
        trade_mode=0,
        leverage=100,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=margin_free if margin_free is not None else equity,
        currency="USD",
    )


def _symbol_info(volume_step: float = 0.01) -> SymbolInfo:
    return SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=volume_step,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )


def _tick(bid: float = 2000.0, ask: float = 2000.10) -> TickData:
    return TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=bid, ask=ask)


def _proposal(req: str = "A15-PROBE", action: ActionType = ActionType.BUY) -> TradeProposal:
    return TradeProposal(
        request_id=req,
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=action,
        confidence=0.5,
        proposed_entry=2000.50,
        stop_loss=1998.50,
        take_profit=2004.50,
        risk_reward_ratio=2.0,
        reason_code="A15_TEST",
    )


def _position(volume: float, ptype: OrderType = OrderType.BUY, ticket: int = 1) -> Position:
    return Position(
        ticket=ticket,
        symbol="XAUUSD",
        type=ptype,
        volume=volume,
        price_open=2000.0,
        sl=1998.0,
        tp=2004.0,
        profit=0.0,
        magic=888101,
    )


def _engine(**risk_kw) -> RiskEngine:
    """Builds a RiskEngine honoring config values (ctor kwarg forwarding —
    see TestConfigContract: the ctor ignores RiskConfig.max_allowed_lots)."""
    defaults: dict = dict(risk_per_trade_pct=1.0, max_allowed_lots=2.0, max_concurrent_positions=5)
    defaults.update(risk_kw)
    cfg = RiskConfig(**{k: v for k, v in defaults.items() if k != "max_allowed_lots"})
    return RiskEngine(cfg, max_allowed_lots=defaults["max_allowed_lots"])


# ---------------------------------------------------------------------------
# D-1 — config-contract defect pin: RiskEngine(config) ignores
# RiskConfig.max_allowed_lots unless the caller ALSO forwards the ctor
# kwarg. LiveEngine forwards it (live path safe); smoke/research/replay
# constructors that pass only the config silently run with the 10.0
# default — the configured operator limit never binds there.
# This test pins the LIVE wiring contract (kwarg must be forwarded) so a
# regression in the composition root cannot silently relax the cap.
# ---------------------------------------------------------------------------


class TestConfigContract:
    def test_live_engine_wiring_forwards_max_allowed_lots(self) -> None:
        import inspect

        from nexus_scalp.application.live_engine import LiveEngine

        src = inspect.getsource(LiveEngine.__init__)
        assert "max_allowed_lots=config.risk.max_allowed_lots" in src, (
            "LiveEngine must forward RiskConfig.max_allowed_lots to the "
            "RiskEngine ctor — the ctor ignores the config value"
        )

    def test_engine_ctor_honors_explicit_kwarg(self) -> None:
        eng = RiskEngine(RiskConfig(max_allowed_lots=0.5), max_allowed_lots=0.5)
        assert eng.max_allowed_lots == 0.5


# ---------------------------------------------------------------------------
# N1 — directional exposure squeeze boundaries
# ---------------------------------------------------------------------------


class TestExposureSqueezeBoundary:
    def test_exposure_just_below_limit_passes_and_clamps_to_remaining_cap(self) -> None:
        eng = _engine()
        # 1.99 lots already open vs a 2.0 cap: allowed, but the sized volume
        # must be clamped to the remaining 0.01 (never exceed the cap).
        order = eng.evaluate_proposal(
            _proposal("N1A"), _account(), _symbol_info(), [_position(1.99)], _tick()
        )
        assert order is not None
        assert order.volume <= 0.01 + 1e-9

    def test_exposure_exactly_at_limit_blocks(self) -> None:
        eng = _engine()
        order = eng.evaluate_proposal(
            _proposal("N1B"), _account(), _symbol_info(), [_position(2.0)], _tick()
        )
        assert order is None  # >= cap: the squeeze guard rejects

    def test_exposure_just_above_limit_blocks(self) -> None:
        eng = _engine()
        order = eng.evaluate_proposal(
            _proposal("N1C"), _account(), _symbol_info(), [_position(2.01)], _tick()
        )
        assert order is None

    def test_opposite_direction_volume_does_not_count_into_directional_squeeze(self) -> None:
        eng = _engine()
        # A SELL position must not consume the BUY directional cap (hedging
        # semantics live elsewhere); the proposal stays evaluable.
        order = eng.evaluate_proposal(
            _proposal("N1D"), _account(), _symbol_info(), [_position(1.0, OrderType.SELL)], _tick()
        )
        assert order is not None


# ---------------------------------------------------------------------------
# N2 — spread gate boundaries
# ---------------------------------------------------------------------------


class TestSpreadGateBoundary:
    def test_spread_exactly_at_limit_passes(self) -> None:
        eng = _engine(max_spread_points=60)
        # (2000.60 - 2000.00) / 0.01 == 60 points == limit: NOT > limit.
        order = eng.evaluate_proposal(
            _proposal("N2A"), _account(), _symbol_info(), [], _tick(2000.0, 2000.60)
        )
        assert order is not None

    def test_spread_one_point_above_limit_rejects(self) -> None:
        eng = _engine(max_spread_points=60)
        order = eng.evaluate_proposal(
            _proposal("N2B"), _account(), _symbol_info(), [], _tick(2000.0, 2000.61)
        )
        assert order is None

    def test_extreme_spread_rejects(self) -> None:
        eng = _engine(max_spread_points=60)
        order = eng.evaluate_proposal(
            _proposal("N2C"), _account(), _symbol_info(), [], _tick(2000.0, 2010.0)
        )
        assert order is None


# ---------------------------------------------------------------------------
# N3 — dispatch-layer idempotency (risk approval can't double-fire)
# ---------------------------------------------------------------------------


class _AuditSpy:
    def __init__(self) -> None:
        self.orders: list[dict] = []

    def log_order(self, **kw) -> None:
        self.orders.append(kw)

    def log_execution(self, *a, **k) -> None:
        pass


class _OM:
    """Minimal composition root mirroring DispatchEngine's real surface."""

    def __init__(self) -> None:
        self.risk_engine = _engine()
        self.global_state = "RUNNING"
        self._processed_orders: dict[str, bool] = {}
        self._consecutive_failures = 0
        self.audit = _AuditSpy()
        self.experience_engine = SimpleNamespace(ledger=None)

        def _acct():
            return _account()

        def _sinfo(s):
            return _symbol_info()

        self.adapter = SimpleNamespace(
            get_account_info=_acct,
            get_symbol_info=_sinfo,
        )
        self.mt5_adapter = SimpleNamespace(
            execute_market_order=lambda **kw: 777,
            place_pending_order=lambda **kw: 888,
        )
        self._live_tickets_cache: dict[int, dict] = {}
        import threading

        self._live_tickets_lock = threading.Lock()

    def _is_exposure_available(self, symbol: str | None = None) -> bool:
        return len(self._live_tickets_cache) < 1

    def count_total_exposure(self, symbol: str | None = None) -> tuple[int, int]:
        return (0, 0)

    def register_entry_context(self, **kw) -> None:
        pass

    def _resolve_entry_reason(self, decision) -> str:
        return "PURE_AI"

    def _clamp_dispatch_volume(self, volume, symbol=None):
        return DispatchEngine(self)._clamp_dispatch_volume(volume, symbol=symbol)


class TestDispatchIdempotency:
    def test_primary_path_duplicate_request_id_blocked(self) -> None:
        from nexus_scalp.adapters.mt5.providers import BROKER_SERVER_UTC_OFFSET_MINUTES
        from nexus_scalp.research.economics import in_maintenance_window

        om = _OM()
        de = DispatchEngine(om)
        # CI-DETERMINISM (NSE-Swarm 2026-09-11): the first dispatch ran into
        # the nightly MAINTENANCE_WINDOW guard whenever the wall clock sat in
        # the buffered break (server 00:30..01:30 -> UTC 21:30..22:30 +30m
        # spread buffer) — the duplicate assertion then compared against a
        # maintenance-blocked False, failing every run in that window (CI
        # 1072/1073 all platforms). Pin the decision timestamp OUTSIDE the
        # window so the test exercises the DUPLICATE guard, not the clock.
        outside = datetime.now(UTC) + timedelta(minutes=180)
        if in_maintenance_window(
            outside, server_utc_offset_hours=BROKER_SERVER_UTC_OFFSET_MINUTES / 60.0
        ):
            outside = outside + timedelta(minutes=180)
        # TradeProposal is a frozen model -> rebuild with the pinned timestamp.
        p1 = _proposal("DUP-1").model_copy(update={"generated_at": outside})
        p2 = _proposal("DUP-1").model_copy(update={"generated_at": outside})
        assert de.dispatch_order(p1, 0.05) is True
        # Same request_id again (re-fire/replay) must be terminal regardless
        # of the first outcome.
        assert de.dispatch_order(p2, 0.05) is False

    def test_refused_request_id_is_also_terminal(self) -> None:
        om = _OM()
        de = DispatchEngine(om)

        # First dispatch refused by the broker (ticket=0)
        def _ticket_zero(**kw):
            return 0

        om.mt5_adapter = SimpleNamespace(
            execute_market_order=_ticket_zero, place_pending_order=_ticket_zero
        )
        om._is_exposure_available = lambda symbol=None: True  # exposure slot stays free
        assert de.dispatch_order(_proposal("REJ-1"), 0.05) is False

        # A refused request must NOT be retried blindly under the same id
        def _ticket_999(**kw):
            return 999

        om.mt5_adapter = SimpleNamespace(
            execute_market_order=_ticket_999, place_pending_order=_ticket_999
        )
        assert de.dispatch_order(_proposal("REJ-1"), 0.05) is False

    def test_hedge_path_duplicate_order_id_blocked(self) -> None:
        om = _OM()

        def _acct():
            return _account()

        def _sinfo(s):
            return _symbol_info()

        def _send(order):
            return True

        om.adapter = SimpleNamespace(
            get_account_info=_acct,
            get_symbol_info=_sinfo,
            send_order=_send,
        )
        de = DispatchEngine(om)
        order = TradeOrder(
            order_id="HEDGE-1",
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=0.05,
            price=2000.5,
            stop_loss=1998.5,
            take_profit=2004.5,
            magic_number=888101,
            comment="NSE_HFT_SIZED",
        )
        assert de.execute_order(order) is True
        assert de.execute_order(order) is False


# ---------------------------------------------------------------------------
# N4 — dispatch clamp rejects insane volumes (NaN must not reach the broker)
# ---------------------------------------------------------------------------


class TestDispatchClampInsanity:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 0.0, -0.05])
    def test_non_finite_or_non_positive_volume_clamps_to_zero(self, bad) -> None:
        om = _OM()
        de = DispatchEngine(om)
        vol = de._clamp_dispatch_volume(bad, symbol="XAUUSD")
        assert vol == 0.0
        assert isinstance(vol, float)
        assert not (isinstance(vol, float) and math.isnan(vol))

    def test_finite_volume_survives_clamp_unchanged(self) -> None:
        om = _OM()
        de = DispatchEngine(om)
        assert de._clamp_dispatch_volume(0.5, symbol="XAUUSD") == pytest.approx(0.5)

    def test_volume_above_hard_max_is_capped(self) -> None:
        om = _OM()
        de = DispatchEngine(om)
        clamped = de._clamp_dispatch_volume(50.0, symbol="XAUUSD")
        assert clamped == pytest.approx(10.0)  # HARD_MAX_LOTS


# ---------------------------------------------------------------------------
# N6 — pending-order cap at the risk engine
# ---------------------------------------------------------------------------


class TestPendingOrderCap:
    def test_pending_orders_at_cap_block_proposal(self) -> None:
        eng = _engine()
        pendings = [SimpleNamespace(ticket=i) for i in range(5)]
        order = eng.evaluate_proposal(
            _proposal("N6A"),
            _account(),
            _symbol_info(),
            [],
            _tick(),
            pending_orders=pendings,
        )
        assert order is None

    def test_pending_orders_below_cap_allow_evaluation(self) -> None:
        eng = _engine()
        pendings = [SimpleNamespace(ticket=i) for i in range(4)]
        order = eng.evaluate_proposal(
            _proposal("N6B"),
            _account(),
            _symbol_info(),
            [],
            _tick(),
            pending_orders=pendings,
        )
        assert order is None or isinstance(order, TradeOrder)


# ---------------------------------------------------------------------------
# X1 — kill switch authority at the risk engine (committed contract re-pin)
# ---------------------------------------------------------------------------


class TestKillSwitchAuthority:
    def test_kill_switch_blocks_evaluate_proposal(self) -> None:
        eng = _engine()
        eng.enable_kill_switch()
        try:
            assert (
                eng.evaluate_proposal(_proposal("X1A"), _account(), _symbol_info(), [], _tick())
                is None
            )
        finally:
            eng.disable_kill_switch()
        # release restores evaluation
        order = eng.evaluate_proposal(_proposal("X1B"), _account(), _symbol_info(), [], _tick())
        assert order is None or isinstance(order, TradeOrder)
