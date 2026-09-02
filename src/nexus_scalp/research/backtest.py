"""
Backtest Engine
===============
PHASE 09B deterministic, friction-aware backtest over recorded experience
(spec 12 / 13).

The engine runs `compute_backtest` over a decided sample partition. Because the
experience ledger records realised R, exit PnL, holding duration and MAE/MFE for
each executed+closed trade, the backtest reconstructs a realistic simulation
with entry/spread/slippage/latency friction modelled explicitly. It is
DETERMINISTIC: same dataset + strategy version + config + execution assumptions
=> same result.
"""

from __future__ import annotations

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.metrics import compute_backtest
from nexus_scalp.research.models import (
    BacktestResult,
    ExecutionAssumptions,
    ResearchDataset,
)
from nexus_scalp.research.splitting import (
    DEFAULT_EMBARGO_SECONDS,
    DEFAULT_PURGE_SECONDS,
    split_temporal,
)

logger = get_logger("nexus_scalp.research.backtest")


class BacktestEngine:
    """Runs deterministic backtests over research datasets."""

    def __init__(self, assumptions: ExecutionAssumptions | None = None) -> None:
        self.assumptions = assumptions or ExecutionAssumptions()

    def run(
        self,
        dataset: ResearchDataset,
        strategy_id: str,
        strategy_version: str,
        use_split: bool = False,
        val_frac: float = 0.2,
        oos_frac: float = 0.2,
        purge_seconds: float = DEFAULT_PURGE_SECONDS,
        embargo_seconds: float = DEFAULT_EMBARGO_SECONDS,
        split: object | None = None,
    ) -> BacktestResult:
        """
        Runs a deterministic backtest.

        `use_split=True` backtests only the TRAIN+VALIDATION partition (never the
        OOS), which is the correct in-sample measurement for walk-forward/OOS
        gates. Otherwise backtests the whole dataset.
        """
        samples = dataset.samples
        if use_split:
            tsplit = split_temporal(
                dataset,
                val_frac=val_frac,
                oos_frac=oos_frac,
                purge_seconds=purge_seconds,
                embargo_seconds=embargo_seconds,
            )
            samples = tsplit.train + tsplit.validation

        logger.info(
            "[BACKTEST] event=START",
            strategy_id=strategy_id,
            dataset=dataset.dataset_id,
            samples=len(samples),
        )
        result = compute_backtest(
            samples,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset.dataset_id,
            assumptions=self.assumptions,
        )
        logger.info(
            "[BACKTEST] event=COMPLETE",
            strategy_id=strategy_id,
            expectancy_r=round(result.expectancy_r, 6),
            drawdown_r=round(result.max_drawdown_r, 6),
            trades=result.total_trades,
        )
        return result
