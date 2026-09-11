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
from typing import Any

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


def _sized_view_confidence_factor(confidence: float) -> float:
    """Per-trade confidence factor for the sized economic view (ECON v1).

    Reproduces the LIVE confidence contract exactly:

      * confidence <= 0 (NOT RECORDED / unknown)  -> 1.0 flat sizing.
        The live engine sizes at base risk when no validated calibration
        artifact is bound (NOT_CALIBRATED -> multiplier exactly 1.0,
        confidence_calibration.confidence_to_risk_multiplier) — unknown
        evidence never de-risks, never levers. The historical bug fed 0.0
        for every trade, silently applying the 0.25x floor to ALL history
        and understating sized risk/P&L/drawdown up to 4x.
      * recorded confidence (0, 1]                -> the calibrated band
        [LIVE_CONF_MULTIPLIER_MIN, 1.0] via SizingPolicy.confidence_scalar,
        the same factor the live pipeline composes (de-risk only).
    """
    # Lazy import: economics imports metrics' models layer (import-cycle
    # contract — see research/models.py rebuild_economic_refs).
    from nexus_scalp.research.economics import SizingPolicy

    if not math.isfinite(confidence) or confidence <= 0.0:
        return 1.0
    return SizingPolicy.confidence_scalar(min(1.0, confidence))


