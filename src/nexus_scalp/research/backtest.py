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
from nexus_scalp.research.economics import EconomicAssumptions, normalize_assumptions
from nexus_scalp.research.metrics import compute_backtest, compute_sized_economic_pnl
from nexus_scalp.research.models import (
    FALLBACK_ZERO_PROVENANCE,
    BacktestResult,
    ExecutionAssumptions,
    ResearchDataset,
    ensure_not_zero_friction,
)
from nexus_scalp.research.splitting import (
    DEFAULT_EMBARGO_SECONDS,
    DEFAULT_PURGE_SECONDS,
    split_temporal,
)

logger = get_logger("nexus_scalp.research.backtest")


class BacktestEngine:
    """Runs deterministic backtests over research datasets.

    ECON v1: the engine carries an explicit economic world. By default
    (``assumptions=None``) it runs PRODUCTION-LIKE economics (conservative
    evidence-derived friction + required-swap validation contract + sized
    economic re-valuation). Passing a legacy ``ExecutionAssumptions`` keeps
    the legacy friction semantics but LABELS the run FRICTIONLESS_RESEARCH
    (explicit construction = explicit analytical opt-in) and produces no
    fabricated sized view unless an EconomicAssumptions is supplied.
    """

    def __init__(
        self,
        assumptions: ExecutionAssumptions | None = None,
        economic: EconomicAssumptions | None = None,
    ) -> None:
        # E1/E2: record WHERE the friction bundle came from. The engine's
        # None-default stays PRODUCTION-LIKE (conservative, NON-zero friction —
        # never zero-cost). An explicit legacy ExecutionAssumptions travels
        # unchanged, but when it is the zero-cost bundle it is stamped
        # FALLBACK_ZERO so callers can detect the audit-E1 defect.
        if economic is not None:
            self.economic = economic
            self.assumptions = self._to_legacy(economic)
            self.assumptions_provenance = "EXPLICIT"
        elif assumptions is not None:
            self.economic = normalize_assumptions(assumptions)
            self.assumptions = self._to_legacy(self.economic)
            if assumptions.spread_ticks == 0.0 and assumptions.slippage_ticks == 0.0:
                self.assumptions_provenance = FALLBACK_ZERO_PROVENANCE
            else:
                self.assumptions_provenance = "EXPLICIT"
        else:
            self.economic = normalize_assumptions(None)
            self.assumptions = self._to_legacy(self.economic)
            self.assumptions_provenance = "PRODUCTION_LIKE_DEFAULT"

    @staticmethod
    def _to_legacy(economic: EconomicAssumptions) -> ExecutionAssumptions:
        """The friction view the deterministic core consumes (R semantics)."""
        f = economic.friction
        return ExecutionAssumptions(
            spread_ticks=f.spread_ticks,
            slippage_ticks=f.slippage_ticks,
            latency_ms=f.latency_ms,
            price_tick=f.price_tick,
            pay_spread=f.pay_spread,
            max_slippage_ticks=f.max_slippage_ticks,
        )

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
        allow_zero_friction: bool = False,
    ) -> BacktestResult:
        """
        Runs a deterministic backtest.

        `use_split=True` backtests only the TRAIN+VALIDATION partition (never the
        OOS), which is the correct in-sample measurement for walk-forward/OOS
        gates. Otherwise backtests the whole dataset.
        """
        # E1 loud guard: refuse a zero-cost run unless explicitly allowed.
        ensure_not_zero_friction(
            self.assumptions,
            allow=allow_zero_friction,
            context="BacktestEngine.run",
        )
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
        # ECON v1: attach the explicit economic world + the sized view so the
        # result is self-describing (profile, friction, swap, sizing policy).
        result = result.model_copy(
            update={
                "economic": self.economic,
                "sized": compute_sized_economic_pnl(samples, self.economic),
            }
        )
        logger.info(
            "[BACKTEST] event=COMPLETE",
            strategy_id=strategy_id,
            expectancy_r=round(result.expectancy_r, 6),
            drawdown_r=round(result.max_drawdown_r, 6),
            trades=result.total_trades,
        )
        return result
