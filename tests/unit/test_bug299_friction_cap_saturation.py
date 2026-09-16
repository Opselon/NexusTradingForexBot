"""BUG-299 — the canonical-costs research path silently traded at 25% of its
calibrated friction, and the robustness gate that depends on it measured NOTHING.

Root cause (P0 role-4 lens, master wave report §13 + P0-3 handoff item 3):
``configuration.execution_costs.to_research_assumptions`` converted the canonical
evidence (spread.mean 0.147 USD + slippage.p95 0.05 USD, price_tick 0.01) into
spread=15 + slippage=5 TICKS but left ``ExecutionAssumptions.max_slippage_ticks``
at the model default 5.0 ("guard against runaway"). ``research.metrics.compute_backtest``
models effective friction as ``min(spread + slippage, max_slippage_ticks)`` — so
EVERY default-costs research engine (ResearchPipeline backtest / walk-forward /
OOS / robustness) paid 5 ticks instead of the calibrated 20.

Consequences this battery pins:
  A) The bridge never truncates calibrated friction (cap >= spread+slip+stress
     headroom); the effective friction equals the evidence sum.
  B) A cap-pinned bundle (the pre-fix canonical shape) can NEVER produce a PASS:
     the robustness engine records every scenario as not_simulable and fails
     closed with ROBUSTNESS_NOT_SIMULABLE (P0-3 AC-3: "silent no-op forbidden").
     RED-BEFORE on 2ae009c0: the SAME bundle returned status=PASS with
     max_degradation=0.0 and six byte-identical stress expectancies.
  C) A healthy bundle measures a REAL spread-stress degradation (strictly lower
     stressed expectancy) and records only the two latency scenarios (which the
     deterministic backtest genuinely cannot price) in the census.
  D) The DEFAULT engine (canonical costs) is not a silent no-op anymore.
  E) An empty scenario set fails closed (no scenarios = nothing measured).

All tests stdlib+repo only (no torch), safe for the slim venv and the gate.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.configuration import execution_costs as ec
from nexus_scalp.research.models import (
    ExecutionAssumptions,
    ResearchDataset,
    ResearchSample,
    default_research_assumptions,
)
from nexus_scalp.research.robustness import STRESS_SCENARIOS, RobustnessEngine

T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _sample(i: int, r: float, risk: float = 2.0) -> ResearchSample:
    return ResearchSample(
        sample_id=f"s{i}",
        experience_id=f"e{i}",
        idempotency_key=f"k{i}",
        decision_timestamp=T0 + timedelta(minutes=i * 10),
        outcome_timestamp=T0 + timedelta(minutes=i * 10 + 7),
        symbol="XAUUSD",
        strategy_id="bug298",
        strategy_version="1.0.0",
        regime="LONDON",
        entry_price=2000.0,
        stop_loss=2000.0 - risk,
        direction="BUY",
        realized_r=r,
        realized_pnl_usd=r * 20.0,
        risk_distance=risk,
        holding_duration_sec=420.0,
        mae_r=0.2,
        mfe_r=0.8,
        exit_reason="TP" if r > 0 else "SL",
    )


def _dataset(n: int = 120, r_win: float = 0.9, frac: float = 0.7) -> ResearchDataset:
    samples = [_sample(i, r_win if (i % 10) < int(frac * 10) else -0.9) for i in range(n)]
    return ResearchDataset(dataset_id="ds298", samples=samples)


def _effective(a: ExecutionAssumptions) -> float:
    return min(a.spread_ticks + a.slippage_ticks, a.max_slippage_ticks)


# ---------------------------------------------------------------------------
# A. The canonical bridge must never truncate its own evidence
# ---------------------------------------------------------------------------


def test_bridge_cap_covers_calibrated_friction_plus_stress_headroom() -> None:
    costs = ec.get_execution_assumptions()
    ra, _cal = ec.to_research_assumptions(costs)
    base_ticks = ra.spread_ticks + ra.slippage_ticks
    assert ra.max_slippage_ticks >= base_ticks + ec.STRESS_HEADROOM_TICKS, (
        f"cap {ra.max_slippage_ticks} truncates calibrated friction {base_ticks}: "
        "every stress scenario lands on the same capped value and the gate is dead"
    )
    # The canonical evidence is ~20 ticks (0.147+0.05 USD / 0.01): a cap at the
    # old 5.0 model default is EXACTLY the BUG-299 shape.
    assert base_ticks > 5.0, "fixture assumption: canonical friction exceeds the legacy default cap"


def test_effective_friction_equals_evidence_not_cap() -> None:
    costs = ec.get_execution_assumptions()
    ra, _cal = ec.to_research_assumptions(costs)
    assert _effective(ra) == ra.spread_ticks + ra.slippage_ticks, (
        "min(spread+slip, cap) must equal the measured friction — pre-fix it was "
        "pinned at 5.0 while the evidence said 20.0"
    )


def test_low_friction_bundle_keeps_legacy_floor() -> None:
    """A zero/low-cost analytical conversion keeps the 5.0 runaway guard (never
    smaller than the legacy default), so the E1 zero-friction contract's shape
    for frictionless bundles is unchanged."""
    costs = ec.get_execution_assumptions()
    tiny = costs.model_copy(
        update={
            "spread": costs.spread.model_copy(update={"mean": 0.01}),
            "slippage": costs.slippage.model_copy(update={"paper_measured_p95": 0.0}),
        }
    )
    ra, _ = ec.to_research_assumptions(tiny)
    assert ra.max_slippage_ticks == 5.0
    # base friction 1 tick; +2 stress stays below the floor cap -> live.
    stressed = ra.with_perturbation(spread=2.0)
    assert _effective(stressed) == stressed.spread_ticks + stressed.slippage_ticks


# ---------------------------------------------------------------------------
# B. Cap-pinned friction (the pre-fix canonical shape) can never report PASS
# ---------------------------------------------------------------------------


def test_cap_pinned_bundle_fails_closed_not_silent_pass() -> None:
    """RED-BEFORE 2ae009c0: this exact bundle (base 20 ticks, cap 5 — what
    default_research_assumptions() produced pre-fix) returned
    status=PASS / max_degradation=0.0 / six identical stress expectancies."""
    pinned = ExecutionAssumptions(spread_ticks=15.0, slippage_ticks=5.0, max_slippage_ticks=5.0)
    res = RobustnessEngine(baseline=pinned).evaluate(_dataset(), "s298", "v1")
    assert res.status == "FAIL"
    assert "ROBUSTNESS_NOT_SIMULABLE" in res.reason
    assert res.max_degradation == 0.0
    assert set(res.not_simulable_scenarios) == {name for name, _ in STRESS_SCENARIOS}
    # All "stress" expectancies were byte-identical to baseline — the census
    # is what makes that visible instead of presenting a PASS.
    assert all(v == res.baseline_expectancy_r for v in res.stress_expectancies.values())


def test_empty_scenario_set_fails_closed() -> None:
    healthy = ExecutionAssumptions(spread_ticks=15.0, slippage_ticks=5.0, max_slippage_ticks=24.0)
    res = RobustnessEngine(baseline=healthy, scenarios=[]).evaluate(_dataset(), "s298", "v1")
    assert res.status == "FAIL"
    assert "ROBUSTNESS_NOT_SIMULABLE" in res.reason


# ---------------------------------------------------------------------------
# C. A healthy bundle measures real degradation; latency stays honest census
# ---------------------------------------------------------------------------


def test_healthy_bundle_measures_real_spread_degradation() -> None:
    healthy = ExecutionAssumptions(spread_ticks=15.0, slippage_ticks=5.0, max_slippage_ticks=24.0)
    eng = RobustnessEngine(baseline=healthy)
    res = eng.evaluate(_dataset(), "s298", "v1")
    # +1 and +2 tick spread stress move P&L strictly downward and monotonically.
    assert res.stress_expectancies["spread_plus_1"] < res.baseline_expectancy_r
    assert res.stress_expectancies["spread_plus_2"] < res.stress_expectancies["spread_plus_1"]
    assert res.stress_expectancies["slippage_plus_1"] < res.baseline_expectancy_r
    assert res.max_degradation > 0.0
    # Latency is genuinely not priced by the deterministic backtest: recorded,
    # never presented as a measured 0-degradation result.
    assert set(res.not_simulable_scenarios) == {"latency_plus_50ms", "latency_plus_150ms"}


# ---------------------------------------------------------------------------
# D. The DEFAULT (canonical) engine is no longer a silent no-op
# ---------------------------------------------------------------------------


def test_default_canonical_engine_measures_something() -> None:
    a, prov = default_research_assumptions()
    if prov != "CANONICAL_COSTS":
        pytest.skip("canonical cost artifact unavailable on this host (FALLBACK_ZERO)")
    res = RobustnessEngine().evaluate(_dataset(), "s298", "v1")
    live = [n for n in res.stress_expectancies if n not in res.not_simulable_scenarios]
    assert live, "pre-BUG-299 canonical runs had ZERO live scenarios (every one cap-pinned)"
    assert any(res.stress_expectancies[n] < res.baseline_expectancy_r for n in live)
    assert res.max_degradation > 0.0


# ---------------------------------------------------------------------------
# E. Contract pins (MIRROR-TEST-CLASS lesson: pin the SOURCE, not a copy)
# ---------------------------------------------------------------------------


def test_bridge_sets_cap_from_evidence_source_pin() -> None:
    src = inspect.getsource(ec.to_research_assumptions)
    assert "max_slippage_ticks" in src, (
        "the canonical bridge must derive the friction cap — leaving it unset "
        "re-introduces the 5.0 default saturation (BUG-299)"
    )


def test_ineffectiveness_uses_backtest_semantics_single_owner() -> None:
    """The engine's effectiveness check must share compute_backtest's exact
    min(spread+slip, cap) formula — one owner (mirrors the BUG-292 percentile
    lesson). Source-pin both sides."""
    from nexus_scalp.research import metrics as m
    from nexus_scalp.research import robustness as rb

    metrics_src = inspect.getsource(m.compute_backtest)
    assert "min(friction_points, assumptions.max_slippage_ticks)" in metrics_src
    helper_src = inspect.getsource(rb._effective_friction_ticks)
    assert "max_slippage_ticks" in helper_src
    # Behavioural parity: any ExecutionAssumptions-shaped object resolves the
    # same effective friction through both owners.
    for a in (
        ExecutionAssumptions(spread_ticks=15.0, slippage_ticks=5.0, max_slippage_ticks=24.0),
        ExecutionAssumptions(spread_ticks=15.0, slippage_ticks=5.0, max_slippage_ticks=5.0),
        ExecutionAssumptions(spread_ticks=1.0, slippage_ticks=1.0),
    ):
        assert rb._effective_friction_ticks(a) == min(
            a.spread_ticks + a.slippage_ticks, a.max_slippage_ticks
        )
