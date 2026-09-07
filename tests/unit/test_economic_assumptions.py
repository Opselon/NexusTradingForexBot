"""Canonical Economic Assumptions Layer — behavioral suite (ECON v1).

Covers the high-leverage economic-parity invariants:
  * friction defaults fail-safe (production-like is the implicit default)
  * explicit frictionless override is labelled and non-promotable
  * zero-friction override stays an explicit construction (never silent)
  * swap accounting: crossing, long/short sign, unset rates fail loudly
  * rollover window derivation from server time, not hardcoded UTC
  * canonical sizing == live RiskEngine sizing (same inputs, same decision)
  * sizing factors match live RiskEngine semantics (confidence/drawdown/regime)
  * provenance identity is complete and round-trips
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus_scalp.research.economics import (
    EconomicAssumptions,
    ExecutionProfile,
    FrictionAssumptions,
    InstrumentEconomics,
    SizingPolicy,
    SwapProfile,
    in_maintenance_window,
    normalize_assumptions,
    rollover_crossings,
)
from nexus_scalp.research.models import ExecutionAssumptions


# =============================================================================
# Profiles: fail-closed defaults
# =============================================================================


def test_default_assumptions_are_production_like() -> None:
    """The implicit default must be the conservative validation profile."""
    a = EconomicAssumptions()  # bare construction
    assert a.profile == ExecutionProfile.PRODUCTION_LIKE
    assert a.friction.spread_ticks > 0.0
    assert a.friction.slippage_ticks > 0.0


def test_none_normalized_to_production_like() -> None:
    a = normalize_assumptions(None)
    assert a.profile == ExecutionProfile.PRODUCTION_LIKE
    assert a.friction.spread_ticks > 0.0
    assert a.friction.slippage_ticks > 0.0


def test_frictionless_mode_is_explicit_and_labelled() -> None:
    a = EconomicAssumptions.frictionless_research()
    assert a.profile == ExecutionProfile.FRICTIONLESS_RESEARCH
    assert a.friction.spread_ticks == 0.0
    assert a.friction.slippage_ticks == 0.0
    assert not a.is_promotable_economics()


def test_legacy_execution_assumptions_stay_explicit_choice() -> None:
    """Passing a legacy ExecutionAssumptions is an explicit caller decision:
    the friction values travel unchanged and the run is labelled analytical."""
    legacy = ExecutionAssumptions(spread_ticks=3.0, slippage_ticks=1.0, latency_ms=50.0)
    a = normalize_assumptions(legacy)
    assert a.profile == ExecutionProfile.FRICTIONLESS_RESEARCH
    assert a.friction.spread_ticks == 3.0
    assert a.friction.slippage_ticks == 1.0
    assert a.friction.latency_ms == 50.0


def test_production_economics_requires_swap() -> None:
    """Production-like economics without explicit swap rates is NOT promotable."""
    a = EconomicAssumptions.production_like()
    assert not a.is_promotable_economics()  # swap rates unset -> fail-closed
    complete = EconomicAssumptions.production_like(
        swap=SwapProfile(long_usd_per_lot_per_rollover=-5.0, short_usd_per_lot_per_rollover=2.0)
    )
    assert complete.is_promotable_economics()


def test_provenance_reconstructs_the_economic_world() -> None:
    a = EconomicAssumptions.production_like(
        swap=SwapProfile(long_usd_per_lot_per_rollover=-5.0, short_usd_per_lot_per_rollover=2.0)
    )
    p = a.provenance()
    assert p["economic_assumptions_version"] == "ECON_V1"
    assert p["execution_profile"] == "PRODUCTION_LIKE"
    assert p["swap_complete"] is True
    assert p["promotable_economics"] is True
    for key in ("friction", "swap", "instrument", "sizing"):
        assert key in p
    # round-trips through model validation
    rebuilt = EconomicAssumptions.model_validate(
        {**a.model_dump(), "profile": a.profile}
    )
    assert rebuilt.provenance() == p


# =============================================================================
# Swap / rollover accounting
# =============================================================================


def test_swap_counts_rollover_crossings_by_direction() -> None:
    sp = SwapProfile(long_usd_per_lot_per_rollover=-6.0, short_usd_per_lot_per_rollover=2.0)
    entry = datetime(2026, 9, 1, 22, 0)
    exit_ = datetime(2026, 9, 2, 3, 0)
    assert sp.swap_usd("BUY", 1.0, entry, exit_) == -6.0
    assert sp.swap_usd("SELL", 2.0, entry, exit_) == 4.0
    # no crossing -> zero swap even for a position
    assert sp.swap_usd("BUY", 1.0, datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 20, 0)) == 0.0
    # multi-day hold crossing several rollover instants (Fri 22:00 ->
    # Mon 03:00 server time crosses Sat 00:00, Sun 00:00, Mon 00:00)
    assert sp.swap_usd("BUY", 1.0, datetime(2026, 9, 4, 22, 0), datetime(2026, 9, 7, 3, 0)) == -18.0


def test_swap_unset_rates_fail_closed() -> None:
    sp = SwapProfile()
    with pytest.raises(ValueError, match="fail-closed"):
        sp.swap_usd("BUY", 1.0, datetime(2026, 9, 1), datetime(2026, 9, 2))


def test_rollover_crossing_boundary_semantics() -> None:
    e = datetime(2026, 9, 1, 22, 0)
    assert rollover_crossings(e, datetime(2026, 9, 2, 0, 0)) == 1  # at instant counts
    assert rollover_crossings(e, e) == 0  # zero-length hold
    assert rollover_crossings(e, datetime(2026, 9, 1, 23, 0)) == 0  # exit before instant


def test_maintenance_window_crosses_midnight() -> None:
    assert in_maintenance_window(datetime(2026, 9, 1, 23, 10)) is True
    assert in_maintenance_window(datetime(2026, 9, 1, 0, 30)) is True
    assert in_maintenance_window(datetime(2026, 9, 1, 12, 0)) is False
    assert in_maintenance_window(datetime(2026, 9, 1, 2, 0)) is False


# =============================================================================
# Canonical sizing == live RiskEngine
# =============================================================================


def _live_sizing_volume(equity: float, entry: float, sl: float, risk_pct: float) -> float:
    """The live engine's dynamic volume for identical inputs."""
    from nexus_scalp.configuration.config import RiskConfig
    from nexus_scalp.domain.models import AccountInfo, SymbolInfo
    from nexus_scalp.risk.risk_engine import RiskEngine

    engine = RiskEngine(config=RiskConfig(risk_per_trade_pct=risk_pct))
    account = AccountInfo(
        login=1,
        trade_mode=0,
        leverage=100,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=equity,
    )
    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=0.1,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )
    volume, _ = engine.calculate_dynamic_volume(
        entry=entry, sl=sl, account=account, symbol_info=symbol_info, risk_pct=risk_pct
    )
    return volume


