"""
Hard Out-of-Sample Gate
=======================
PHASE 09B (spec 15 / 34 / 38).

A candidate can NOT become VALIDATED merely because in-sample performance is
excellent; it MUST survive the out-of-sample gate. A strategy whose OOS is
negative is REJECTED even if win rate is high (spec 34).
"""

from __future__ import annotations

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.metrics import compute_backtest, compute_relative_degradation
from nexus_scalp.research.models import (
    ExecutionAssumptions,
    OOSResult,
    ResearchDataset,
)
from nexus_scalp.research.splitting import split_temporal

logger = get_logger("nexus_scalp.research.oos")

#: OOS must at minimum retain a non-negative expectancy to pass the gate.
MIN_OOS_EXPECTANCY_R: float = 0.0
#: Maximum acceptable relative degradation from in-sample to OOS.
MAX_OOS_DEGRADATION: float = 1.0  # 100% relative drop is the hard ceiling


class OOSGate:
    """Enforces the hard out-of-sample gate."""

    def __init__(
        self,
        min_oos_expectancy_r: float = MIN_OOS_EXPECTANCY_R,
        max_degradation: float = MAX_OOS_DEGRADATION,
        assumptions: ExecutionAssumptions | None = None,
    ) -> None:
        self.min_oos_expectancy_r = float(min_oos_expectancy_r)
        self.max_degradation = float(max_degradation)
        self.assumptions = assumptions or ExecutionAssumptions()

    def evaluate(
        self,
        dataset: ResearchDataset,
        strategy_id: str,
        strategy_version: str,
        val_frac: float = 0.2,
        oos_frac: float = 0.2,
        purge_seconds: float = 0.0,
        embargo_seconds: float = 0.0,
        context_contract: dict | None = None,
    ) -> OOSResult:
        # PHASE 26 (strategy-aware validation): scope the evaluation
        # population to the strategy's declared market conditions when a
        # contract is supplied. Thresholds are untouched; only the sample
        # population changes, and the diagnostics travel on the result.
        dataset_for_eval = dataset
        context_diag: dict = {}
        if context_contract:
            from nexus_scalp.research.context_contract import (
                filter_samples_by_contract,
                has_active_contract,
            )

            if has_active_contract(context_contract):
                filtered, context_diag = filter_samples_by_contract(
                    list(dataset.samples), context_contract
                )
                if filtered:
                    dataset_for_eval = dataset.model_copy(update={"samples": filtered})
                else:
                    context_diag["sufficient_evidence"] = False

        split = split_temporal(
            dataset_for_eval,
            val_frac=val_frac,
            oos_frac=oos_frac,
            embargo_seconds=embargo_seconds,
            purge_seconds=purge_seconds,
        )
        in_sample = split.train + split.validation
        in_bt = compute_backtest(
            in_sample,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset.dataset_id,
            assumptions=self.assumptions,
        )
        oos_bt = compute_backtest(
            split.oos,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset.dataset_id,
            assumptions=self.assumptions,
        )

        in_exp = in_bt.expectancy_r
        oos_exp = oos_bt.expectancy_r
        degradation = compute_relative_degradation(in_exp, oos_exp)

        oos_samples = len(split.oos)
        reasons: list[str] = []
        passed = oos_exp >= self.min_oos_expectancy_r
        if not passed:
            reasons.append(
                f"OOS expectancy {oos_exp:.4f}R below minimum {self.min_oos_expectancy_r}R"
            )
        if oos_samples == 0:
            passed = False
            reasons.append("No out-of-sample samples available")
        if in_exp > 0.0 and degradation > self.max_degradation:
            passed = False
            reasons.append(
                f"OOS degradation {degradation:.2f} exceeds max {self.max_degradation:.2f}"
            )
        if in_exp <= 0.0 and oos_exp <= 0.0:
            if oos_samples:
                reasons.append("In-sample and OOS both non-positive")

        status = "PASS" if passed else "FAIL"
        reason = "; ".join(reasons) or "OOS evidence confirms positive edge"

        logger.info(
            "[OOS] event=RESULT",
            strategy_id=strategy_id,
            oos_expectancy_r=round(oos_exp, 6),
            status=status,
        )
        return OOSResult(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset.dataset_id,
            in_sample_expectancy_r=round(in_exp, 6),
            oos_expectancy_r=round(oos_exp, 6),
            oos_samples=oos_samples,
            oos_win_rate=round(oos_bt.win_rate, 6),
            status=status,
            reason=reason,
            context_diagnostics=(context_diag or None),
        )
