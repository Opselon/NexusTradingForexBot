"""
Robustness Engine
=================
PHASE 09B (spec 16 / 35 / 38).

A strategy must be tested under realistic perturbations. Robustness is NOT
"still profitable"; it is measured as degradation under stress. A strategy that
collapses (+0.44R -> -0.12R) under +1 tick slip is fragile and fails.

Stress dimensions: spread, slippage, latency, entry-price perturbation
(modeled via friction), and small timing shifts (embargo sensitivity).
"""

from __future__ import annotations

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.metrics import compute_backtest
from nexus_scalp.research.models import (
    ExecutionAssumptions,
    ResearchDataset,
    RobustnessResult,
    default_research_assumptions,
    ensure_not_zero_friction,
)

logger = get_logger("nexus_scalp.research.robustness")

#: Max absolute R degradation from baseline before the strategy is FRAGILE.
MAX_ACCEPTABLE_DEGRADATION_R: float = 0.25


def _effective_friction_ticks(a: object) -> float:
    """Effective per-trade friction the deterministic backtest actually pays:
    ``min(spread + slippage, max_slippage_ticks)`` (research.metrics semantics).
    Duck-typed so any ExecutionAssumptions-shaped bundle works."""
    return float(
        min(
            getattr(a, "spread_ticks", 0.0) + getattr(a, "slippage_ticks", 0.0),
            getattr(a, "max_slippage_ticks", 0.0),
        )
    )


#: Stress scenarios applied on top of the baseline assumptions.
STRESS_SCENARIOS: list[tuple[str, dict[str, float]]] = [
    ("spread_plus_1", {"spread": 1.0}),
    ("spread_plus_2", {"spread": 2.0}),
    ("slippage_plus_1", {"sl": 1.0}),
    ("slippage_plus_2", {"sl": 2.0}),
    ("latency_plus_50ms", {"latency": 50.0}),
    ("latency_plus_150ms", {"latency": 150.0}),
]


class RobustnessEngine:
    """Applies stress scenarios and measures degradation."""

    def __init__(
        self,
        baseline: ExecutionAssumptions | None = None,
        max_acceptable_deg_r: float = MAX_ACCEPTABLE_DEGRADATION_R,
        scenarios: list[tuple[str, dict[str, float]]] | None = None,
    ) -> None:
        # E1/E2: canonical-cost default (see walkforward for the contract).
        if baseline is not None:
            self.baseline = baseline
            self.assumptions_provenance = "EXPLICIT"
        else:
            self.baseline, self.assumptions_provenance = default_research_assumptions()
        self.max_acceptable_deg_r = float(max_acceptable_deg_r)
        self.scenarios = scenarios if scenarios is not None else STRESS_SCENARIOS

    def evaluate(
        self,
        dataset: ResearchDataset,
        strategy_id: str,
        strategy_version: str,
        allow_zero_friction: bool = False,
    ) -> RobustnessResult:
        # E1 loud guard: refuse a zero-cost run unless explicitly allowed.
        ensure_not_zero_friction(
            self.baseline,
            allow=allow_zero_friction,
            context="RobustnessEngine.evaluate",
        )
        base_bt = compute_backtest(
            dataset.samples,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset.dataset_id,
            assumptions=self.baseline,
        )
        baseline_exp = base_bt.expectancy_r
        stress_expectancies: dict[str, float] = {}
        # BUG-299 (P0-3 item 3): a scenario is ECONOMICALLY REAL only if its
        # perturbation changes the effective friction the backtest actually
        # pays. Pre-fix, the canonical-costs pipeline ran with
        # spread+slip (20 ticks) ABOVE max_slippage_ticks (5) — the
        # min(spread+slip, cap) in compute_backtest pinned baseline AND every
        # stressed bundle to the same 5 ticks, so all six "stress" expectancies
        # were byte-identical to baseline, max_degradation was a constant 0.0,
        # and GATE8 PASSed every candidate that reached it (scoring's rob term
        # = 1.0 — the exact "silent no-op" P0-3 forbids). Latency scenarios
        # have never touched P&L in the deterministic backtest (canonical
        # artifact: "LIVE latency UNKNOWN until measured") — recording them as
        # NOT_SIMULABLE instead of a fake 0-degradation PASS.
        base_eff_friction = _effective_friction_ticks(self.baseline)
        ineffective: list[str] = []
        max_deg = 0.0
        for name, params in self.scenarios:
            stressed = self.baseline.with_perturbation(
                spread=params.get("spread", 0.0),
                sl=params.get("sl", 0.0),
                latency=params.get("latency", 0.0),
            )
            if _effective_friction_ticks(stressed) == base_eff_friction:
                ineffective.append(name)
            bt = compute_backtest(
                dataset.samples,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                dataset_id=dataset.dataset_id,
                assumptions=stressed,
            )
            stress_expectancies[name] = round(bt.expectancy_r, 6)
            deg = baseline_exp - bt.expectancy_r
            max_deg = max(max_deg, deg)

        # Fragile if any single stress pushes expectancy negative AND the drop
        # is material, or if total max degradation exceeds the ceiling.
        reasons: list[str] = []
        worst = min(stress_expectancies.values()) if stress_expectancies else 0.0
        failed = max_deg > self.max_acceptable_deg_r
        if failed:
            reasons.append(
                f"Max degradation {max_deg:.4f}R exceeds ceiling {self.max_acceptable_deg_r}R"
            )
        if worst < 0.0 and max_deg > self.max_acceptable_deg_r / 2.0:
            failed = True
            reasons.append(
                f"Worst stress expectancy {worst:.4f}R is negative under material stress"
            )
        # BUG-299 fail-closed: if EVERY stress scenario resolved to the same
        # effective friction as the baseline (a cap at or below base friction —
        # the pre-fix canonical shape — or a scenario set that only moves
        # dimensions the deterministic backtest cannot price), the gate
        # measured NOTHING and must never report PASS. Latency-only
        # ineffectiveness alongside live friction scenarios is expected and is
        # recorded in the census, never presented as a measured result.
        if not self.scenarios or len(ineffective) == len(self.scenarios):
            failed = True
            reasons.append(
                "ROBUSTNESS_NOT_SIMULABLE: no stress scenario changes the effective "
                f"friction the backtest pays ({len(self.scenarios)} scenarios, all "
                "non-impacting; base effective friction "
                f"{base_eff_friction:g} ticks, cap "
                f"{getattr(self.baseline, 'max_slippage_ticks', 0.0):g}) — the gate "
                "cannot measure degradation and fails closed"
            )
            logger.warning(
                "[ROBUSTNESS] event=NOT_SIMULABLE strategy_id=%s dead_scenarios=%s "
                "base_friction_ticks=%s cap=%s — a PASS from this bundle is impossible; "
                "check ExecutionAssumptions.max_slippage_ticks vs spread+slippage",
                strategy_id,
                ineffective,
                base_eff_friction,
                getattr(self.baseline, "max_slippage_ticks", None),
            )

        status = "FAIL" if failed else "PASS"
        logger.info(
            "[ROBUSTNESS] event=RESULT",
            strategy_id=strategy_id,
            baseline=round(baseline_exp, 6),
            max_degradation=round(max_deg, 6),
            status=status,
            not_simulable=len(ineffective),
        )
        return RobustnessResult(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            baseline_expectancy_r=round(baseline_exp, 6),
            stress_expectancies=stress_expectancies,
            max_degradation=round(max_deg, 6),
            status=status,
            reason="; ".join(reasons) or "Robust to modelled stress",
            not_simulable_scenarios=ineffective,
        )