@pytest.mark.parametrize(
    "equity,entry,sl,risk_pct",
    [
        (500.0, 2000.0, 1998.0, 1.0),
        (1000.0, 2000.0, 1998.0, 1.0),
        (10000.0, 2000.0, 1998.0, 1.0),
        (47000.0, 4000.0, 3996.0, 0.5),
        (100000.0, 3300.0, 3297.0, 0.5),
    ],
)
def test_sizing_matches_live_risk_engine(equity: float, entry: float, sl: float, risk_pct: float) -> None:
    """Same inputs -> same sizing decision as the live engine (no drift)."""
    from nexus_scalp.research.economics import compute_sizing

    decision = compute_sizing(
        policy=SizingPolicy(base_risk_pct=risk_pct),
        instrument=InstrumentEconomics(),
        equity=equity,
        peak_equity=equity,  # no drawdown: raw risk sizing
        entry=entry,
        stop_loss=sl,
        confidence=0.85,  # neutral scalar: exactly 1.0 (the live divisor)
        regime=None,
    )
    live = _live_sizing_volume(equity, entry, sl, risk_pct)
    assert decision.volume == pytest.approx(live)


def test_sizing_confidence_factor_matches_live() -> None:
    """confidence 0.85 -> scalar 1.0; volume equals raw live risk sizing."""
    from nexus_scalp.research.economics import compute_sizing

    decision = compute_sizing(
        policy=SizingPolicy(base_risk_pct=1.0),
        instrument=InstrumentEconomics(),
        equity=10000.0,
        peak_equity=10000.0,
        entry=2000.0,
        stop_loss=1998.0,
        confidence=0.85,
        regime=None,
    )
    assert decision.factors["confidence"] == 1.0
    live = _live_sizing_volume(10000.0, 2000.0, 1998.0, 1.0)
    assert decision.volume == pytest.approx(live)


