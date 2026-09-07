"""
Pure Performance & Risk Statistics
==================================
PHASE 09B deterministic statistics for backtest / walk-forward / OOS.

All functions are pure and deterministic: given the same list of R-multiples
(and optional USD PnL), they return the same result. No I/O, no randomness.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel, Field

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.models import BacktestResult, ExecutionAssumptions, ResearchSample

logger = get_logger("nexus_scalp.research.metrics")


def _r_array(samples: Sequence[ResearchSample]) -> np.ndarray:
    vals = [float(s.realized_r) for s in samples]
    finite = [v for v in vals if math.isfinite(v)]
    if len(finite) != len(vals):
        logger.warning(
            "[STRATEGY_RESEARCH] event=NON_FINITE_R_EXCLUDED",
            count=len(vals) - len(finite),
        )
    return np.asarray(finite, dtype=float)


def _usd_array(samples: Sequence[ResearchSample]) -> np.ndarray:
    return np.asarray([float(s.realized_pnl_usd) for s in samples], dtype=float)


def drawdown_metrics(r_series: Sequence[float]) -> tuple[float, float, int]:
    """
    Returns (max_drawdown_usd_notional, max_drawdown_r, recovery_duration_trades).

    Drawdown computed on the cumulative R equity curve. Convention: drawdown is
    reported as a positive magnitude.
    """
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    max_dd_r = 0.0
    current_dd_trades = 0
    recovery_trades = 0
    worst_recovery = 0
    for r in r_series:
        cum += float(r)
        if cum > peak:
            peak = cum
            # recovered; record how long the just-ended drawdown lasted
            if current_dd_trades > 0:
                worst_recovery = max(worst_recovery, current_dd_trades)
            current_dd_trades = 0
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
            recovery_trades = current_dd_trades
        if dd > 0:
            current_dd_trades += 1
            max_dd_r = max(max_dd_r, dd)
    return max_dd, max_dd_r, max(recovery_trades, 0)


def compute_relative_degradation(
    in_sample: float,
    out_of_sample: float,
    *,
    epsilon: float = 1e-4,
    clip_max: float = 10.0,
) -> float:
    """Stable relative degradation: (in_sample - out_of_sample) / |in_sample|.

    BUG-140 Phase 6: the previous inline formula divided by |in_sample|
    unchecked, so an in-sample expectancy near zero exploded the ratio
    (e.g. avg_val=0.0001R, avg_oos=-0.001R -> degradation=-11.0 or worse),
    making the OOS gate's  comparison meaningless.

    Semantics (deterministic, signed):
      * |in_sample| >= epsilon : true relative ratio, clipped to
        [-clip_max, +clip_max] so a pathological ratio can never dominate
        gate arithmetic downstream.
      * |in_sample| <  epsilon  : the ratio is numerically meaningless;
        return the SIGN of the drop only (+1.0 degraded, -1.0 improved,
        0.0 negligible) — enough for the gate's  comparison without
        fabricating a magnitude.
    """
    diff = float(in_sample) - float(out_of_sample)
    denom = abs(float(in_sample))
    if denom < epsilon:
        if abs(diff) < epsilon:
            return 0.0
        return 1.0 if diff > 0.0 else -1.0
    ratio = diff / denom
    return max(-clip_max, min(clip_max, ratio))


def max_consecutive_losses(r_series: Sequence[float]) -> int:
    best = 0
    run = 0
    for r in r_series:
        if float(r) < 0.0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def compute_backtest(
    samples: Sequence[ResearchSample],
    strategy_id: str,
    strategy_version: str,
    dataset_id: str,
    assumptions: ExecutionAssumptions,
) -> BacktestResult:
    """
    Deterministic backtest over a fitted sample list.

    Friction is modeled by shifting each trade's realised R inward by the
    fraction of planned risk consumed by spread + slippage (points). If no
    planned risk is available, friction drops expectancy by an absolute floor.
    This keeps the backtest deterministic and realistic while remaining
    computable from the recorded experience alone.
    """
    ordered = sorted(samples, key=lambda s: s.decision_timestamp)
    n = len(ordered)
    if n == 0:
        return BacktestResult(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset_id,
            assumptions=assumptions,
        )

    friction_points = assumptions.spread_ticks + assumptions.slippage_ticks
    friction_ticks_eff = min(friction_points, assumptions.max_slippage_ticks)
    friction_r = 0.0

    adj_r: list[float] = []
    adj_usd: list[float] = []
    wins = losses = breakeven = 0
    tail_loss = 0
    mae_list: list[float] = []
    mfe_list: list[float] = []
    dur_list: list[float] = []
    worst_r = 0.0
    largest_loss_r = 0.0

    for s in ordered:
        risk = s.risk_distance  # price distance from entry to stop
        r = s.realized_r
        if friction_ticks_eff > 0:
            if risk > 1e-9:
                # Convert ticks to points via price_tick; degrade R by that fraction.
                friction_frac = (friction_ticks_eff * assumptions.price_tick) / risk
                friction_r = min(friction_frac, 0.5)  # never more than 0.5R friction
            else:
                friction_r = 0.01 * friction_ticks_eff
            r = r - friction_r
            # Degrade notional USD by the same R fraction when a non-zero R exists.
            if abs(s.realized_r) > 1e-9:
                usd_fraction = friction_r / abs(s.realized_r)
                adj_usd.append(s.realized_pnl_usd * max(0.0, 1.0 - usd_fraction))
            else:
                adj_usd.append(s.realized_pnl_usd)
        else:
            adj_usd.append(s.realized_pnl_usd)
        adj_r.append(r)
        if r > 0.0001:
            wins += 1
        elif r < -0.0001:
            losses += 1
        else:
            breakeven += 1
        if r <= -1.5:
            tail_loss += 1
        worst_r = min(worst_r, r)
        largest_loss_r = min(largest_loss_r, r)
        mae_list.append(s.mae_r)
        mfe_list.append(s.mfe_r)
        dur_list.append(s.holding_duration_sec)

    r_arr = np.asarray(adj_r, dtype=float)
    if len(r_arr) == 0 or not np.all(np.isfinite(r_arr)):
        # TASK-4: never let NaN/Inf reach statistics; an all-non-finite
        # dataset yields an empty (zero-trade) backtest, not NaN metrics.
        return BacktestResult(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset_id,
            assumptions=assumptions,
            total_trades=0,
        )
    expectancy_r = float(np.mean(r_arr)) if n else 0.0
    expectancy_usd = float(np.mean(adj_usd)) if n else 0.0
    net_pnl = float(np.sum(adj_usd))

    win_arr = r_arr[r_arr > 0.0001]
    loss_arr = r_arr[r_arr < -0.0001]
    avg_win = float(np.mean(win_arr)) if len(win_arr) else 0.0
    avg_loss = float(np.mean(loss_arr)) if len(loss_arr) else 0.0
    gross_win = float(np.sum(win_arr))
    gross_loss = float(abs(np.sum(loss_arr)))
    profit_factor = (
        gross_win / gross_loss if gross_loss > 1e-9 else (gross_win if gross_win > 0 else 0.0)
    )

    max_dd_usd, max_dd_r, recovery = drawdown_metrics(adj_r)
    consec = max_consecutive_losses(adj_r)
    var = float(np.var(r_arr)) if n else 0.0

    # Friction sensitivity: re-run with +1 tick friction to measure degradation.
    base_expectancy = expectancy_r
    spread_sens = 0.0
    slippage_sens = 0.0
    latency_sens = 0.0
    if n and base_expectancy != 0.0:
        spread_sens = _friction_sensitivity(ordered, assumptions, spread_delta=1.0, sl_delta=0.0)
        slippage_sens = _friction_sensitivity(ordered, assumptions, spread_delta=0.0, sl_delta=1.0)
    # Latency sensitivity is modeled as an immaterial fractional expectancy drop;
    # measured as absolute R degradation scaled by assumed latency per hour.
    latency_sens = abs(expectancy_r) * min(assumptions.latency_ms / 60000.0, 0.05)

    equity_curve = list(np.cumsum(r_arr))

    # ECON v1 sized economic view: re-price the same trades under the
    # canonical live sizing economics. The RAW metrics above stay the
    # analytical view; this adds the economically relevant valuation
    # (sized exposure, equity path, drawdown in USD, turnover) that
    # promotion must consult. A legacy-only assumptions object (no
    # EconomicAssumptions supplied) yields an EMPTY sized view rather than
    # a fabricated one — the caller chooses the economic world explicitly.
    sized: SizedEconomicResult | None = None
    economic = getattr(assumptions, "economic", None)
    if economic is not None:
        sized = compute_sized_economic_pnl(ordered, economic)

    return BacktestResult(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        dataset_id=dataset_id,
        assumptions=assumptions,
        total_trades=n,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        net_pnl_usd=net_pnl,
        expectancy_r=round(expectancy_r, 6),
        expectancy_usd=round(expectancy_usd, 6),
        avg_win_r=round(avg_win, 6),
        avg_loss_r=round(avg_loss, 6),
        profit_factor=round(profit_factor, 6),
        max_drawdown_usd=round(max_dd_usd, 6),
        max_drawdown_r=round(max_dd_r, 6),
        recovery_duration_trades=int(recovery),
        return_variance=round(var, 6),
        worst_trade_r=round(worst_r, 6),
        largest_loss_r=round(largest_loss_r, 6),
        tail_loss_count=int(tail_loss),
        max_consecutive_losses=int(consec),
        avg_mae_r=round(float(np.mean(mae_list)), 6) if mae_list else 0.0,
        avg_mfe_r=round(float(np.mean(mfe_list)), 6) if mfe_list else 0.0,
        avg_holding_duration_sec=round(float(np.mean(dur_list)), 2) if dur_list else 0.0,
        spread_sensitivity_r=round(spread_sens, 6),
        slippage_sensitivity_r=round(slippage_sens, 6),
        latency_sensitivity_r=round(latency_sens, 6),
        equity_curve_r=[round(float(x), 6) for x in equity_curve],
        sized=sized,
    )


def _friction_sensitivity(
    ordered: list[ResearchSample],
    assumptions: ExecutionAssumptions,
    spread_delta: float,
    sl_delta: float,
) -> float:
    """Computes expectancy under +1 tick friction and returns the R degradation."""
    stressed = assumptions.with_perturbation(spread=spread_delta, sl=sl_delta)
    friction = stressed.spread_ticks + stressed.slippage_ticks
    friction_eff = min(friction, stressed.max_slippage_ticks)
    adj: list[float] = []
    for s in ordered:
        risk = s.risk_distance
        r = s.realized_r
        if friction_eff > 0:
            if risk > 1e-9:
                frac = (friction_eff * stressed.price_tick) / risk
                r = r - min(frac, 0.5)
            else:
                r = r - 0.01 * friction_eff
        adj.append(r)
    return float(np.mean(adj)) if adj else 0.0


# ---------------------------------------------------------------------------
# SIZED ECONOMIC P&L (ECON v1: backtest models live sizing economics)
# ---------------------------------------------------------------------------
# The raw recorded ledger R/PnL is what the engine ACTUALLY traded (fixed
# historical account state). Promotion needs the counterfactual: what the SAME
# trades would have produced under the CANONICAL sizing policy (the live
# RiskEngine factor pipeline: regime x drawdown x confidence, risk% of the
# RUNNING equity path). This is computed causally — the equity/peak state at
# trade t is built only from trades with decision timestamps <= t. It is a
# valuation view, never a mutation of recorded evidence.


def compute_sized_economic_pnl(
    ordered: Sequence[ResearchSample],
    assumptions: "EconomicAssumptions",
) -> "SizedEconomicResult":
    """Re-prices a trade sequence under canonical live sizing economics.

    Per trade (in decision order):
      1. effective risk% = SizingPolicy.live pipeline on the RUNNING
         equity/peak path (causal: strictly prior closed trades),
      2. broker volume = RiskEngine.calculate_dynamic_volume(equity, entry,
         stop, risk%) — identical call to live sizing,
      3. gross PnL = recorded R * planned risk USD of the sized trade,
         where planned risk USD = volume * contract_size * risk_distance
         (falls back to the recorded PnL's implied risk when the risk
         distance is unavailable and volume is broker-minimum),
      4. execution friction (spread/slippage R + commission) deducted,
      5. swap charged per server-time rollover crossing.

    Deterministic; no future information enters sizing.
    """
    from nexus_scalp.research.economics import (
        compute_sizing,
        rollover_crossings,
    )

    sized_r: list[float] = []
    sized_pnl: list[float] = []
    volumes: list[float] = []
    exec_cost: list[float] = []
    swap_cost: list[float] = []
    gross_pnl: list[float] = []

    equity = float(assumptions.starting_equity_usd)
    peak = equity
    for s in ordered:
        risk_distance = float(s.risk_distance)
        decision = compute_sizing(
            policy=assumptions.sizing,
            instrument=assumptions.instrument,
            equity=equity,
            peak_equity=peak,
            entry=float(s.entry_price) if s.entry_price > 0 else 1.0,
            stop_loss=float(s.stop_loss) if s.stop_loss > 0 else 0.0,
            confidence=float(getattr(s, "signal_confidence", 0.0) or 0.0),
            regime=str(s.regime or ""),
        )
        volume = decision.volume
        volumes.append(volume)

        # Risk anchor for the sized trade. Prefer the planned risk distance;
        # when it is unavailable, anchor 1R at the recorded trade's own
        # realized risk so relative semantics survive data gaps (explicit
        # modeling of the missing input, never silent fixed-size).
        if risk_distance > 1e-9 and assumptions.instrument.contract_size > 0:
            risk_usd = volume * assumptions.instrument.contract_size * risk_distance
        elif abs(float(s.realized_r)) > 1e-9 and volume > 0:
            risk_usd = abs(float(s.realized_pnl_usd) / float(s.realized_r))
        else:
            risk_usd = 0.0

        gross = float(s.realized_r) * risk_usd
        friction_r = assumptions.friction.friction_r(risk_distance)
        f_cost = friction_r * risk_usd
        commission = assumptions.friction.commission_per_lot_usd * volume
        swap = 0.0
        if assumptions.swap.is_complete() and volume > 0:
            swap = assumptions.swap.swap_usd(
                s.direction,
                volume,
                s.decision_timestamp,
                s.outcome_timestamp,
            )
        net = gross - f_cost - commission + swap
        swap_signed = swap  # long debit / short credit already signed by rates

        sized_r.append(net / risk_usd if risk_usd > 1e-9 else 0.0)
        sized_pnl.append(net)
        gross_pnl.append(gross)
        exec_cost.append(f_cost + commission)
        swap_cost.append(swap_signed)

        equity += net
        peak = max(peak, equity)

    return SizedEconomicResult(
        sized_r=sized_r,
        sized_pnl_usd=sized_pnl,
        gross_pnl_usd=gross_pnl,
        execution_cost_usd=exec_cost,
        swap_cost_usd=swap_cost,
        volumes=volumes,
        equity_curve_usd=[float(assumptions.starting_equity_usd)]
        + [float(assumptions.starting_equity_usd) + sum(sized_pnl[: i + 1]) for i in range(len(sized_pnl))],
    )


class SizedEconomicResult(BaseModel):
    """Causal sized-P&L re-valuation of a trade sequence (ECON v1)."""

    sized_r: list[float] = Field(default_factory=list)
    sized_pnl_usd: list[float] = Field(default_factory=list)
    gross_pnl_usd: list[float] = Field(default_factory=list)
    execution_cost_usd: list[float] = Field(default_factory=list)
    swap_cost_usd: list[float] = Field(default_factory=list)
    volumes: list[float] = Field(default_factory=list)
    equity_curve_usd: list[float] = Field(default_factory=list)

    @property
    def net_pnl_usd(self) -> float:
        return float(sum(self.sized_pnl_usd))

    @property
    def max_drawdown_usd(self) -> float:
        peak = 0.0
        cum = 0.0
        max_dd = 0.0
        for v in self.equity_curve_usd:
            cum = float(v)
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def expectancy_r(self) -> float:
        return float(np.mean(self.sized_r)) if self.sized_r else 0.0

    @property
    def turnover_lots(self) -> float:
        return float(sum(self.volumes))



def variance_preserving_mean(values: Sequence[float]) -> float:
    """Mean ignoring NaN; robust for downstream scoring."""
    arr = np.asarray([float(v) for v in values if not np.isnan(v)], dtype=float)
    return float(np.mean(arr)) if len(arr) else 0.0


# ECON v1: BacktestResult (in models) carries a forward reference to
# SizedEconomicResult (defined above). Now that this module has fully
# imported, the reference is resolvable — rebuild the model so the
# forward ref evaluates against THIS module's namespace.
from nexus_scalp.research import models as _models  # noqa: E402


def _rebuild_backtest_result() -> None:
    BacktestResult.model_rebuild(_types_namespace={"SizedEconomicResult": SizedEconomicResult})


_rebuild_backtest_result()
_models.BacktestResult.model_rebuild(
    _types_namespace={"SizedEconomicResult": SizedEconomicResult}
)
