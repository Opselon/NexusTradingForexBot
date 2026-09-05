"""
Walk-Forward Validation Engine
==============================
PHASE 09B (spec 14 / 38).

A strategy must survive repeated temporal re-evaluation. This engine consumes
the purged/embargoed walk-forward folds from `splitting.py`, backtests each
validation window, and tracks fold expectancy / drawdown / stability /
degradation. A strategy that succeeds in only one fold is not robust.
"""

from __future__ import annotations

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.metrics import compute_backtest, compute_relative_degradation
from nexus_scalp.research.models import (
    ExecutionAssumptions,
    ResearchDataset,
    WalkForwardFold,
    WalkForwardResult,
)
from nexus_scalp.research.splitting import (
    DEFAULT_EMBARGO_SECONDS,
    DEFAULT_PURGE_SECONDS,
    walk_forward_folds,
)

logger = get_logger("nexus_scalp.research.walkforward")

#: A fold is a PASS when validation expectancy is positive.
MIN_FOLD_EXPECTANCY_R: float = 0.0
#: Fraction of folds that must PASS for the strategy to be considered stable.
MIN_PASS_FRACTION: float = 0.5


class WalkForwardEngine:
    """Executes the walk-forward validation pipeline."""

    def __init__(
        self,
        min_pass_fraction: float = MIN_PASS_FRACTION,
        assumptions: ExecutionAssumptions | None = None,
    ) -> None:
        self.min_pass_fraction = float(min_pass_fraction)
        self.assumptions = assumptions or ExecutionAssumptions()

    def validate(
        self,
        dataset: ResearchDataset,
        strategy_id: str,
        strategy_version: str,
        n_splits: int = 3,
        val_frac: float = 0.2,
        purge_seconds: float = DEFAULT_PURGE_SECONDS,
        embargo_seconds: float = DEFAULT_EMBARGO_SECONDS,
        context_contract: dict | None = None,
    ) -> WalkForwardResult:
        # PHASE 26: strategy-aware sample filtering (reconstructed after
        # accidental working-tree loss; mirrors restored oos.py contract).
        dataset_for_folds = dataset
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
                    dataset_for_folds = dataset.model_copy(update={"samples": filtered})
                else:
                    context_diag["sufficient_evidence"] = False

        # PHASE 29 (data-volume honesty): when the population cannot support
        # the requested fold count, compute an ADAPTIVE fold number and, if
        # even one fold is impossible, return an explicit insufficient_reason
        # instead of a silent passed=False with zeroed metrics. The orchestrator
        # maps this to EVIDENCE_BUILDING (more data needed) — never REJECTED.
        min_needed = (n_splits + 2) * 3  # mirrors splitting.walk_forward_folds guard
        if len(dataset_for_folds.samples) < min_needed:
            reason = (
                f"FAMILY_TOO_SMALL_FOR_FOLDS: {len(dataset_for_folds.samples)} samples "
                f"< {min_needed} required for {n_splits} folds"
            )
            logger.warning(
                "[WALK_FORWARD] event=INSUFFICIENT_SAMPLES strategy_id=%s samples=%s needed=%s",
                strategy_id,
                len(dataset_for_folds.samples),
                min_needed,
            )
            result = WalkForwardResult(
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                dataset_id=dataset.dataset_id,
                folds=[],
                passed=False,
                avg_val_expectancy_r=0.0,
                avg_oos_expectancy_r=0.0,
                degradation=0.0,
                context_diagnostics=context_diag or None,
                insufficient_reason=reason,
            )
            return result

        folds = walk_forward_folds(
            dataset_for_folds,
            n_splits=n_splits,
            val_frac=val_frac,
            embargo_seconds=embargo_seconds,
            purge_seconds=purge_seconds,
        )
        fold_results: list[WalkForwardFold] = []
        pass_count = 0
        val_expects: list[float] = []
        oos_expects: list[float] = []

        for fold in folds:
            val_bt = compute_backtest(
                fold.validation,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                dataset_id=dataset.dataset_id,
                assumptions=self.assumptions,
            )
            oos_bt = compute_backtest(
                fold.oos,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                dataset_id=dataset.dataset_id,
                assumptions=self.assumptions,
            )
            # BUG-244 (Agent-5): a fold's own OOS window is evidence — a
            # negative-OOS fold must never be stamped PASS (scoring honors
            # walkforward.passed, so val-only gating let VALIDATED verdicts
            # rest on folds whose out-of-sample window lost money).
            val_pass = val_bt.expectancy_r > MIN_FOLD_EXPECTANCY_R
            oos_pass = oos_bt.expectancy_r >= MIN_FOLD_EXPECTANCY_R
            status = "PASS" if (val_pass and oos_pass) else "FAIL"
            if val_pass and oos_pass:
                pass_count += 1
            val_expects.append(val_bt.expectancy_r)
            oos_expects.append(oos_bt.expectancy_r)

            fold_results.append(
                WalkForwardFold(
                    fold=fold.fold,
                    train_start=fold.train_start,
                    train_end=fold.train_end,
                    val_start=fold.val_start,
                    val_end=fold.val_end,
                    oos_start=fold.oos_start,
                    oos_end=fold.oos_end,
                    train_samples=len(fold.train),
                    val_samples=len(fold.validation),
                    oos_samples=len(fold.oos),
                    val_expectancy_r=round(val_bt.expectancy_r, 6),
                    oos_expectancy_r=round(oos_bt.expectancy_r, 6),
                    oos_drawdown_r=round(oos_bt.max_drawdown_r, 6),
                    status=status,
                )
            )

        avg_val = _avg(val_expects)
        avg_oos = _avg(oos_expects)
        # Degradation: relative drop from avg validation to avg OOS (stable formula).
        degradation = compute_relative_degradation(avg_val, avg_oos)

        total_folds = len(fold_results)
        passed = (
            total_folds > 0
            and (pass_count / total_folds) >= self.min_pass_fraction
            and avg_oos >= MIN_FOLD_EXPECTANCY_R
        )

        logger.info(
            "[WALK_FORWARD] event=COMPLETE",
            strategy_id=strategy_id,
            version=strategy_version,
            folds=total_folds,
            passes=pass_count,
            avg_val=round(avg_val, 6),
            avg_oos=round(avg_oos, 6),
            status="PASS" if passed else "FAIL",
        )
        return WalkForwardResult(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset.dataset_id,
            folds=fold_results,
            passed=passed,
            avg_val_expectancy_r=round(avg_val, 6),
            avg_oos_expectancy_r=round(avg_oos, 6),
            degradation=round(degradation, 6),
            context_diagnostics=(context_diag or None),
        )


def _avg(values: list[float]) -> float:
    return (sum(values) / len(values)) if values else 0.0
