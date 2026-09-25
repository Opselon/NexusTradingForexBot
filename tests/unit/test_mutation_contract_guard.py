"""MUTATION CONTRACT GUARD BATTERY (QA hardening mission).

The battery owned by the domain-contract mutations in
scripts/qa/run_mutations.py (CONTRACT_MUTATIONS). Each test pins one
CRITICAL SAFETY INVARIANT; the paired mutation flips exactly the guard
and this battery must FAIL (mutation KILLED) when it does:

    MUT-RISK-KILLSWITCH   kill-switch must veto every proposal
    MUT-TEMPORAL-PURGE    purge gap between train/val is materialized
    MUT-TEMPORAL-EMBARGO  embargo gap between val/test is materialized
    MUT-FRESHNESS-GATE    stale market/feature/inference state -> STALE
    MUT-SHADOW-BOUNDARY   SHADOW mode never executes (observation-only)
    MUT-SM-HYSTERESIS     time+count debounce: time leg cannot be dropped
    MUT-RB-HORIZON        recovery horizon clamped into [min, max]

Deterministic, offline, no MT5, no network; xdist-safe (module-scoped
state only via fresh objects per test).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    SymbolInfo,
    TickData,
    TradeProposal,
)
from nexus_scalp.execution.position_state_machine import PositionStateMachine
from nexus_scalp.execution.position_states import PositionState
from nexus_scalp.execution.recovery_budget import RecoveryBudgetLedger
from nexus_scalp.risk.risk_engine import RiskEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)


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


def _tick(now: datetime | None = None) -> TickData:
    return TickData(symbol="XAUUSD", timestamp=now or _NOW, bid=1999.9, ask=2000.1)


def _account(equity: float = 10_000.0) -> AccountInfo:
    return AccountInfo(
        login=1,
        trade_mode=0,
        leverage=100,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=equity * 2.0,
        currency="USD",
    )


def _buy_proposal(confidence: float = 0.9) -> TradeProposal:
    return TradeProposal(
        request_id="mutguard-req-1",
        symbol="XAUUSD",
        generated_at=_NOW,
        action=ActionType.BUY_MARKET,
        confidence=confidence,
        proposed_entry=2000.0,
        stop_loss=1998.0,
        take_profit=2006.0,
        risk_reward_ratio=2.5,
        execution_mode="STANDARD",
    )


# ---------------------------------------------------------------------------
# MUT-RISK-KILLSWITCH: the emergency kill switch must veto EVERY proposal
# ---------------------------------------------------------------------------
def test_kill_switch_vetoes_all_execution() -> None:
    engine = RiskEngine(RiskConfig(risk_per_trade_pct=0.5))
    engine.enable_kill_switch()
    order = engine.evaluate_proposal(
        _buy_proposal(), _account(), _gold_symbol(), [], _tick(), atr=1.5
    )
    assert order is None, "kill switch active: proposal must be rejected"


# ---------------------------------------------------------------------------
# MUT-TEMPORAL-PURGE / MUT-TEMPORAL-EMBARGO: the purge/embargo defaults are
# the leak-protection floor. They are asserted as materially > 0 so zeroing
# them (the mutation) fails this battery.
# ---------------------------------------------------------------------------
def test_temporal_purge_default_is_material() -> None:
    from nexus_scalp.research.splitting import DEFAULT_PURGE_SECONDS

    assert DEFAULT_PURGE_SECONDS > 0.0, (
        "purge gap zeroed: train/validation would overlap (lookahead leak)"
    )


def test_temporal_embargo_default_is_material() -> None:
    from nexus_scalp.research.splitting import DEFAULT_EMBARGO_SECONDS

    assert DEFAULT_EMBARGO_SECONDS > 0.0, (
        "embargo gap zeroed: validation/test would overlap (lookahead leak)"
    )


# ---------------------------------------------------------------------------
# MUT-FRESHNESS-GATE: a stamp older than max_age MUST classify STALE.
# ---------------------------------------------------------------------------
def test_freshness_stale_stamp_is_stale() -> None:
    from nexus_scalp.application.live_freshness import LiveFreshnessService

    old_stamp = datetime.now(UTC) - timedelta(seconds=9_999.0)
    state, _age = LiveFreshnessService.stage_freshness(old_stamp, 5.0)
    assert state == "STALE", "stale stamp admitted as fresh (freshness gate bypassed)"


# ---------------------------------------------------------------------------
# MUT-SHADOW-BOUNDARY: SHADOW mode is observation-only. The literal marker
# the boundary stamps is source-pinned (same discipline as
# test_launcher_paper_boundary_bug212) AND the downgrade path is pinned via
# the reason-code contract.
# ---------------------------------------------------------------------------
def test_shadow_boundary_marker_present() -> None:
    from pathlib import Path

    import nexus_scalp.application.live.decision_executor as de

    src = Path(de.__file__).read_text(encoding="utf-8")
    assert "SHADOW_OBSERVATION_ONLY" in src, "SHADOW downgrade marker missing"
    assert "ExecutionMode.SHADOW" in src, "SHADOW mode check removed from decision executor"


# ---------------------------------------------------------------------------
# MUT-SM-HYSTERESIS: time hysteresis leg. With min_dur unmet, a candidate
# seen min_cnt times must NOT transition (both legs required).
# ---------------------------------------------------------------------------
class _FakeClock:
    def __init__(self) -> None:
        self.t0 = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)

    def at(self, seconds: float) -> datetime:
        return self.t0 + timedelta(seconds=seconds)


def test_state_machine_time_leg_cannot_be_dropped() -> None:
    h = PositionStateMachine(lambda: (10.0, 3))
    clock = _FakeClock()
    t = 4242
    h.transition_with_hysteresis(t, PositionState.PROFIT_UNPROTECTED, clock.at(0.0))
    # count satisfied (3 more sightings) but time NOT satisfied (0.9s < 10s)
    for k in range(1, 4):
        out = h.transition_with_hysteresis(t, PositionState.PROFIT_PROTECTED, clock.at(0.3 * k))
        assert out is PositionState.PROFIT_UNPROTECTED, (
            "transition confirmed before min_duration: time hysteresis leg dropped"
        )


# ---------------------------------------------------------------------------
# MUT-RB-HORIZON: recovery horizon must stay within [min, max] for extreme
# inputs (max->min clamp inversion produces horizons BELOW the minimum).
# ---------------------------------------------------------------------------
class _Cfg:
    def __init__(self) -> None:
        self.recovery_budget_pct_of_r = 0.50
        self.default_recovery_horizon_sec = 180.0
        self.min_recovery_horizon_sec = 30.0
        self.max_recovery_horizon_sec = 600.0


def test_recovery_horizon_respects_clamp() -> None:
    ledger = RecoveryBudgetLedger()
    ledger.allocate(
        77,
        initial_risk_usd=100.0,
        current_pnl_usd=-50.0,
        confidence_factor=5.0,  # extreme -> raw horizon far above max
        atr=0.01,  # extreme volatility scaling
        trend_strength=-2.0,
        now=_FakeClock().at(0.0),
        algo_config=_Cfg(),
    )
    horizon = ledger.recovery_horizons[77]
    assert 30.0 <= horizon <= 600.0, f"recovery horizon {horizon} escaped [30, 600] clamp"