def compute_sized_economic_pnl(
    ordered: Sequence[ResearchSample],
    assumptions: Any,
) -> SizedEconomicResult:
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
    from nexus_scalp.configuration.config import RiskConfig
    from nexus_scalp.research.economics import compute_sizing
    from nexus_scalp.risk.risk_engine import RiskEngine

    sized_r: list[float] = []
    sized_pnl: list[float] = []
    volumes: list[float] = []
    exec_cost: list[float] = []
    swap_cost: list[float] = []
    gross_pnl: list[float] = []

    # PERF (research-perf audit 2026-09-09): compute_sizing previously built a
    # throwaway RiskEngine per trade. The engine is stateless sizing math
    # (broker step/min/max/margin rules), so ONE shared instance keyed to the
    # policy's broker limits produces IDENTICAL volumes without 5k re-inits.
    shared_engine: RiskEngine | None = RiskEngine(
        config=RiskConfig(risk_per_trade_pct=assumptions.sizing.base_risk_pct),
        max_allowed_lots=assumptions.sizing.max_allowed_lots,
    )

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
            # ECON-CONF: route the per-trade confidence through the LIVE
            # factor contract (unknown -> flat 1.0; recorded -> calibrated
            # de-risk band). Passing the raw sample value would let 0.0
            # (not-recorded) engage the 0.25x floor on EVERY trade.
            confidence=_sized_view_confidence_factor(
                float(getattr(s, "signal_confidence", 0.0) or 0.0)
            ),
            regime=str(s.regime or ""),
            risk_engine=shared_engine,
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

    # PERF (research-perf audit 2026-09-09): the equity curve was built with an
    # O(n^2) prefix re-summation (sum(sized_pnl[:i+1]) per element); a running
    # accumulator produces the IDENTICAL float sequence without the quadratic
    # blowup (5k samples: ~60ms -> ~0.3ms; 50k samples was ~6s).
    start_equity = float(assumptions.starting_equity_usd)
    curve = [start_equity]
    running = start_equity
    for net in sized_pnl:
        running += net
        curve.append(running)

    return SizedEconomicResult(
        sized_r=sized_r,
        sized_pnl_usd=sized_pnl,
        gross_pnl_usd=gross_pnl,
        execution_cost_usd=exec_cost,
        swap_cost_usd=swap_cost,
        volumes=volumes,
        equity_curve_usd=curve,
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
        max_dd = 0.0
        for v in self.equity_curve_usd:
            cum = float(v)
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
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


# ---------------------------------------------------------------------------
# OOS SIGNIFICANCE (edge-hardening 2026-09-09): promotion must not rest on a
# point estimate. A strategy whose OOS window holds too few trades, or whose
# bootstrap CI for mean OOS R still straddles zero, is NOISE — never evidence.
# Same non-parametric bootstrap rationale as directional_lab.bootstrap_mean_diff
# (R distributions are heavy-tailed), reused here for the one-sample case.
# ---------------------------------------------------------------------------

#: Minimum OOS trades before the CI is considered decisive evidence.
MIN_OOS_SIGNIFICANCE_SAMPLES: int = 12


def oos_significance(
    r_values: Sequence[float], *, n_boot: int = 2000, seed: int = 42
) -> dict[str, Any]:
    """Deterministic bootstrap CI for mean OOS R (one-sample).

    Returns {"n", "mean_r", "ci_low", "ci_high", "decisive"} where decisive
    means n >= MIN_OOS_SIGNIFICANCE_SAMPLES AND ci_low > 0 (the whole 95% CI
    sits above breakeven). Small samples are NEVER decisive — the caller must
    treat them as evidence-building, not as a pass.
    """
    vals = [float(v) for v in r_values if math.isfinite(float(v))]
    n = len(vals)
    if n < 2:
        return {
            "n": n,
            "mean_r": round(sum(vals) / n, 6) if n else 0.0,
            "ci_low": 0.0,
            "ci_high": 0.0,
            "decisive": False,
        }
    arr = np.asarray(vals, dtype=np.float64)
    rng = np.random.default_rng(seed)
    boot_means = np.empty(n_boot, dtype=np.float64)
    for i in range(n_boot):
        sample_idx = rng.integers(0, n, n)
        boot_means[i] = arr[sample_idx].mean()
    ci_low = float(np.percentile(boot_means, 2.5))
    ci_high = float(np.percentile(boot_means, 97.5))
    return {
        "n": n,
        "mean_r": round(float(arr.mean()), 6),
        "ci_low": round(ci_low, 6),
        "ci_high": round(ci_high, 6),
        "decisive": bool(n >= MIN_OOS_SIGNIFICANCE_SAMPLES and ci_low > 0.0),
    }


# ---------------------------------------------------------------------------
# SELECTION-BIAS CONTROL (edge round-2, 2026-09-09): when the search mines
# hundreds of candidate families from ONE dataset, the best-of-N expectancy is
# inflated by luck (multiple testing). Two deterministic, additive instruments:
#
#   * deflated_sharpe_ratio — Bailey & de Prado's DSR: the probability that a
#     TRUE Sharpe of 0 yields an observed (annualization-free, per-trade) SR
#     at least as extreme, given N independent trials and the variance of the
#     trials' SRs. Reports a 0..1 confidence.
#   * spa_family_pvalue — White's Reality Check (the SPA idea in its canonical
#     deterministic form): bootstrap p-value that the BEST family expectancy
#     in the mined set is attributable to luck. p <= alpha => survivor is real.
# ---------------------------------------------------------------------------


def sharpe_ratio_r(r_values: Sequence[float]) -> float:
    """Per-trade Sharpe ratio of an R series (mean / std, ddof=1)."""
    vals = np.asarray([float(v) for v in r_values if math.isfinite(float(v))], dtype=float)
    if len(vals) < 2:
        return 0.0
    std = float(np.std(vals, ddof=1))
    if std <= 1e-12:
        return 0.0
    return float(np.mean(vals)) / std


def deflated_sharpe_ratio(
    r_values: Sequence[float],
    n_trials: int,
    *,
    trial_sr_variance: float | None = None,
) -> dict[str, Any]:
    """Deflated Sharpe Ratio (Bailey & de Prado 2014), per-trade R semantics.

    n_trials: how many candidate strategies were mined/evaluated from the same
    data before this one (multiplicity). trial_sr_variance: variance of the
    trials' Sharpe ratios; when unknown, the conservative identity
    var(SR*) ≈ 1/(n-1) for unit-variance R series is used.

    Returns {"sr", "dsr", "n", "n_trials"} — dsr in [0,1] is P(SR0 < observed)
    under the deflated null; > 0.95 is the conventional "real after search" bar.
    Deterministic; no RNG (closed-form under CLT).
    """
    vals = np.asarray([float(v) for v in r_values if math.isfinite(float(v))], dtype=float)
    n = len(vals)
    if n < 3 or n_trials < 1:
        return {"sr": 0.0, "dsr": 0.0, "n": n, "n_trials": int(n_trials)}
    mean = float(np.mean(vals))
    std = float(np.std(vals, ddof=1))
    sr = mean / std if std > 1e-12 else 0.0
    # Higher-moment estimator variance of SR (Bailey-de Prado 2014, eq. for
    # V[SR]); with unit-ish R series this collapses near 1/(n-1).
    g1 = float(np.mean(((vals - mean) / std) ** 3)) if std > 1e-12 else 0.0  # skew
    g2 = float(np.mean(((vals - mean) / std) ** 4)) if std > 1e-12 else 3.0  # kurtosis
    sr_var = (
        float(trial_sr_variance)
        if trial_sr_variance is not None and trial_sr_variance > 0
        else (1.0 - g1 * sr + ((g2 - 1.0) / 4.0) * sr * sr) / max(n - 1, 1)
    )
    # Expected MAXIMUM SR under the null across n_trials independent trials
    # (Bailey-de Prado: Z_alpha = sqrt(2 ln N) - (ln(pi N)) / (2 sqrt(2 ln N))).
    ln_n = math.log(max(float(n_trials), 2.0))
    z_alpha = math.sqrt(2.0 * ln_n) - (math.log(math.pi * n_trials)) / (2.0 * math.sqrt(2.0 * ln_n))
    sr0 = math.sqrt(max(sr_var, 1e-12)) * z_alpha
    # DSR = P(true SR of a null-strategy max < observed SR) — closed form via erf.
    dsr = 0.5 * (1.0 + math.erf((sr - sr0) / math.sqrt(2.0 * max(sr_var, 1e-12))))
    dsr = max(0.0, min(1.0, dsr))
    return {"sr": round(sr, 6), "dsr": round(dsr, 6), "n": n, "n_trials": int(n_trials)}


def spa_family_pvalue(
    family_r_lists: Sequence[Sequence[float]],
    *,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """White's Reality Check p-value for the BEST mined family (SPA-style).

    family_r_lists: per-trade R lists for EVERY candidate mined from the same
    dataset (the multiplicity set). The null: the best family's mean R is 0 —
    i.e., its observed advantage is pure selection luck. Bootstrap resamples
    each family's pooled indices jointly (preserving cross-family dependence)
    and reports P(max family mean <= observed best mean under centered null).

    Deterministic via seeded RNG. Returns {"n_families", "best_mean_r",
    "p_value", "alpha", "survivor"} where survivor = p_value <= alpha.
    """
    families = [
        np.asarray([float(v) for v in lst if math.isfinite(float(v))], dtype=float)
        for lst in family_r_lists
        if lst is not None and len(lst) > 0
    ]
    families = [f for f in families if len(f) >= 2]
    n_fam = len(families)
    if n_fam == 0:
        return {
            "n_families": 0,
            "best_mean_r": 0.0,
            "p_value": 1.0,
            "alpha": 0.05,
            "survivor": False,
        }
    means = np.asarray([f.mean() for f in families], dtype=float)
    best_idx = int(np.argmax(means))
    best_mean = float(means[best_idx])
    # Centered null: shift every family to zero mean (White's RC step).
    centered = [f - f.mean() for f in families]
    rng = np.random.default_rng(seed)
    lengths = [len(f) for f in centered]
    boot_max_means = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        mx = -np.inf
        for f in centered:
            idx = rng.integers(0, len(f), len(f))
            m = float(f[idx].mean())
            mx = max(mx, m)
        boot_max_means[b] = mx
    # p = P(luck-only max >= observed best mean)
    p = float(np.mean(boot_max_means >= best_mean))
    p = max(0.0, min(1.0, p))
    return {
        "n_families": n_fam,
        "best_mean_r": round(best_mean, 6),
        "p_value": round(p, 6),
        "alpha": 0.05,
        "survivor": bool(p <= 0.05),
        "_best_idx": best_idx,
        "_lengths": lengths,
    }


# ECON v1: BacktestResult (in models) carries a forward reference to
# SizedEconomicResult (defined above). Now that this module has fully
# imported, the reference is resolvable — rebuild the model so the
# forward ref evaluates against THIS module's namespace.
from nexus_scalp.research import models as _models  # noqa: E402


def _rebuild_backtest_result() -> None:
    BacktestResult.model_rebuild(_types_namespace={"SizedEconomicResult": SizedEconomicResult})


_rebuild_backtest_result()
_models.BacktestResult.model_rebuild(_types_namespace={"SizedEconomicResult": SizedEconomicResult})
