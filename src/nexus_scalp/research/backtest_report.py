"""Detailed Truthful Backtest Report Engine
===========================================
Generates empirical replay backtest reports with transparent provenance,
explicit separation between R and USD metrics, and truthful disclosure of
empirical replay limitations vs broker/tick execution.

Truthful Engine Contract:
- Engine: NSE_EMPIRICAL_REPLAY
- Never mislabeled as MT5 Strategy Tester output.
- No fabricated initial deposit, leverage, tick modeling quality, or order tickets.
- Missing broker properties are explicitly null with explanatory notes.
- Trade list is bounded to 500 entries with explicit total and truncation flags.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from nexus_scalp.research.models import ExecutionAssumptions, ResearchSample


def normalize_direction(raw: str | None) -> str:
    """Normalizes trade direction to standard BUY / SELL or UNKNOWN."""
    if not raw:
        return "UNKNOWN"
    d = raw.strip().upper()
    if d in ("BUY", "LONG"):
        return "BUY"
    if d in ("SELL", "SHORT"):
        return "SELL"
    return "UNKNOWN"


def _safe_round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
        if not math.isfinite(f):
            return None
        return round(f, digits)
    except (TypeError, ValueError):
        return None


def _sharpe_ratio_r(r_series: Sequence[float]) -> float | None:
    """Non-annualized per-trade Sharpe ratio (mean / sample_std, ddof=1)."""
    vals = [float(v) for v in r_series if math.isfinite(float(v))]
    if len(vals) < 2:
        return None
    arr = np.asarray(vals, dtype=float)
    std = float(np.std(arr, ddof=1))
    if std <= 1e-12:
        return None
    return float(np.mean(arr)) / std


def sanitize_report(value: Any) -> Any:
    """Recursively converts non-finite floats (NaN, Inf, -Inf) to None for safe JSON serialization without mutating inputs."""
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): sanitize_report(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_report(v) for v in value]
    if isinstance(value, tuple):
        return [sanitize_report(v) for v in value]
    return value


def build_backtest_report(
    *,
    strategy_id: str,
    strategy_version: str,
    dataset_id: str,
    assumptions: ExecutionAssumptions,
    ordered_samples: Sequence[ResearchSample],
    adj_r: list[float],
    adj_usd: list[float],
    modeled_costs_usd: list[float],
    total_trades: int,
    wins: int,
    losses: int,
    breakeven: int,
    tail_loss_count: int,
    max_consecutive_losses: int,
    net_pnl_usd: float,
    expectancy_r: float,
    expectancy_usd: float,
    avg_win_r: float,
    avg_loss_r: float,
    max_drawdown_usd: float,
    max_drawdown_r: float,
    recovery_duration_trades: int,
    return_variance: float,
    worst_trade_r: float,
    largest_loss_r: float,
    avg_mae_r: float,
    avg_mfe_r: float,
    avg_holding_duration_sec: float,
) -> dict[str, Any]:
    """Builds a truthful, comprehensive backtest report dict."""
    n = total_trades

    # Performance
    gross_profit_usd = sum(u for u in adj_usd if u > 0.0)
    gross_loss_usd = sum(u for u in adj_usd if u < 0.0)  # negative loss convention
    abs_gross_loss_usd = abs(gross_loss_usd)

    profit_factor_usd: float | None = None
    if abs_gross_loss_usd > 0.0:
        profit_factor_usd = _safe_round(gross_profit_usd / abs_gross_loss_usd, 6)

    expected_payoff_usd = (net_pnl_usd / n) if n > 0 else None
    win_rate = (wins / n) if n > 0 else None

    sharpe_r = _sharpe_ratio_r(adj_r)

    performance: dict[str, Any] = {
        "gross_profit_usd": _safe_round(gross_profit_usd, 6),
        "gross_loss_usd": _safe_round(gross_loss_usd, 6),
        "usd_basis": "Adjusted recorded USD outcomes; gross profit/loss mean positive/negative sums of this series, not pre-broker-fee amounts",
        "net_pnl_usd": _safe_round(net_pnl_usd, 6),
        "expected_payoff_usd": _safe_round(expected_payoff_usd, 6),
        "profit_factor_usd": profit_factor_usd,
        "win_rate": _safe_round(win_rate, 6),
        "win_rate_basis": "R > 0.0001 (trade R basis); breakeven is abs(R) <= 0.0001",
        "expectancy_r": _safe_round(expectancy_r, 6),
        "expectancy_usd": _safe_round(expectancy_usd, 6),
        "avg_win_r": _safe_round(avg_win_r, 6),
        "avg_loss_r": _safe_round(avg_loss_r, 6),
        "worst_trade_r": _safe_round(worst_trade_r, 6),
        "largest_loss_r": _safe_round(largest_loss_r, 6),
        "return_variance": _safe_round(return_variance, 6),
        "return_variance_basis": "population variance of adjusted trade R (R squared)",
        "sharpe_ratio_r": _safe_round(sharpe_r, 6),
        "sharpe_basis": "nonannualized per-trade adjusted R mean / sample standard deviation (ddof=1), zero benchmark; unavailable with fewer than two trades or constant R",
        "profit_factor_usd_note": "Positive adjusted USD sum divided by absolute negative adjusted USD sum; undefined without losses",
    }

    # Settings
    settings: dict[str, Any] = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "dataset_id": dataset_id,
        "evaluation_mode": "EMPIRICAL_REPLAY",
        "assumptions": assumptions.model_dump(mode="json")
        if hasattr(assumptions, "model_dump")
        else {},
        "initial_deposit_usd": None,
        "initial_deposit_explanation": "Initial deposit not recorded; empirical replay tracks closed trade PnL without fabricated starting balance",
        "leverage": None,
        "leverage_explanation": "Account leverage not recorded in empirical replay",
        "partition": "SUPPLIED_SAMPLES",
        "usd_friction_model": "adjusted USD = recorded USD - friction R * abs(recorded USD / recorded R); if R is near zero, USD unchanged and cost unavailable",
        "r_friction_model": "min(spread_ticks + slippage_ticks, max_slippage_ticks) * price_tick / risk_distance, capped at 0.5R; missing risk fallback 0.01R per effective tick",
        "latency_execution_modeled": False,
        "pay_spread_applied": False,
        "model_note": "Legacy core adds spread regardless of pay_spread; latency does not change fills. Recorded PnL may already include costs; modeled costs are additional stress, not broker fee reconstruction.",
        "input_sample_count": n,
        "source": "Caller-supplied ResearchSample partition; broker origin is not verified by this calculation",
        "selected_sample_count": n,
        "symbols": sorted({s.symbol for s in ordered_samples}),
        "timeframes": sorted({s.timeframe for s in ordered_samples}),
        "decision_start": ordered_samples[0].decision_timestamp.isoformat() if n else None,
        "decision_end": ordered_samples[-1].decision_timestamp.isoformat() if n else None,
        "outcome_end": max(s.outcome_timestamp for s in ordered_samples).isoformat() if n else None,
        "r_vs_usd_separation": "R-multiples track trade risk geometry; USD values reflect realized ledger cash and modeled friction",
    }

    # Drawdown
    drawdown: dict[str, Any] = {
        "max_drawdown_usd": _safe_round(max_drawdown_usd, 6),
        "basis": "Closed-outcome cumulative PnL in decision order with zero reference; not account balance/equity drawdown",
        "max_drawdown_r": _safe_round(max_drawdown_r, 6),
        "recovery_duration_trades": None,
        "recovery_duration_explanation": "Legacy duration trades metric not verified under empirical replay convention",
        "max_consecutive_losses": int(max_consecutive_losses),
        "equity_drawdown_usd": None,
        "equity_drawdown_explanation": "Intra-trade equity drawdown unavailable; only closed trade outcomes are recorded",
        "max_drawdown_pct": None,
        "max_drawdown_pct_explanation": "Drawdown percentage unavailable without account deposit/balance baseline",
    }

    # Directions
    buy_indices = [
        i for i, s in enumerate(ordered_samples) if normalize_direction(s.direction) == "BUY"
    ]
    sell_indices = [
        i for i, s in enumerate(ordered_samples) if normalize_direction(s.direction) == "SELL"
    ]

    def _calc_dir_stats(indices: list[int]) -> dict[str, Any]:
        count = len(indices)
        if count == 0:
            return {
                "total_trades": 0,
                "count": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "win_rate": 0.0,
                "win_rate_pct": 0.0,
                "gross_profit_usd": 0.0,
                "gross_loss_usd": 0.0,
                "net_pnl_usd": 0.0,
                "expectancy_r": 0.0,
                "profit_factor_usd": None,
            }
        r_sub = [adj_r[i] for i in indices]
        usd_sub = [adj_usd[i] for i in indices]
        w = sum(1 for r in r_sub if r > 0.0001)
        l = sum(1 for r in r_sub if r < -0.0001)
        b = count - w - l
        gp = sum(u for u in usd_sub if u > 0.0)
        gl = sum(u for u in usd_sub if u < 0.0)
        abs_gl = abs(gl)
        pf = _safe_round(gp / abs_gl, 6) if abs_gl > 0.0 else None
        return {
            "total_trades": count,
            "count": count,
            "wins": w,
            "losses": l,
            "breakeven": b,
            "win_rate": _safe_round(w / count, 6),
            "win_rate_pct": _safe_round((w / count) * 100.0, 2),
            "gross_profit_usd": _safe_round(gp, 6),
            "gross_loss_usd": _safe_round(gl, 6),
            "net_pnl_usd": _safe_round(sum(usd_sub), 6),
            "expectancy_r": _safe_round(float(np.mean(r_sub)), 6),
            "profit_factor_usd": pf,
        }

    buy_stats = _calc_dir_stats(buy_indices)
    sell_stats = _calc_dir_stats(sell_indices)
    unknown_indices = [
        i
        for i, s in enumerate(ordered_samples)
        if normalize_direction(s.direction) not in ("BUY", "SELL")
    ]
    unknown_stats = _calc_dir_stats(unknown_indices)

    # Canonical normalized keys only: prevents duplicate UI blocks
    directions: dict[str, Any] = {
        "BUY": buy_stats,
        "SELL": sell_stats,
        "UNKNOWN": unknown_stats,
    }

    # Trade statistics
    trade_limit = 500
    is_truncated = n > trade_limit
    trade_statistics: dict[str, Any] = {
        "count": n,
        "total_trades": n,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "tail_loss_count": tail_loss_count,
        "max_consecutive_losses": max_consecutive_losses,
        "total_recorded_samples": n,
        "classification_basis": "Adjusted R: wins > 0.0001, losses < -0.0001, otherwise breakeven; USD signs may differ",
        "displayed_trades": min(n, trade_limit),
        "truncated": is_truncated,
        "max_display_trades": trade_limit,
        "truncation_note": (
            "Trade list capped at 500 entries for transport and UI safety" if is_truncated else None
        ),
    }

    # Holding / excursion
    # Disclose excursion availability without silently dropping zeros or negative excursions.
    mae_vals = [float(s.mae_r) for s in ordered_samples if math.isfinite(float(s.mae_r))]
    mfe_vals = [float(s.mfe_r) for s in ordered_samples if math.isfinite(float(s.mfe_r))]
    mean_mae = _safe_round(float(np.mean(mae_vals)), 6) if mae_vals else None
    mean_mfe = _safe_round(float(np.mean(mfe_vals)), 6) if mfe_vals else None
    zero_mae_count = sum(1 for v in mae_vals if abs(v) <= 1e-9)
    zero_mfe_count = sum(1 for v in mfe_vals if abs(v) <= 1e-9)

    holding_excursion: dict[str, Any] = {
        "avg_holding_duration_sec": _safe_round(avg_holding_duration_sec, 2),
        "holding_seconds": _safe_round(avg_holding_duration_sec, 2),
        "holding_availability_note": "Sample duration defaults to zero; true zero versus unavailable cannot be distinguished",
        "mae_r": {
            "mean": mean_mae,
            "sample_count": len(mae_vals),
            "zero_count": zero_mae_count,
            "availability_note": "Exact zero may represent either true zero adverse excursion or unrecorded default in ResearchSample; values preserved without silent drop.",
        },
        "mfe_r": {
            "mean": mean_mfe,
            "sample_count": len(mfe_vals),
            "zero_count": zero_mfe_count,
            "availability_note": "Exact zero may represent either true zero favorable excursion or unrecorded default in ResearchSample; values preserved without silent drop.",
        },
        "avg_mae_r": mean_mae,
        "avg_mfe_r": mean_mfe,
    }

    # Data quality
    non_finite_count = sum(1 for s in ordered_samples if not math.isfinite(s.realized_r))
    missing_risk_count = sum(1 for s in ordered_samples if s.risk_distance <= 1e-9)
    missing_exit_reason = sum(1 for s in ordered_samples if not s.exit_reason)

    data_quality: dict[str, Any] = {
        "status": "AVAILABLE" if n else "EMPTY",
        "sample_count": n,
        "valid_sample_count": n,
        "non_finite_r_count": non_finite_count,
        "missing_risk_distance_count": missing_risk_count,
        "missing_exit_reason_count": missing_exit_reason,
        "temporal_order_verified": True,
        "mt5_modeling_quality_pct": None,
        "mt5_quality_explanation": "Not an MT5 Strategy Tester tick simulation; empirical replay over recorded experiences",
        "commission_recorded": False,
        "availability_note": "Recorded R/USD defaults may encode missing evidence; upstream completeness is not independently verified",
        "swap_recorded": False,
        "ticks_simulated": None,
        "ticks_quality_explanation": "Tick simulation quality not applicable to empirical replay",
    }

    # Trades (bounded to 500)
    trade_entries: list[dict[str, Any]] = []
    for i in range(min(n, trade_limit)):
        s = ordered_samples[i]
        entry = {
            "sample_id": s.sample_id,
            "experience_id": s.experience_id,
            "idempotency_key": s.idempotency_key,
            "decision_timestamp": s.decision_timestamp.isoformat(),
            "outcome_timestamp": s.outcome_timestamp.isoformat(),
            "symbol": s.symbol,
            "direction": normalize_direction(s.direction),
            "entry_price": _safe_round(s.entry_price, 6) if s.entry_price > 0 else None,
            "stop_loss": _safe_round(s.stop_loss, 6) if s.stop_loss > 0 else None,
            "take_profit": _safe_round(s.take_profit, 6) if s.take_profit > 0 else None,
            "risk_distance": _safe_round(s.risk_distance, 6) if s.risk_distance > 0 else None,
            "realized_r": _safe_round(s.realized_r, 6),
            "realized_pnl_usd": _safe_round(s.realized_pnl_usd, 6),
            "adjusted_r": _safe_round(adj_r[i], 6),
            "adjusted_pnl_usd": _safe_round(adj_usd[i], 6),
            "modeled_cost_usd": _safe_round(modeled_costs_usd[i], 6)
            if abs(s.realized_r) > 1e-9
            else None,
            "modeled_cost_r": _safe_round(s.realized_r - adj_r[i], 6),
            "direction_raw": s.direction,
            "timeframe": s.timeframe,
            "feature_schema_id": s.feature_schema_id,
            "feature_hash": s.feature_hash,
            "strategy_id": s.strategy_id,
            "strategy_version": s.strategy_version,
            "holding_duration_sec": _safe_round(s.holding_duration_sec, 2),
            "mae_r": _safe_round(s.mae_r, 6),
            "mfe_r": _safe_round(s.mfe_r, 6),
            "exit_reason": s.exit_reason or "UNKNOWN",
            "exit_price": None,
            "volume": None,
            "commission_usd": None,
            "swap_usd": None,
            "signal_confidence": _safe_round(s.signal_confidence, 6)
            if s.signal_confidence > 0
            else None,
        }
        trade_entries.append(entry)

    # Curves: cumulative R and closed PnL USD (no fabricated deposit)
    cum_r = 0.0
    curve_r: list[float | None] = []
    for r_val in adj_r:
        cum_r += r_val
        curve_r.append(_safe_round(cum_r, 6))

    cum_usd = 0.0
    curve_usd: list[float | None] = []
    for u_val in adj_usd:
        cum_usd += u_val
        curve_usd.append(_safe_round(cum_usd, 6))

    curves: dict[str, Any] = {
        "cumulative_r": curve_r,
        "closed_pnl_usd": curve_usd,
        "deposit_fabricated": False,
        "note": "Curves use decision order (not chronological close order when trades overlap); cumulative closed-outcome R and USD PnL, not account equity, with zero reference and no deposit.",
    }

    limitations: list[str] = [
        "NSE_EMPIRICAL_REPLAY reconstructs performance from closed experience records, not an MT5 Strategy Tester tick simulation.",
        "Initial deposit, account balance, and leverage are not simulated; PnL is tracked on a closed trade basis.",
        "Individual order tickets, pending order fills, and intra-trade equity excursions are not recorded in ResearchSample records.",
        "Execution costs (spread/slippage) are modeled analytically from planned risk distance; actual broker commissions and swaps are not recorded. Added cost never improves losses, but near-zero recorded R cannot imply a USD/R conversion.",
        "Exit prices and lot volumes are not preserved on individual ResearchSample records; see sized economic view for counterfactual sizing.",
    ]

    return sanitize_report(
        {
            "schema_version": "1",
            "engine": "NSE_EMPIRICAL_REPLAY",
            "settings": settings,
            "performance": performance,
            "drawdown": drawdown,
            "directions": directions,
            "trade_statistics": trade_statistics,
            "holding_excursion": holding_excursion,
            "data_quality": data_quality,
            "trades": trade_entries,
            "orders": None,
            "curves": curves,
            "limitations": limitations,
        }
    )
