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
)

logger = get_logger("nexus_scalp.research.robustness")

#: Max absolute R degradation from baseline before the strategy is FRAGILE.
MAX_ACCEPTABLE_DEGRADATION_R: float = 0.25
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
        self.baseline = baseline or ExecutionAssumptions()
        self.max_acceptable_deg_r = float(max_acceptable_deg_r)
        self.scenarios = scenarios if scenarios is not None else STRESS_SCENARIOS

    def evaluate(
        self,
        dataset: ResearchDataset,
        strategy_id: str,
        strategy_version: str,
    ) -> RobustnessResult:
        base_bt = compute_backtest(
            dataset.samples,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset.dataset_id,
            assumptions=self.baseline,
        )
        baseline_exp = base_bt.expectancy_r
        stress_expectancies: dict[str, float] = {}
        max_deg = 0.0
        for name, params in self.scenarios:
            stressed = self.baseline.with_perturbation(
                spread=params.get("spread", 0.0),
                sl=params.get("sl", 0.0),
                latency=params.get("latency", 0.0),
            )
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

        status = "FAIL" if failed else "PASS"
        logger.info(
            "[ROBUSTNESS] event=RESULT",
            strategy_id=strategy_id,
            baseline=round(baseline_exp, 6),
            max_degradation=round(max_deg, 6),
            status=status,
        )
        return RobustnessResult(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            baseline_expectancy_r=round(baseline_exp, 6),
            stress_expectancies=stress_expectancies,
            max_degradation=round(max_deg, 6),
            status=status,
            reason="; ".join(reasons) or "Robust to modelled stress",
        )
