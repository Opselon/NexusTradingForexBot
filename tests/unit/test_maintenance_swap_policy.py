"""Maintenance-window entry policy (ECON v1 phase 6) — behavioral tests.

Evidence basis (probe at HEAD, data/raw/XAUUSD_M1.csv, 100k bars):
  * the daily server-time market break is 23:00 -> 01:00 EVERY day
    (last bar 22:59, first bar 01:00, all months, no DST shift in
    May-Aug 2026 window)
  * spread inside 22:30-01:15: p90=48, p95=87 points vs p90=20 outside
  * rollover (swap posting) = server-midnight day boundary

These tests pin the canonical predicates in research/economics.py:
window derivation from server time (not hardcoded UTC), midnight crossing,
buffer semantics, rollover crossing boundary inclusivity, and the
economics-level integration (production profile requires swap completeness;
swap attaches per rollover crossing in the sized view).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.research.economics import (
    EconomicAssumptions,
    InstrumentEconomics,
    SwapProfile,
    in_maintenance_window,
    rollover_crossings,
)


# =============================================================================
# Window derivation
# =============================================================================


def test_window_is_server_time_not_hardcoded_utc() -> None:
    """A UTC-anchored caller with a GMT+2 server must get the SERVER window;
    the predicate shifts when the offset changes (derived, not hardcoded)."""
    # 21:10 UTC == 23:10 server(GMT+2): inside maintenance for a +2 broker
    assert in_maintenance_window(
        datetime(2026, 9, 1, 21, 10, tzinfo=UTC), server_utc_offset_hours=2.0
    )
    # the same instant is OUTSIDE for a server == UTC broker
    assert not in_maintenance_window(
        datetime(2026, 9, 1, 21, 10, tzinfo=UTC), server_utc_offset_hours=0.0
    )


def test_window_includes_evidence_buffer() -> None:
    """22:30 entry (buffer edge) is blocked; 22:00 (pre-buffer) is not."""
    assert in_maintenance_window(datetime(2026, 9, 1, 22, 30))
    assert not in_maintenance_window(datetime(2026, 9, 1, 22, 0))
    # post-midnight side: buffer extends 30m past 01:00 -> 01:29 inside,
    # 01:31 outside (00:00 opens a new server day for the window predicate)
    assert in_maintenance_window(datetime(2026, 9, 2, 1, 0))
    assert in_maintenance_window(datetime(2026, 9, 1, 1, 20))
    assert not in_maintenance_window(datetime(2026, 9, 1, 1, 45))


def test_normal_session_hours_clear() -> None:
    for hh, mm in ((2, 0), (8, 15), (12, 0), (17, 45), (21, 59)):
        assert not in_maintenance_window(datetime(2026, 9, 1, hh, mm)), (hh, mm)


# =============================================================================
# Rollover crossing / swap
# =============================================================================


def test_rollover_inclusive_boundary_and_multiples() -> None:
    e = datetime(2026, 9, 1, 22, 0)
    assert rollover_crossings(e, datetime(2026, 9, 2, 0, 0)) == 1  # at-instant counts
    assert rollover_crossings(e, datetime(2026, 9, 1, 23, 59)) == 0
    # five full server days held -> five crossings
    assert (
        rollover_crossings(
            datetime(2026, 9, 1, 6, 0), datetime(2026, 9, 6, 6, 0)
        )
        == 5
    )


def test_swap_long_debit_short_credit_with_volume_and_crossings() -> None:
    sp = SwapProfile(long_usd_per_lot_per_rollover=-5.5, short_usd_per_lot_per_rollover=1.25)
    e = datetime(2026, 9, 1, 22, 0)
    x = datetime(2026, 9, 2, 2, 0)
    assert sp.swap_usd("BUY_MARKET", 0.4, e, x) == pytest.approx(-2.2)
    assert sp.swap_usd("SELL_MARKET", 0.8, e, x) == pytest.approx(1.0)


def test_swap_zero_when_no_rollover_crossed() -> None:
    sp = SwapProfile(long_usd_per_lot_per_rollover=-5.0, short_usd_per_lot_per_rollover=2.0)
    e = datetime(2026, 9, 1, 9, 0)
    assert sp.swap_usd("BUY", 3.0, e, e + timedelta(hours=6)) == 0.0


def test_unset_swap_rates_raise_not_zero() -> None:
    sp = SwapProfile()
    with pytest.raises(ValueError, match="fail-closed"):
        sp.swap_usd("BUY", 1.0, datetime(2026, 9, 1), datetime(2026, 9, 2))


# =============================================================================
# Economics integration
# =============================================================================


def test_sized_view_attaches_swap_and_execution_costs() -> None:
    """End-to-end: sized economic view charges friction + swap per trade."""
    from nexus_scalp.research.metrics import compute_sized_economic_pnl
    from nexus_scalp.research.models import ResearchSample

    entry = datetime(2026, 9, 1, 22, 0, tzinfo=UTC)
    outcome = datetime(2026, 9, 2, 2, 0, tzinfo=UTC)
    s = ResearchSample(
        sample_id="s0",
        experience_id="e0",
        idempotency_key="k0",
        decision_timestamp=entry,
        outcome_timestamp=outcome,
        symbol="XAUUSD",
        strategy_id="s",
        strategy_version="1",
        regime="TRENDING_MOMENTUM",
        direction="BUY_MARKET",
        entry_price=2000.0,
        stop_loss=1998.0,
        take_profit=2004.0,
        realized_r=2.0,
        realized_pnl_usd=20.0,
        risk_distance=2.0,
    )
    swap = SwapProfile(long_usd_per_lot_per_rollover=-6.0, short_usd_per_lot_per_rollover=2.0)
    econ = EconomicAssumptions.production_like(swap=swap, starting_equity_usd=10_000.0)
    view = compute_sized_economic_pnl([s], econ)
    assert len(view.volumes) == 1
    volume = view.volumes[0]
    # swap charged once for the single rollover crossing
    assert view.swap_cost_usd[0] == pytest.approx(-6.0 * volume)
    # friction charged in R space, converted at sized risk
    expected_friction = econ.friction.friction_r(2.0) * volume * 100.0 * 2.0
    assert view.execution_cost_usd[0] == pytest.approx(expected_friction)
    # gross = recorded R * sized risk
    assert view.gross_pnl_usd[0] == pytest.approx(2.0 * volume * 100.0 * 2.0)
    # net ties: gross - exec + swap
    assert view.sized_pnl_usd[0] == pytest.approx(
        view.gross_pnl_usd[0] - view.execution_cost_usd[0] + view.swap_cost_usd[0]
    )


def test_frictionless_view_skips_swap_and_friction() -> None:
    from nexus_scalp.research.metrics import compute_sized_economic_pnl
    from nexus_scalp.research.models import ResearchSample

    s = ResearchSample(
        sample_id="s1",
        experience_id="e1",
        idempotency_key="k1",
        decision_timestamp=datetime(2026, 9, 1, 22, 0, tzinfo=UTC),
        outcome_timestamp=datetime(2026, 9, 2, 2, 0, tzinfo=UTC),
        symbol="XAUUSD",
        strategy_id="s",
        strategy_version="1",
        direction="BUY_MARKET",
        entry_price=2000.0,
        stop_loss=1998.0,
        take_profit=2004.0,
        realized_r=1.0,
        realized_pnl_usd=10.0,
        risk_distance=2.0,
    )
    econ = EconomicAssumptions.frictionless_research(instrument=InstrumentEconomics())
    view = compute_sized_economic_pnl([s], econ)
    assert view.execution_cost_usd[0] == 0.0
    assert view.swap_cost_usd[0] == 0.0  # frictionless profile: no swap rates
    assert view.sized_pnl_usd[0] == pytest.approx(view.gross_pnl_usd[0])
    assert not econ.is_promotable_economics()
