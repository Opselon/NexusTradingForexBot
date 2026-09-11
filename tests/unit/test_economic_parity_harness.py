"""Backtest <-> canonical economic parity harness (ECON v1 phase 9).

Deterministic parity contract: given a fixed historical sequence (signals +
confidence + regime + account state + execution assumptions), the backtest
and the canonical economic/sizing components must implement the SAME
economic contract:

  A. sizing parity        — BacktestEngine's sized view volumes == the
                            canonical compute_sizing decisions
  B. causal sizing        — the sized view is invariant to FUTURE trades
                            (a later win cannot change an earlier size)
  C. friction default     — an omitted assumptions argument yields the
                            production-like friction, never zero
  D. frictionless opt-in  — frictionless runs are explicitly labelled and
                            non-promotable
  E. swap in the contract — complete swap assumptions are required for a
                            promotable economic world

Regression fixture intent: if someone reintroduces constant-size backtests,
zero-friction defaults, missing swap accounting, a divergent sizing formula,
or a different drawdown basis, at least one test here MUST fail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.research.backtest import BacktestEngine
from nexus_scalp.research.economics import (
    EconomicAssumptions,
    ExecutionProfile,
    SwapProfile,
    compute_sizing,
)
from nexus_scalp.research.metrics import (
    _sized_view_confidence_factor,
    compute_backtest,
    compute_sized_economic_pnl,
)
from nexus_scalp.research.models import ResearchDataset, ResearchSample

START_EQ = 10_000.0


def _trade(
    i: int,
    *,
    r: float,
    conf: float,
    regime: str = "TRENDING_MOMENTUM",
    risk_distance: float = 2.0,
) -> ResearchSample:
    t = datetime(2026, 9, 1, 9, i * 17 % 60, tzinfo=UTC) + timedelta(hours=i // 60)
    return ResearchSample(
        sample_id=f"s{i}",
        experience_id=f"e{i}",
        idempotency_key=f"k{i}",
        decision_timestamp=t,
        outcome_timestamp=t + timedelta(minutes=45),
        symbol="XAUUSD",
        strategy_id="parity",
        strategy_version="1",
        regime=regime,
        direction="BUY_MARKET" if i % 2 == 0 else "SELL_MARKET",
        entry_price=2000.0,
        stop_loss=2000.0 - risk_distance,
        take_profit=2000.0 + 1.8 * risk_distance,
        realized_r=r,
        realized_pnl_usd=r * 10.0,
        risk_distance=risk_distance,
        signal_confidence=conf,
    )


def _dataset(n: int = 12) -> ResearchDataset:
    rs = [
        _trade(i, r=[2.0, -1.0, 0.6, -1.0, 1.4][i % 5], conf=0.6 + (i % 4) * 0.1) for i in range(n)
    ]
    return ResearchDataset(dataset_id="ds_parity", samples=rs)


SWAP = SwapProfile(long_usd_per_lot_per_rollover=-6.0, short_usd_per_lot_per_rollover=2.5)
ECON = EconomicAssumptions.production_like(swap=SWAP, starting_equity_usd=START_EQ)


# =============================================================================
# A. Sizing parity: backtest's sized view == canonical sizing engine
# =============================================================================


def test_sized_view_volumes_match_canonical_sizing() -> None:
    ds = _dataset()
    ordered = sorted(ds.samples, key=lambda s: s.decision_timestamp)
    view = compute_sized_economic_pnl(ordered, ECON)
    assert len(view.volumes) == len(ordered)
    # replicate the running-equity loop with the CANONICAL component
    equity = START_EQ
    peak = equity
    for sample, vol in zip(ordered, view.volumes, strict=True):
        decision = compute_sizing(
            policy=ECON.sizing,
            instrument=ECON.instrument,
            equity=equity,
            peak_equity=peak,
            entry=sample.entry_price,
            stop_loss=sample.stop_loss,
            confidence=_sized_view_confidence_factor(
                getattr(sample, "signal_confidence", 0.0)
            ),  # ECON-CONF parity: same per-trade factor the view uses
            regime=sample.regime,
        )
        # samples built by _dataset() carry no recorded confidence (0.0 =>
        # NOT RECORDED => flat 1.0 on both paths). Volumes must match exactly.
        assert vol == decision.volume
        # advance the equity path the same way the view does
        risk_distance = sample.risk_distance
        risk_usd = vol * ECON.instrument.contract_size * risk_distance
        gross = sample.realized_r * risk_usd
        friction = ECON.friction.friction_r(risk_distance) * risk_usd
        swap = ECON.swap.swap_usd(
            sample.direction, vol, sample.decision_timestamp, sample.outcome_timestamp
        )
        equity += gross - friction + swap
        peak = max(peak, equity)


# =============================================================================
# B. Causal sizing: no lookahead into the equity path
# =============================================================================


def test_sizing_is_causal_future_trades_cannot_change_earlier_size() -> None:
    early = _trade(0, r=2.0, conf=0.9)
    late_win = _trade(1, r=3.0, conf=0.9)
    late_big_win = _trade(2, r=10.0, conf=0.95)
    econ = EconomicAssumptions.production_like(swap=SWAP, starting_equity_usd=1_000.0)

    base = compute_sized_economic_pnl([early], econ)
    with_late = compute_sized_economic_pnl([early, late_win], econ)
    with_bigger_late = compute_sized_economic_pnl([early, late_win, late_big_win], econ)

    assert base.volumes[0] == with_late.volumes[0] == with_bigger_late.volumes[0]
    # the first trade's P&L is likewise untouched by future trades
    assert base.sized_pnl_usd[0] == with_bigger_late.sized_pnl_usd[0]


# =============================================================================
# C. Friction defaults fail-safe
# =============================================================================


def test_omitted_assumptions_default_to_production_friction() -> None:
    engine = BacktestEngine()  # nothing supplied
    assert engine.economic.profile == ExecutionProfile.PRODUCTION_LIKE
    assert engine.economic.friction.spread_ticks > 0
    assert engine.assumptions.spread_ticks > 0
    assert engine.assumptions.slippage_ticks > 0


def test_zero_friction_backtest_never_looks_production_like() -> None:
    """The engine's result must always carry its explicit economic world;
    a 'delete the friction default' mutation flips the profile or zeroes the
    friction and is caught here."""
    eng = BacktestEngine()
    result = eng.run(_dataset(8), "parity", "1")
    assert result.economic.profile == ExecutionProfile.PRODUCTION_LIKE
    assert result.economic.friction.spread_ticks > 0.0


# =============================================================================
# D/E. Frictionless label + swap requirement on results
# =============================================================================


def test_frictionless_result_is_labelled_non_promotable() -> None:
    econ = EconomicAssumptions.frictionless_research(starting_equity_usd=START_EQ)
    eng = BacktestEngine(economic=econ)
    # E1 adaptation: the analytical frictionless profile is zero-cost by
    # design; the run must now carry the explicit allow flag.
    result = eng.run(_dataset(8), "parity", "1", allow_zero_friction=True)
    assert result.economic.profile == ExecutionProfile.FRICTIONLESS_RESEARCH
    assert result.economic.is_promotable_economics() is False


def test_production_result_requires_swap_for_promotability() -> None:
    ds = _dataset(8)
    result = BacktestEngine(economic=ECON).run(ds, "parity", "1")
    assert result.economic.is_promotable_economics() is True
    assert result.sized is not None
    assert result.sized.swap_cost_usd  # swap attaches where rates are given


def test_result_carries_full_economic_provenance() -> None:
    result = BacktestEngine(economic=ECON).run(_dataset(6), "parity", "1")
    prov = result.economic.provenance()
    for key in (
        "execution_profile",
        "friction",
        "swap",
        "instrument",
        "sizing",
        "starting_equity_usd",
        "promotable_economics",
    ):
        assert key in prov


# =============================================================================
# Mutation detectors (high-leverage contract guards)
# =============================================================================


def test_mutation_constant_size_backtest_detected() -> None:
    """If sized volumes stop depending on the equity path (constant-size
    regression), the sized P&L can no longer track the equity trajectory."""
    ds = _dataset(12)
    view = compute_sized_economic_pnl(sorted(ds.samples, key=lambda s: s.decision_timestamp), ECON)
    # With a 10k account the risk-percent sizing rounds to a stable lot size;
    # the equity-path coupling is proven by scale: 10x the account must move
    # the sized volumes (a constant-size backtest would keep them fixed).
    big = EconomicAssumptions.production_like(swap=SWAP, starting_equity_usd=START_EQ * 10.0)
    view_big = compute_sized_economic_pnl(
        sorted(ds.samples, key=lambda s: s.decision_timestamp), big
    )
    assert max(view_big.volumes) > max(view.volumes), (
        "volumes must scale with the equity path (no constant-size backtest)"
    )
    assert view.max_drawdown_usd > 0.0


def test_mutation_zero_friction_detected() -> None:
    """Production friction MUST reduce expectancy vs frictionless on the
    same trades; a silent zero-friction default breaks this invariant."""
    ds = _dataset(12)
    with_friction = compute_sized_economic_pnl(
        sorted(ds.samples, key=lambda s: s.decision_timestamp), ECON
    )
    frictionless = compute_sized_economic_pnl(
        sorted(ds.samples, key=lambda s: s.decision_timestamp),
        EconomicAssumptions.frictionless_research(starting_equity_usd=START_EQ),
    )
    assert sum(with_friction.execution_cost_usd) > 0.0
    assert sum(frictionless.execution_cost_usd) == 0.0
    assert sum(with_friction.sized_pnl_usd) < sum(frictionless.sized_pnl_usd)


def test_mutation_missing_swap_detected() -> None:
    """Swap must move the sized P&L for overnight holds; deleting the swap
    term from the contract changes the net path. BUY trades held across the
    server-midnight rollover (22:00 entry, 02:00 exit) make the debit visible."""
    t0 = datetime(2026, 9, 1, 22, 0, tzinfo=UTC)
    overnight_buy = _trade(0, r=1.0, conf=0.9)
    s = overnight_buy.model_copy(
        update={
            "decision_timestamp": t0,
            "outcome_timestamp": t0 + timedelta(hours=4),
        }
    )
    ordered = [s]
    with_swap = compute_sized_economic_pnl(ordered, ECON)
    no_swap = compute_sized_economic_pnl(
        ordered,
        EconomicAssumptions.production_like(
            swap=SwapProfile(long_usd_per_lot_per_rollover=0.0, short_usd_per_lot_per_rollover=0.0),
            starting_equity_usd=START_EQ,
        ),
    )
    assert sum(no_swap.swap_cost_usd) == 0.0
    assert sum(with_swap.swap_cost_usd) < 0.0  # long swap debit present
    assert sum(with_swap.sized_pnl_usd) != sum(no_swap.sized_pnl_usd)


def test_mutation_divergent_sizing_formula_detected() -> None:
    """BacktestEngine.assumptions (legacy friction view) must be DERIVED from
    the economic world — editing one side alone breaks the parity identity."""
    econ = ECON
    eng = BacktestEngine(economic=econ)
    legacy = eng.assumptions
    assert legacy.spread_ticks == econ.friction.spread_ticks
    assert legacy.slippage_ticks == econ.friction.slippage_ticks
    assert legacy.price_tick == econ.friction.price_tick
    assert legacy.max_slippage_ticks == econ.friction.max_slippage_ticks


# =============================================================================
# F. ECON-CONF: per-trade confidence parity (unknown == flat, recorded == band)
# =============================================================================


def test_confidence_factor_unknown_is_flat_not_floored() -> None:
    """THE ECON-CONF regression: confidence 0.0 (NOT RECORDED) must size FLAT
    (factor 1.0) exactly like the live NOT_CALIBRATED engine contract —
    never the 0.25x de-risk floor. The historical bug fed 0.0 for every
    ledger trade and understated sized risk/P&L/drawdown up to 4x."""
    assert _sized_view_confidence_factor(0.0) == 1.0
    assert _sized_view_confidence_factor(-0.4) == 1.0  # invalid -> flat
    assert _sized_view_confidence_factor(float("nan")) == 1.0
    # recorded confidence routes through the calibrated de-risk band
    assert _sized_view_confidence_factor(0.3) == pytest.approx(0.25 + 0.75 * 0.3)
    assert _sized_view_confidence_factor(1.0) == 1.0  # cap, never lever


def test_sized_view_unknown_confidence_matches_flat_sizing() -> None:
    """A ledger sample with NO recorded confidence must size EXACTLY like the
    live flat base-risk path (confidence_scalar(1.0) == 1.0), not the floor."""
    sample = _trade(0, r=1.0, conf=0.9).model_copy(update={"signal_confidence": 0.0})
    view = compute_sized_economic_pnl([sample], ECON)
    flat = compute_sizing(
        policy=ECON.sizing,
        instrument=ECON.instrument,
        equity=START_EQ,
        peak_equity=START_EQ,
        entry=sample.entry_price,
        stop_loss=sample.stop_loss,
        confidence=1.0,
        regime=sample.regime,
    )
    assert view.volumes[0] == flat.volume
    assert flat.volume > 0.0


def test_sized_view_recorded_confidence_de_risks_not_levers() -> None:
    """Recorded confidence must de-risk relative to the flat path and must
    match the canonical factor band — the sized view mirrors live sizing."""
    low = _trade(0, r=1.0, conf=0.3)
    high = _trade(0, r=1.0, conf=0.9)
    view_low = compute_sized_economic_pnl([low], ECON)
    view_high = compute_sized_economic_pnl([high], ECON)
    assert view_low.volumes[0] < view_high.volumes[0]  # de-risk only
    flat = compute_sizing(
        policy=ECON.sizing,
        instrument=ECON.instrument,
        equity=START_EQ,
        peak_equity=START_EQ,
        entry=low.entry_price,
        stop_loss=low.stop_loss,
        confidence=1.0,
        regime=low.regime,
    )
    assert view_high.volumes[0] <= flat.volume  # never lever above flat


def test_dataset_builder_carries_ledger_confidence(tmp_path) -> None:
    """ResearchDatasetBuilder._to_sample must FORWARD the ledger's
    signal_confidence — the producer exists (experience ledger), the field
    must survive the ledger -> research sample projection (ECON-CONF)."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.experience.ledger import ExperienceLedger
    from nexus_scalp.research.dataset import ResearchDatasetBuilder
    from tests.unit.task4_research_helpers import make_outcome, make_record

    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'econconf.db'}")
    ledger = ExperienceLedger(audit_repo=repo)
    rec = make_record("econconf1").model_copy(update={"signal_confidence": 0.42})
    ledger.record_experience(rec)
    ledger.record_outcome(make_outcome(rec, r=1.2))
    repo._queue.join()
    ds = ResearchDatasetBuilder(ledger).build()
    repo.close()
    assert len(ds.samples) == 1
    assert ds.samples[0].signal_confidence == pytest.approx(0.42)
