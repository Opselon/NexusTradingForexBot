"""Geometry tests for the adaptive exit policy (the 'risk-free too early' bug).

Every test drives the REAL policy with a REAL ``Position``/``SymbolInfo`` and
asserts the decision it returns — not numbers copied out of the module. The
central regression: a position that has earned a fraction of its planned risk
must NOT be declared risk-free, and a position that has earned enough must get
a lock that actually covers the round-trip cost.
"""

from __future__ import annotations

import json

import pytest

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo
from nexus_scalp.execution.lifecycle.exit_policy import (
    ExitAction,
    ExitPlan,
    ExitPolicyConfig,
    ExitProtectionPolicy,
    artifacts_dir,
    build_policy,
    load_learned_profile,
    normalize_regime,
)

CONTRACT = 100.0  # XAUUSD
ENTRY = 2400.00
RISK_USD = 150.0  # planned risk
R_UNIT = RISK_USD / (0.10 * CONTRACT)  # 15.00 USD of price per 1R at 0.10 lots


def position(
    profit: float,
    *,
    side: OrderType = OrderType.BUY,
    sl: float = 0.0,
    volume: float = 0.10,
) -> Position:
    return Position(
        ticket=9001,
        symbol="XAUUSD",
        type=side,
        volume=volume,
        price_open=ENTRY,
        sl=sl,
        tp=0.0,
        profit=profit,
        magic=770001,
    )


def symbol_info(digits: int = 2, point: float = 0.01) -> SymbolInfo:
    return SymbolInfo(
        symbol="XAUUSD",
        digits=digits,
        point=point,
        tick_size=point,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        freeze_level=0,
        trade_contract_size=CONTRACT,
    )


def plan(
    profit: float,
    *,
    mfe: float | None = None,
    hold: float = 60.0,
    atr: float = 0.35,
    regime: str = "",
    side: OrderType = OrderType.BUY,
    sl: float = 0.0,
    spread: float = 0.05,
    policy: ExitProtectionPolicy | None = None,
) -> ExitPlan:
    return (policy or ExitProtectionPolicy()).plan(
        position(profit, side=side, sl=sl),
        risk_usd=RISK_USD,
        atr_price=atr,
        contract_size=CONTRACT,
        spread_price=spread,
        hold_seconds=hold,
        mfe_usd=mfe if mfe is not None else profit,
        regime=regime,
        symbol_info=symbol_info(),
    )


# --------------------------------------------------------------------------- #
# the defect: early risk-free
# --------------------------------------------------------------------------- #
def test_a_quarter_r_of_profit_is_not_risk_free():
    """The reported bug: 0.25R used to trigger a BE lock. It must not now."""
    result = plan(0.25 * RISK_USD)
    assert result.action == ExitAction.HOLD
    assert "below de-risk threshold" in result.reason


def test_partial_profit_gets_partial_de_risk_not_breakeven():
    """0.6R earns HALF the risk back, not a flat stop at entry."""
    result = plan(0.60 * RISK_USD, atr=0.20)
    assert result.action == ExitAction.PARTIAL_DE_RISK
    assert result.new_sl == pytest.approx(ENTRY - 0.45 * R_UNIT, abs=0.01)
    assert result.new_sl < ENTRY  # never at/above entry


def test_full_risk_free_requires_the_floor_and_the_atr():
    result = plan(1.2 * RISK_USD, atr=0.35)
    assert result.action == ExitAction.BREAKEVEN_LOCK
    assert result.new_sl is not None and result.new_sl > ENTRY


def test_the_lock_covers_round_trip_cost_not_just_entry():
    cfg = ExitPolicyConfig(round_trip_cost_usd_per_lot=3.0, lock_buffer_pips=0.6)
    result = plan(1.5 * RISK_USD, atr=0.20, policy=ExitProtectionPolicy(cfg))
    assert result.action == ExitAction.BREAKEVEN_LOCK
    # 3.0 USD/lot on 0.10 lots = $0.30 over 10 oz = $0.03/oz, plus the buffer:
    assert result.new_sl > ENTRY + 0.03


def test_sell_side_mirrors_the_geometry():
    result = plan(1.4 * RISK_USD, atr=0.20, side=OrderType.SELL)
    assert result.action == ExitAction.BREAKEVEN_LOCK
    assert result.new_sl is not None and result.new_sl < ENTRY


# --------------------------------------------------------------------------- #
# guards that made the old trigger fire on noise
# --------------------------------------------------------------------------- #
def test_minimum_dwell_blocks_an_instant_lock():
    result = plan(2.0 * RISK_USD, hold=3.0)
    assert result.action == ExitAction.HOLD
    assert "dwell" in result.reason


