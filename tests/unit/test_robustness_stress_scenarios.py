"""ML-VAL-003 — Robustness stress testing: friction clamp, spread & slippage perturbation.

This battery hardens GATE8 (``gate_robustness`` / ``RobustnessEngine.evaluate``)
against the failure mode the task names: candidates whose economics collapse under
real execution friction must be REJECTED, and the degradation ceiling must never
be relaxed.

Stress grid (XAUUSD-shaped, deterministic, no torch):
  * spread spikes  +1/+2/+5/+10/+20/+50 ticks  (task: "+1 to +5 pips"; on
    price_tick=0.01 a 0.01-USD pip = 1 tick, so +1..+50 covers the full
    market-open-spike envelope the task's UNKNOWN asks about)
  * slippage       +1/+2 ticks                  (task spec)
  * latency        +50/+150 ms                  (deterministic model cannot price
    it — recorded as NOT_SIMULABLE, never as a fake 0-degradation PASS)
  * 1,000-trade benchmark record (task BENCHMARK_PLAN)

Ceiling contract discovered while writing this battery (pinned, not relaxed):
  * the per-trade friction model in ``compute_backtest`` clamps each trade's
    friction cost at ``min(friction_frac, 0.5)`` — 0.5R per trade — so the
    MAXIMUM degradation any single trade can contribute is 0.5R. The task text's
    "50% degradation" bound is therefore the THEORETICAL MAXIMUM of the friction
    model, and the binding constraint is the engine's own
    ``MAX_ACCEPTABLE_DEGRADATION_R = 0.25R`` (robustness.py:29). GATE8 rejects at
    0.25R, well inside the task's 0.50 outer bound — the ceiling is honoured,
    not relaxed. This is recorded because a future widening of
    MAX_ACCEPTABLE_DEGRADATION_R past 0.50 would break the task's NON_GOALS.

ACCEPTED-RISK findings pinned as tests (documented, not silently suppressed —
see ``test_accepted_risk_*``):
  * an EMPTY dataset evaluates to PASS (robustness.py has no floor on the sample
    count; compute_backtest returns a zero-trade report whose expectancy is 0.0
    and no scenario degrades it). GATE7 (``MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02``,
    oos.py:41) and GATE1 dataset-integrity reject such a candidate earlier in
    ``ModelLifecycleOrchestrator._evaluate_gates`` (orchestrator.py:352-380), so
    an empty model cannot reach production through the gate chain. GATE8 alone
    does not re-derive the floor.
  * a model with a NEGATIVE BASELINE expectancy can PASS GATE8 (its stress
    degradation is small because it was already losing). GATE8 measures
    degradation, not absolute profitability; GATE7 owns the absolute floor.

All tests are stdlib+repo only (no torch) and safe for the slim venv.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from nexus_scalp.model_lifecycle.gates import GateResult, gate_robustness
from nexus_scalp.research.models import (
    ExecutionAssumptions,
    ResearchDataset,
    ResearchSample,
)
from nexus_scalp.research.robustness import (
    MAX_ACCEPTABLE_DEGRADATION_R,
    STRESS_SCENARIOS,
    RobustnessEngine,
    RobustnessResult,
)

T0 = datetime(2026, 8, 1, tzinfo=UTC)

#: The task's outer bound: degradation must stay <= 50% (NON_GOALS: do not relax).
TASK_CEILING = 0.50
#: Engine-level degradation ceiling (robustness.py:29) — the binding constraint.
#: Pinned so a silent widening past the task bound is caught immediately.
ENGINE_CEILING = MAX_ACCEPTABLE_DEGRADATION_R

#: XAUUSD M1 stress grid (spread in ticks == pips on price_tick=0.01).
SPREAD_STRESS_TICKS = (1, 2, 5, 10, 20, 50)
SLIPPAGE_STRESS_TICKS = (1, 2)

#: A TIGHT stop (risk_distance 1.0 USD on price_tick 0.01) is what makes the
#: +50 market-open spike bite: 51 effective ticks * 0.01 = 0.51R friction ->
#: clipped at the 0.5R per-trade floor -> 0.48R measured degradation (FAIL).
#: Wide stops (2.0 USD) saturate the 0.5R floor on +50 ticks too, but from a
#: higher baseline the same 0.25R drop stays under the 0.25R ceiling (PASS).
FRAGILE_RISK_DISTANCE = 1.0
ROBUST_RISK_DISTANCE = 2.0


def _sample(i: int, r: float, risk: float = ROBUST_RISK_DISTANCE) -> ResearchSample:
    """One closed experience with a known realised-R outcome."""
    entry = 2000.0
    return ResearchSample(
        sample_id=f"s{i}",
        experience_id=f"e{i}",
        idempotency_key=f"k{i}",
        decision_timestamp=T0 + timedelta(minutes=i * 10),
        outcome_timestamp=T0 + timedelta(minutes=i * 10 + 7),
        symbol="XAUUSD",
        strategy_id="mlval003",
        strategy_version="1.0.0",
        regime="LONDON",
        entry_price=entry,
        stop_loss=entry - risk,
        take_profit=entry + risk * 2.0,
        direction="BUY",
        realized_r=r,
        realized_pnl_usd=r * 20.0,
        risk_distance=risk,
        holding_duration_sec=420.0,
        mae_r=0.2,
        mfe_r=0.8,
        exit_reason="TP" if r > 0 else "SL",
    )


def _dataset(
    n: int = 120,
    win_r: float = 0.9,
    loss_r: float = -0.9,
    win_frac: float = 0.7,
    risk: float = ROBUST_RISK_DISTANCE,
    dataset_id: str = "ds_val003",
) -> ResearchDataset:
    """Deterministic series with a known R-distribution: ``win_frac`` winners at
    ``+win_r`` and the remainder at ``-loss_r`` (task IMPLEMENTATION_PLAN step 2)."""
    samples = [
        _sample(i, win_r if (i % 10) < int(win_frac * 10) else loss_r, risk=risk) for i in range(n)
    ]
    return ResearchDataset(dataset_id=dataset_id, samples=samples)


def _healthy_baseline() -> ExecutionAssumptions:
    """A friction bundle whose cap leaves real stress headroom (not cap-pinned)."""
    return ExecutionAssumptions(
        spread_ticks=1.0,
        slippage_ticks=1.0,
        price_tick=0.01,
        max_slippage_ticks=1000.0,
    )


def _run(
    extra_spread: float = 0.0,
    extra_slip: float = 0.0,
    max_acceptable_deg_r: float = ENGINE_CEILING,
    win_frac: float = 0.7,
    win_r: float = 0.9,
    risk: float = ROBUST_RISK_DISTANCE,
    n: int = 120,
    baseline: ExecutionAssumptions | None = None,
    scenarios: list[tuple[str, dict[str, float]]] | None = None,
) -> tuple[RobustnessResult, GateResult]:
    """Run the full RobustnessEngine -> gate_robustness chain (GATE8)."""
    base = baseline if baseline is not None else _healthy_baseline()
    if scenarios is None:
        scenarios = [("stress", {"spread": extra_spread, "sl": extra_slip})]
    eng = RobustnessEngine(
        baseline=base,
        max_acceptable_deg_r=max_acceptable_deg_r,
        scenarios=scenarios,
    )
    res = eng.evaluate(
        _dataset(n=n, win_frac=win_frac, win_r=win_r, risk=risk), "mlval003", "1.0.0"
    )
    return res, gate_robustness(res)


# ---------------------------------------------------------------------------
# 1. Degradation is REAL and monotone in the stress magnitude
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra_ticks", SPREAD_STRESS_TICKS)
def test_spread_spike_degrades_expectancy_monotonically(extra_ticks: float) -> None:
    """Every spread spike from +1 to +50 ticks lowers expectancy, and harder
    stress is never better than lighter stress (task UNKNOWN: degradation curve
    under extreme market-open spread spikes)."""
    res, _ = _run(extra_spread=float(extra_ticks), max_acceptable_deg_r=1e9)
    assert res.status == "PASS", f"measurement harness must not fail: {res.reason}"
    assert res.stress_expectancies["stress"] < res.baseline_expectancy_r, (
        f"+{extra_ticks:g}-tick spread spike did not degrade expectancy "
        f"({res.stress_expectancies['stress']} vs baseline {res.baseline_expectancy_r})"
    )
    assert "stress" not in res.not_simulable_scenarios


def test_spread_degradation_curve_is_monotone_across_grid() -> None:
    """The degradation curve itself is non-decreasing in spread stress."""
    res, _ = _run(
        max_acceptable_deg_r=1e9,
        scenarios=[(f"sp{t}", {"spread": float(t)}) for t in SPREAD_STRESS_TICKS],
    )
    expectancies = [res.stress_expectancies[f"sp{t}"] for t in SPREAD_STRESS_TICKS]
    for prev, nxt in pairwise(expectancies):
        assert nxt <= prev + 1e-12, f"degradation curve is not monotone: {expectancies}"
    # The extreme market-open spike (+50) is the worst point on the curve.
    assert expectancies[-1] == min(expectancies)


@pytest.mark.parametrize("slip_ticks", SLIPPAGE_STRESS_TICKS)
def test_slippage_stress_degrades_expectancy(slip_ticks: float) -> None:
    """+1/+2 tick slippage (task IMPLEMENTATION_PLAN step 4) bites."""
    res, _ = _run(extra_slip=float(slip_ticks), max_acceptable_deg_r=1e9)
    assert res.stress_expectancies["stress"] < res.baseline_expectancy_r
    assert "stress" not in res.not_simulable_scenarios


def test_slippage_and_spread_stack_independently() -> None:
    """Spread and slippage both price as effective ticks, so a combined stress
    is at least as damaging as either alone."""
    res, _ = _run(
        max_acceptable_deg_r=1e9,
        scenarios=[
            ("spread_only", {"spread": 5.0}),
            ("slip_only", {"sl": 2.0}),
            ("combined", {"spread": 5.0, "sl": 2.0}),
        ],
    )
    spread_only = res.stress_expectancies["spread_only"]
    slip_only = res.stress_expectancies["slip_only"]
    combined = res.stress_expectancies["combined"]
    assert combined <= min(spread_only, slip_only) + 1e-12, (
        f"combined friction ({combined}) must be the worst case"
    )


def test_friction_r_floor_caps_single_trade_cost_at_half_r() -> None:
    """compute_backtest clamps per-trade friction at 0.5R, so a spread that
    EXCEEDS the take-profit distance cannot manufacture an unbounded loss —
    the outcome saturates (task INVESTIGATION_PLAN: "how RobustnessGate handles
    trades where spread exceeds take-profit distance"). Saturation is REAL:
    a +0.4R winner flips to a -0.1R loss, and 2,000x more spread changes
    nothing (the per-trade floor has already bound)."""
    kwargs: dict[str, object] = dict(
        max_acceptable_deg_r=1e9,
        win_frac=1.0,  # all winners: no losing trades to amplify
        risk=FRAGILE_RISK_DISTANCE,  # 51 ticks * 0.01 / 1.0 = 0.51R -> clips at 0.5
        n=20,
    )
    res_low, _ = _run(extra_spread=50.0, win_r=0.4, **kwargs)  # type: ignore[arg-type]
    res_high, _ = _run(extra_spread=100_000.0, win_r=0.4, **kwargs)  # type: ignore[arg-type]
    for worst in (res_low.stress_expectancies["stress"], res_high.stress_expectancies["stress"]):
        assert -1.0 <= worst < 0.0, (
            f"per-trade friction must be floored and must flip a +0.4R winner: {worst}"
        )
    # The 0.5R per-trade floor binds: 50 ticks and 100,000 ticks of spread
    # produce the IDENTICAL stressed expectancy. Saturation, not scaling.
    assert res_low.stress_expectancies["stress"] == res_high.stress_expectancies["stress"], (
        "per-trade friction floor (min(friction_frac, 0.5)) is not bounding the cost"
    )
    assert res_low.max_degradation > 0.0


# ---------------------------------------------------------------------------
# 2. GATE8 rejects fragile candidates (the degradation ceiling must bite)
# ---------------------------------------------------------------------------


def test_gate8_rejects_fragile_candidate() -> None:
    """A candidate that degrades past the ceiling is REJECTED, not passed with
    a warning (task ACCEPTANCE_CRITERIA 2). A tight-stop strategy under a +50
    market-open spread spike loses 0.48R of expectancy — a real, reachable
    rejection, not a contrived one."""
    res, gate = _run(extra_spread=50.0, risk=FRAGILE_RISK_DISTANCE)
    assert res.status == "FAIL", f"fragile candidate must FAIL: {res.reason}"
    assert res.max_degradation > ENGINE_CEILING, (
        f"degradation {res.max_degradation} must exceed the {ENGINE_CEILING}R ceiling"
    )
    assert gate.passed is False
    assert gate.gate == "GATE8_ROBUSTNESS"
    assert "robustness FAIL" in gate.reason


def test_gate8_rejects_when_task_ceiling_is_the_threshold() -> None:
    """The task's own 0.50 threshold also rejects the fragile candidate — the
    50% ceiling is load-bearing, not decorative. Note the degradation is 0.48R,
    which sits just under the 0.50 bound: the per-trade friction floor bounds
    the MAXIMUM measurable degradation at 0.5R, so the task's "50%" is the
    theoretical ceiling of the friction model. The engine's binding constraint
    is its stricter 0.25R default."""
    res, gate = _run(
        extra_spread=50.0, risk=FRAGILE_RISK_DISTANCE, max_acceptable_deg_r=TASK_CEILING
    )
    assert res.status == "FAIL"
    assert res.max_degradation > ENGINE_CEILING
    assert res.max_degradation <= TASK_CEILING, (
        "the friction floor caps measurable degradation at 0.5R; a value above "
        "the task bound means the floor was removed"
    )
    assert gate.passed is False


def test_gate8_passes_robust_candidate() -> None:
    """A candidate whose worst stress stays inside the ceiling passes
    (task IMPLEMENTATION_PLAN step 6)."""
    res, gate = _run(extra_spread=1.0, risk=ROBUST_RISK_DISTANCE)
    assert res.status == "PASS", f"robust candidate must PASS: {res.reason}"
    assert res.max_degradation <= ENGINE_CEILING
    assert gate.passed is True
    assert gate.gate == "GATE8_ROBUSTNESS"


def test_gate8_dict_payload_rejected_same_as_object() -> None:
    """The gate accepts the dict shape too (orchestrators may pass a serialized
    result) and must reject it identically."""
    res, _ = _run(extra_spread=50.0, risk=FRAGILE_RISK_DISTANCE)
    payload = res.model_dump()
    gate = gate_robustness(payload)
    assert gate.passed is False, "serialized FAIL must stay a FAIL"
    assert gate.gate == "GATE8_ROBUSTNESS"


def test_negative_expectancy_under_stress_fails_when_degradation_material() -> None:
    """The engine's second clause: stress that drives expectancy NEGATIVE with
    a material drop (> ceiling/2) fails even when the number is under a loose
    ceiling — the fragility signal, not just the arithmetic."""
    # Tight stop + a +50 spread spike: stress expectancy goes to -0.14R while
    # the degradation (0.48R) sits below the ceiling (0.8) but above ceiling/2
    # (0.4). The ceiling must be in (0.5, 1.0): the per-trade friction floor
    # bounds measurable degradation at 0.5R, so ceiling/2 < 0.5 is required for
    # this clause to be able to fire at all.
    res, gate = _run(
        extra_spread=50.0,
        risk=FRAGILE_RISK_DISTANCE,
        max_acceptable_deg_r=0.8,
    )
    worst = min(res.stress_expectancies.values())
    assert worst < 0.0, "fixture must produce a negative stressed expectancy"
    assert res.max_degradation <= 0.5, (
        "the per-trade friction floor bounds measurable degradation at 0.5R"
    )
    assert res.max_degradation > 0.8 / 2.0, "the drop must exceed ceiling/2"
    assert res.status == "FAIL", f"negative stressed expectancy must FAIL: {res.reason}"
    assert gate.passed is False


def test_ceiling_is_never_relaxed_past_task_bound() -> None:
    """ABORT_CONDITION hardening / NON_GOALS: the 50% degradation ceiling must
    never be relaxed. The engine ceiling sits inside the task bound; a silent
    widening would let fragile candidates through."""
    src = inspect.getsource(RobustnessEngine.evaluate)
    assert "self.max_acceptable_deg_r" in src, "the gate must consult the ceiling"
    assert MAX_ACCEPTABLE_DEGRADATION_R <= TASK_CEILING, (
        f"engine ceiling {MAX_ACCEPTABLE_DEGRADATION_R}R exceeds the task's "
        f"{TASK_CEILING} bound — the degradation ceiling has been relaxed"
    )
    assert ENGINE_CEILING == MAX_ACCEPTABLE_DEGRADATION_R


def test_gate8_never_swallows_a_fail() -> None:
    """ABORT_CONDITION: every reachable RobustnessResult transition maps to the
    right verdict. No FAIL shape converts to a PASS."""
    shapes = {
        # name: (extra_spread, risk, win_frac) -> expected engine status
        "robust": (1.0, ROBUST_RISK_DISTANCE, 0.7),
        "fragile": (50.0, FRAGILE_RISK_DISTANCE, 0.7),
        "negative_stress": (50.0, FRAGILE_RISK_DISTANCE, 0.5),
    }
    for name, (spread, risk, wf) in shapes.items():
        res, gate = _run(extra_spread=spread, risk=risk, win_frac=wf)
        assert gate.passed is (res.status == "PASS"), (
            f"{name}: gate={gate.passed} but engine status={res.status}"
        )
        assert gate.details["max_degradation"] == res.max_degradation


def test_cap_pinned_bundle_cannot_produce_a_fake_pass() -> None:
    """A 0-degradation result built from a cap-pinned bundle must not be
    presentable as healthy (BUG-299 fail-closed guard)."""
    pinned = ExecutionAssumptions(spread_ticks=15.0, slippage_ticks=5.0, max_slippage_ticks=5.0)
    res, gate = _run(baseline=pinned)
    assert res.status == "FAIL"
    assert "ROBUSTNESS_NOT_SIMULABLE" in res.reason
    assert gate.passed is False


def test_ceiling_lowering_rejects_marginal_candidates() -> None:
    """The gate responds to the threshold — a near-zero ceiling rejects what a
    loose one accepts, so the ceiling is load-bearing in both directions."""
    res_loose, gate_loose = _run(extra_spread=2.0, max_acceptable_deg_r=TASK_CEILING)
    res_tight, gate_tight = _run(extra_spread=2.0, max_acceptable_deg_r=1e-9)
    assert gate_loose.passed is True, "small stress under the task ceiling is robust"
    assert gate_tight.passed is False, "a near-zero ceiling must reject everything"
    assert res_loose.max_degradation == res_tight.max_degradation


# ---------------------------------------------------------------------------
# 3. Latency honesty: recorded, not faked
# ---------------------------------------------------------------------------


def test_latency_scenarios_are_census_not_fake_passes() -> None:
    """The deterministic backtest cannot price latency: latency scenarios land
    in the NOT_SIMULABLE census, never presented as a 0-degradation PASS."""
    eng = RobustnessEngine(baseline=_healthy_baseline())
    res = eng.evaluate(_dataset(), "mlval003", "1.0.0")
    assert set(res.not_simulable_scenarios) == {
        name for name, _ in STRESS_SCENARIOS if "latency" in name
    }, f"latency census wrong: {res.not_simulable_scenarios}"
    # The friction-bearing scenarios are all live and all degrading.
    live = [n for n, _ in STRESS_SCENARIOS if "latency" not in n]
    assert live, "a healthy bundle must have at least one live scenario"
    for name in live:
        assert name not in res.not_simulable_scenarios
        assert res.stress_expectancies[name] < res.baseline_expectancy_r


def test_latency_only_scenario_set_fails_closed() -> None:
    """If the ONLY stress is latency (nothing the backtest can price), the gate
    measured nothing and must fail closed."""
    res, gate = _run(scenarios=[("lat_only", {"latency": 150.0})])
    assert res.status == "FAIL"
    assert "ROBUSTNESS_NOT_SIMULABLE" in res.reason
    assert res.not_simulable_scenarios == ["lat_only"]
    assert gate.passed is False


# ---------------------------------------------------------------------------
# 4. Benchmark: 1,000 trade records across 4 friction stress levels
# (task BENCHMARK_PLAN)
# ---------------------------------------------------------------------------

BENCHMARK_TRADES = 1000
BENCHMARK_LEVELS = [
    ("baseline", 0.0, 0.0),
    ("mild", 1.0, 1.0),
    ("severe", 5.0, 2.0),
    ("extreme", 50.0, 2.0),
]


@pytest.mark.parametrize("level,spread,slip", BENCHMARK_LEVELS)
def test_benchmark_1000_trades_across_four_friction_levels(
    level: str, spread: float, slip: float
) -> None:
    """BENCHMARK_PLAN: robustness evaluation on 1,000 trade records at 4
    friction stress levels. Asserts the run completes deterministically and the
    degradation ordering matches the stress ordering."""
    res, _ = _run(
        extra_spread=spread,
        extra_slip=slip,
        n=BENCHMARK_TRADES,
        max_acceptable_deg_r=1e9,
        scenarios=[("lvl", {"spread": spread, "sl": slip})],
    )
    assert len(res.stress_expectancies) == 1
    if spread == 0.0 and slip == 0.0:
        # The "baseline" level perturbs nothing: identical expectancy, and it
        # is an ineffective scenario the census must flag (not a fake PASS).
        assert res.stress_expectancies["lvl"] == pytest.approx(res.baseline_expectancy_r)
        assert "lvl" in res.not_simulable_scenarios
    else:
        assert res.stress_expectancies["lvl"] < res.baseline_expectancy_r
        assert "lvl" not in res.not_simulable_scenarios


def test_benchmark_degradation_orders_by_stress_level() -> None:
    """The 4-level benchmark curve is ordered: more friction => less expectancy."""
    curve: dict[str, float] = {}
    for level, spread, slip in BENCHMARK_LEVELS:
        if spread == 0.0 and slip == 0.0:
            continue
        res, _ = _run(
            extra_spread=spread,
            extra_slip=slip,
            n=BENCHMARK_TRADES,
            max_acceptable_deg_r=1e9,
            scenarios=[("lvl", {"spread": spread, "sl": slip})],
        )
        curve[level] = res.stress_expectancies["lvl"]
    assert curve["mild"] > curve["severe"] > curve["extreme"], (
        f"benchmark curve out of order: {curve}"
    )


def test_benchmark_is_deterministic_on_repeated_runs() -> None:
    """The same 1,000-trade record set must yield byte-identical degradation on
    repeat evaluation (no RNG, no ordering nondeterminism)."""
    ds = _dataset(n=BENCHMARK_TRADES)
    eng = RobustnessEngine(baseline=_healthy_baseline(), max_acceptable_deg_r=1e9)
    first = eng.evaluate(ds, "mlval003", "1.0.0")
    second = eng.evaluate(ds, "mlval003", "1.0.0")
    assert first.baseline_expectancy_r == second.baseline_expectancy_r
    assert first.stress_expectancies == second.stress_expectancies
    assert first.max_degradation == second.max_degradation
    assert first.status == second.status


# ---------------------------------------------------------------------------
# 5. Abort-condition & fail-closed invariants (ABORT_CONDITIONS)
# ---------------------------------------------------------------------------


def test_abort_condition_negative_stress_expectancy_cannot_pass() -> None:
    """ABORT_CONDITION: if friction perturbation lets a net-negative-under-stress
    model PASS the gate, that is a SAFETY DEFECT. Here the stress itself drives
    the candidate negative (baseline +0.34R -> stressed -0.14R) and the drop is
    material -> the gate must reject via the negative-expectancy clause.

    Note the arithmetic: the negative clause requires degradation > ceiling/2.
    The per-trade friction floor bounds measurable degradation at 0.5R, so the
    clause can only fire for a ceiling ABOVE 0.5R — the clause is a genuine
    second line of defence, not the primary trigger (the 0.25R degradation
    ceiling is)."""
    res, gate = _run(extra_spread=50.0, risk=FRAGILE_RISK_DISTANCE, max_acceptable_deg_r=0.8)
    worst = min(res.stress_expectancies.values())
    assert worst < 0.0, "the stress must drive expectancy negative"
    assert res.max_degradation > 0.8 / 2.0, "the drop must be material"
    assert res.status == "FAIL", f"net-negative stressed model passed GATE8: {res.reason}"
    assert "negative under material stress" in res.reason
    assert gate.passed is False, "ABORT_CONDITION VIOLATED: losing model passed GATE8"


def test_engine_result_records_failure_reason() -> None:
    """A FAIL carries a human-actionable reason (not an empty string), so an
    operator can see WHY the candidate was rejected."""
    res, _ = _run(extra_spread=50.0, risk=FRAGILE_RISK_DISTANCE)
    assert res.status == "FAIL"
    assert res.reason and "degradation" in res.reason.lower(), (
        f"FAIL reason must name the degradation: {res.reason!r}"
    )


def test_perturbation_returns_new_bundle_not_mutation() -> None:
    """with_perturbation must not mutate the frozen baseline (contract: frozen
    domain models -> model_copy), so repeated evaluations are independent."""
    baseline = _healthy_baseline()
    before = baseline.model_dump()
    _ = baseline.with_perturbation(spread=10.0, sl=2.0, latency=50.0)
    assert baseline.model_dump() == before
    eng = RobustnessEngine(baseline=baseline, max_acceptable_deg_r=ENGINE_CEILING)
    r1 = eng.evaluate(_dataset(n=60), "mlval003", "1.0.0")
    r2 = eng.evaluate(_dataset(n=60), "mlval003", "1.0.0")
    assert r1.stress_expectancies == r2.stress_expectancies


# ---------------------------------------------------------------------------
# 6. ACCEPTED-RISK findings — pinned, not suppressed (see module docstring)
# ---------------------------------------------------------------------------


def test_accepted_risk_empty_dataset_passes_gate8_but_is_blocked_earlier() -> None:
    """FINDING (ACCEPTED-RISK): ``RobustnessEngine.evaluate`` on an EMPTY
    dataset returns status=PASS. There is no sample-count floor in
    robustness.py: compute_backtest returns a zero-trade report whose
    expectancy is 0.0 and no stress scenario can degrade it.

    NOT a production hole: ``ModelLifecycleOrchestrator._evaluate_gates`` runs
    GATE1 dataset-integrity and GATE7 (``MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02``,
    research/oos.py:41) BEFORE GATE8, so an empty candidate cannot reach
    promotion. GATE8 measures degradation only; it does not re-derive the
    absolute-profitability floor.

    Pinned so the accepted-risk shape is visible: if a future change adds a
    sample-count floor to the robustness engine, this test must be updated
    (not silently deleted) and the gate chain re-audited."""
    res, gate = _run(n=0, scenarios=[("stress", {"spread": 50.0})])
    assert res.baseline_expectancy_r == 0.0
    assert res.status == "PASS", (
        "empty-dataset behaviour changed — re-audit the GATE1/GATE7/GATE8 chain"
    )
    assert gate.passed is True
    # Nothing was measured, and the engine says so in the zero-valued
    # expectancy instead of inventing a degradation number.
    assert res.stress_expectancies["stress"] == 0.0
    assert res.max_degradation == 0.0
    assert res.reason == "Robust to modelled stress"


def test_accepted_risk_negative_baseline_passes_gate8_profitability_is_gate7() -> None:
    """FINDING (ACCEPTED-RISK): a model with a NEGATIVE BASELINE expectancy can
    PASS GATE8 — its degradation under stress is small because it was already
    losing, so the degradation gate has nothing to measure.

    GATE8 is a RELATIVE gate by design (spec 16/35): it measures collapse under
    stress, not absolute profitability. Absolute profitability is owned by
    GATE7 (gate_oos, min_oos_expectancy_r floor). Wiring an absolute floor into
    GATE8 would duplicate GATE7 and violate the single-owner rule.

    Pinned so a negative-baseline candidate cannot quietly pass as 'robust and
    profitable': this test fails loudly if GATE8 ever silently starts rejecting
    on absolute grounds (which would break the gate division of labour)."""
    # 50% winners at +0.9/-0.9 => baseline -0.04R (a losing strategy).
    res, gate = _run(win_frac=0.5, extra_spread=1.0, risk=ROBUST_RISK_DISTANCE)
    assert res.baseline_expectancy_r < 0.0, "fixture must be a losing model"
    assert res.status == "PASS", "GATE8 is relative; absolute loss is GATE7's job"
    assert gate.passed is True
    # The same model IS rejected by the OOS absolute-profitability gate.
    from nexus_scalp.model_lifecycle.gates import gate_oos

    oos_gate = gate_oos({"status": "PASS", "oos_expectancy_r": res.baseline_expectancy_r})
    assert oos_gate.passed is False, (
        "GATE7 must reject a negative-expectancy model — if it does not, the "
        "absolute-profitability floor has moved and this accepted-risk needs review"
    )
