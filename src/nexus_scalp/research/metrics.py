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


def variance_preserving_mean(values: Sequence[float]) -> float:
    """Mean ignoring NaN; robust for downstream scoring."""
    arr = np.asarray([float(v) for v in values if not np.isnan(v)], dtype=float)
    return float(np.mean(arr)) if len(arr) else 0.0