def test_a_collapsed_spike_does_not_de_risk_the_trade():
    """MFE was enough but the move already gave it back: keep the plan."""
    result = plan(0.35 * RISK_USD, mfe=1.3 * RISK_USD)
    assert result.action == ExitAction.HOLD
    assert "collapsed" in result.reason


def test_atr_noise_floor_delays_a_thin_move():
    """A 5.0 ATR against a $30 stop makes 1.1R a wick, not a move."""
    policy = ExitProtectionPolicy()
    result = policy.plan(
        position(33.0),
        risk_usd=30.0,
        atr_price=5.0,
        contract_size=CONTRACT,
        spread_price=0.05,
        hold_seconds=60.0,
        mfe_usd=33.0,
        symbol_info=symbol_info(),
    )
    # Full risk-free is refused (the floor is 1.33R); the trade may only take
    # the half-risk ladder step, which is the whole point of the ladder.
    assert result.action == ExitAction.PARTIAL_DE_RISK
    # 0.8 * 5.0 / (30 / 10) = 1.33R floor > 1.1R reached
    assert result.trigger_r > 1.1


def test_without_risk_provenance_the_original_stop_stands():
    result = ExitProtectionPolicy().plan(
        position(500.0),
        risk_usd=0.0,
        atr_price=0.4,
        contract_size=CONTRACT,
        hold_seconds=600.0,
    )
    assert result.action == ExitAction.HOLD
    assert "provenance" in result.reason


def test_a_stop_already_beyond_the_lock_is_not_modified_again():
    result = plan(2.0 * RISK_USD, atr=0.2, sl=ENTRY + 5.0)
    assert result.action == ExitAction.HOLD
    assert "already at/beyond" in result.reason


# --------------------------------------------------------------------------- #
# regime awareness + learned overlay
# --------------------------------------------------------------------------- #
def test_trend_regime_demands_more_than_range():
    trend = plan(1.0 * RISK_USD, atr=0.2, regime="TREND")
    rng = plan(1.0 * RISK_USD, atr=0.2, regime="RANGE")
    # 1.0R clears the range floor (0.7R) but not the trend floor (1.2R):
    assert rng.action == ExitAction.BREAKEVEN_LOCK
    assert trend.action == ExitAction.PARTIAL_DE_RISK
    assert trend.new_sl is not None and trend.new_sl < ENTRY


def test_regime_aliases_collapse_onto_three_buckets():
    assert normalize_regime("VOLATILITY_EXPANSION") == "trend"
    assert normalize_regime("RANGING") == "range"
    assert normalize_regime("weird") == "default"
    assert normalize_regime(None) == "default"


def test_a_learned_profile_raises_the_bar():
    base = ExitProtectionPolicy()
    learned = ExitProtectionPolicy(learned={"TREND": 2.2})
    assert (
        plan(1.5 * RISK_USD, atr=0.2, regime="TREND", policy=base).action
        == ExitAction.BREAKEVEN_LOCK
    )
    # With a 2.2R learned floor the same trade may only take the ladder step:
    assert (
        plan(1.5 * RISK_USD, atr=0.2, regime="TREND", policy=learned).action
        == ExitAction.PARTIAL_DE_RISK
    )


def test_an_absurd_learned_value_is_rejected():
    policy = ExitProtectionPolicy(learned={"TREND": 99.0})
    assert policy.learned_trigger_r("TREND") is None


def test_malformed_artifact_is_ignored_and_defaults_apply(tmp_path):
    (tmp_path / "exit_policy.json").write_text("{not json", encoding="utf-8")
    assert load_learned_profile(tmp_path) == {}
    assert build_policy(tmp_path).learned_trigger_r("TREND") is None


def test_optimizer_artifact_is_loaded_and_applied(tmp_path):
    (tmp_path / "exit_policy.json").write_text(
        json.dumps({"version": 2, "regimes": {"TREND": 1.75, "RANGE": 0.9}}),
        encoding="utf-8",
    )
    learned = load_learned_profile(tmp_path)
    assert learned == {"TREND": 1.75, "RANGE": 0.9}
    policy = build_policy(tmp_path)
    # The learned floor wins for TREND, and remains bounded by the ceiling.
    floor, _ = policy.trigger_floor_r("TREND", R_UNIT, 0.0)
    assert floor == pytest.approx(1.75)


def test_artifacts_dir_honours_the_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_ARTIFACTS_DIR", str(tmp_path))
    assert artifacts_dir() == tmp_path