def test_sizing_drawdown_penalty_is_causal_and_matches_live_formula() -> None:
    """2% drawdown -> penalty 0.6 (max(0.2, 1-0.2*2)); deep dd floors at 0.2."""
    from nexus_scalp.research.economics import compute_sizing

    decision = compute_sizing(
        policy=SizingPolicy(base_risk_pct=1.0),
        instrument=InstrumentEconomics(),
        equity=9800.0,
        peak_equity=10000.0,
        entry=2000.0,
        stop_loss=1998.0,
        confidence=0.85,
        regime=None,
    )
    assert decision.factors["drawdown"] == pytest.approx(0.6)
    live = _live_sizing_volume(9800.0, 2000.0, 1998.0, 0.6)
    assert decision.volume == pytest.approx(live)
    # deep drawdown floors at 0.2 (10% dd -> 1-2.0 < 0 -> floor)
    deep = compute_sizing(
        policy=SizingPolicy(base_risk_pct=1.0),
        instrument=InstrumentEconomics(),
        equity=9000.0,
        peak_equity=10000.0,
        entry=2000.0,
        stop_loss=1998.0,
        confidence=0.85,
        regime=None,
    )
    assert deep.factors["drawdown"] == 0.2
    live_deep = _live_sizing_volume(9000.0, 2000.0, 1998.0, 0.2)
    assert deep.volume == pytest.approx(live_deep)


def test_sizing_regime_scalar_vol_expansion() -> None:
    from nexus_scalp.research.economics import compute_sizing

    decision = compute_sizing(
        policy=SizingPolicy(base_risk_pct=1.0),
        instrument=InstrumentEconomics(),
        equity=10000.0,
        peak_equity=10000.0,
        entry=2000.0,
        stop_loss=1998.0,
        confidence=0.85,
        regime="VOLATILITY_EXPANSION",
    )
    assert decision.factors["regime"] == 0.5
    live = _live_sizing_volume(10000.0, 2000.0, 1998.0, 0.5)
    assert decision.volume == pytest.approx(live)


def test_sizing_invalid_inputs_fail_closed_to_zero() -> None:
    from nexus_scalp.research.economics import compute_sizing

    for equity in (0.0, -1.0, float("nan")):
        d = compute_sizing(
            policy=SizingPolicy(),
            instrument=InstrumentEconomics(),
            equity=equity,
            peak_equity=max(equity, 0.0),
            entry=2000.0,
            stop_loss=1998.0,
            confidence=0.9,
            regime=None,
        )
        assert d.volume == 0.0
        assert d.reason != "SUCCESS"


def test_zero_risk_distance_rejected() -> None:
    from nexus_scalp.research.economics import compute_sizing

    d = compute_sizing(
        policy=SizingPolicy(),
        instrument=InstrumentEconomics(),
        equity=10000.0,
        peak_equity=10000.0,
        entry=2000.0,
        stop_loss=2000.0,
        confidence=0.9,
        regime=None,
    )
    assert d.volume == 0.0


# =============================================================================
# Friction in R space (metrics.py parity)
# =============================================================================


def test_friction_r_semantics_match_metrics_contract() -> None:
    """friction_r == (ticks * price_tick) / risk_distance, capped at 0.5."""
    # raw bundle: no explicit cap -> legacy 5-tick runaway guard applies
    f = FrictionAssumptions(spread_ticks=3.0, slippage_ticks=2.0, price_tick=0.01)
    assert f.friction_r(2.0) == pytest.approx(0.05 / 2.0)
    # no risk distance -> absolute per-tick floor semantics
    assert f.friction_r(0.0) == pytest.approx(0.01 * 5.0)  # legacy cap bounds the floor
    # zero friction -> zero
    assert FrictionAssumptions().friction_r(2.0) == 0.0
    # capped at 0.5R
    assert f.friction_r(0.1) == 0.5
    # production profile friction is NOT truncated by the legacy 5-tick cap
    p = FrictionAssumptions.production_like()
    assert p.friction_r(2.0) == pytest.approx((20.0 + 2.2) * 0.01 / 2.0)


def test_execution_cost_usd_decomposition() -> None:
    a = EconomicAssumptions.production_like()
    cost = a.execution_cost_usd(
        direction="BUY", volume=0.5, risk_distance=2.0, entry=2000.0
    )
    assert cost == pytest.approx((22.2 * 0.01 / 2.0) * 0.5 * 100.0 * 2.0)
