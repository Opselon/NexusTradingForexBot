"""
Period & Drawdown Aggregation Math
==================================
Pure functions turning canonical `TradeRecord` / `AccountSnapshot` sequences into
`PeriodReport` and `DrawdownReport`.

Kept free of I/O so the same code path serves the live API, the background worker
and any rebuild — one methodology, no consumer-specific variants.

STATISTICAL HONESTY RULES
-------------------------
* Ratios that are undefined for the sample stay `None` (profit factor with zero
  losses, expectancy with zero trades). They are NOT clamped to 1.0 or 0.0.
* R-based statistics are computed only over trades whose risk basis could be
  reconstructed; `r_sample_count` reports how many that was, so a "+1.4 avg R"
  built from 2 of 50 trades is visibly thin rather than silently authoritative.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from nexus_scalp.accounting.models import (
    AccountSnapshot,
    DrawdownReport,
    PeriodReport,
    TradeOutcome,
    TradeRecord,
)
from nexus_scalp.accounting.periods import PeriodBounds


def aggregate_period(
    bounds: PeriodBounds,
    trades: Iterable[TradeRecord],
    snapshots: Sequence[AccountSnapshot] | None = None,
) -> PeriodReport:
    """
    Aggregates all evidence belonging to one canonical period.

    Args:
        bounds: The half-open UTC interval to report on.
        trades: Closed trades whose `closed_at` falls inside `bounds`.
        snapshots: Account snapshots inside `bounds`, used for the balance/equity
            envelope and the intra-period drawdown. Optional: without them the
            money statistics are still exact, only the % return is unavailable.

    Returns:
        A fully populated `PeriodReport`. `has_data` is False when neither trades
        nor snapshots existed, which the dashboard renders as an explicit empty
        state instead of a zeroed row.
    """
    report = PeriodReport(
        kind=bounds.kind,
        key=bounds.key,
        label=bounds.label,
        period_start=bounds.start,
        period_end=bounds.end,
    )

    in_period = [t for t in trades if t.closed_at is not None and bounds.contains(t.closed_at)]
    snaps = list(snapshots or [])

    report.has_data = bool(in_period or snaps)
    if not report.has_data:
        return report

    wins: list[float] = []
    losses: list[float] = []
    r_values: list[float] = []
    durations: list[float] = []
    risk_total = 0.0
    risk_seen = False

    for trade in in_period:
        report.total_trades += 1
        report.net_pnl += trade.net_pnl
        report.commission_total += trade.commission
        report.swap_total += trade.swap
        report.total_volume += trade.volume

        if trade.outcome is TradeOutcome.WIN:
            report.win_count += 1
            report.gross_profit += trade.net_pnl
            wins.append(trade.net_pnl)
        elif trade.outcome is TradeOutcome.LOSS:
            report.loss_count += 1
            report.gross_loss += abs(trade.net_pnl)
            losses.append(abs(trade.net_pnl))
        else:
            report.breakeven_count += 1

        key = trade.exit_classification.value
        report.exit_breakdown[key] = report.exit_breakdown.get(key, 0) + 1

        if trade.realized_r is not None:
            r_values.append(trade.realized_r)
        if trade.risk_usd is not None:
            risk_total += trade.risk_usd
            risk_seen = True
        if trade.duration_sec > 0.0:
            durations.append(trade.duration_sec)

    if in_period:
        pnls = [t.net_pnl for t in in_period]
        report.best_trade = max(pnls)
        report.worst_trade = min(pnls)

    if report.total_trades > 0:
        decided = report.win_count + report.loss_count
        if decided > 0:
            report.win_rate = report.win_count / decided * 100.0
        report.expectancy = report.net_pnl / report.total_trades

        # --- PRO WIN-RATE RECONCILIATION (debug/audit contract) -------------------
        # Win rate can be reported against three denominators and all three must be
        # visible so a "9% win rate" can never be misread as an algorithm error:
        #   decided          = wins / (wins + losses)          (classic, denominator
        #                      excludes scratches)             -> win_rate field
        #   all_trades       = wins / total_trades             (includes breakevens)
        #   pnl_weighted     = gross_profit / (gross_profit + gross_loss)  share of
        #                      realized money captured as profit
        # Each is computed strictly from the same `in_period` trade list that fed
        # the report — one loop, one evidence source, no consumer-side re-derivation.
        dealt = [t for t in in_period if t.outcome is not TradeOutcome.BREAKEVEN]
        all_n = report.total_trades
        report.loss_rate_decided = (
            report.loss_count / len(dealt) * 100.0 if len(dealt) > 0 else None
        )
        report.loss_rate_all = report.loss_count / all_n * 100.0 if all_n > 0 else None
        report.win_rate_all = report.win_count / all_n * 100.0 if all_n > 0 else None
        numerator = report.gross_profit + report.gross_loss
        report.pnl_weighted_win_rate = (
            report.gross_profit / numerator * 100.0 if numerator > 0.0 else None
        )
        report.win_rate_denominator = (
            "DECIDED" if decided > 0 else ("ALL_TRADES" if all_n > 0 else "NONE")
        )

        # Breakeven-incl. expectancy; matches win-rate-all denominator so the pair
        # reconciles (wins + losses + breakevens = total trades).
        if all_n > 0:
            report.expectancy_breakeven_incl = report.net_pnl / all_n
        if len(dealt) > 0:
            report.avg_pnl_per_decided = report.net_pnl / len(dealt)

        # Cost drag in currency AND as a share of gross profit (uses gross_profit as
        # the denominator so negative-return samples stay meaningful).
        report.total_costs = report.commission_total + report.swap_total
        cost_denom = report.gross_profit + report.total_costs
        report.cost_drag_pct = report.total_costs / cost_denom * 100.0 if cost_denom > 0.0 else None

        # Loss persistence: share of losses exited by a protective stop, and the
        # E[r] on losers only (how much of planned risk each loser actually burned).
        if report.loss_count > 0:
            stop_losses = sum(
                1
                for t in in_period
                if t.outcome is TradeOutcome.LOSS
                and t.exit_classification is not None
                and t.exit_classification.is_stop_exit
            )
            report.stop_loss_share = stop_losses / report.loss_count
            loser_r = [
                t.realized_r
                for t in in_period
                if t.outcome is TradeOutcome.LOSS and t.realized_r is not None
            ]
            if loser_r:
                report.avg_loss_r = sum(loser_r) / len(loser_r)

    if wins:
        report.average_win = sum(wins) / len(wins)
    if losses:
        report.average_loss = sum(losses) / len(losses)

    # Profit factor is undefined without losses; reporting "inf" or 1.0 would both
    # be misleading, so it stays None and the UI shows n/a.
    if report.gross_loss > 0.0:
        report.profit_factor = report.gross_profit / report.gross_loss

    if r_values:
        report.average_r = sum(r_values) / len(r_values)
        report.r_sample_count = len(r_values)

    if durations:
        report.average_holding_sec = sum(durations) / len(durations)
    if risk_seen:
        report.total_risk_deployed = risk_total

    if snaps:
        ordered = sorted(snaps, key=lambda s: s.timestamp)
        report.starting_balance = ordered[0].balance
        report.ending_balance = ordered[-1].balance
        report.starting_equity = ordered[0].equity
        report.ending_equity = ordered[-1].equity

        if report.starting_balance and report.starting_balance > 0.0:
            report.pnl_pct = report.net_pnl / report.starting_balance * 100.0

        intra = intraperiod_drawdown(ordered)
        report.max_drawdown_pct = intra[0]
        report.max_drawdown_usd = intra[1]

    return report


def intraperiod_drawdown(
    snapshots: Sequence[AccountSnapshot],
) -> tuple[float | None, float | None]:
    """
    Peak-to-trough equity drawdown WITHIN the supplied snapshot window.

    Returns (max_drawdown_pct, max_drawdown_usd), or (None, None) when fewer than
    two samples exist — a single snapshot cannot express a drawdown.
    """
    if len(snapshots) < 2:
        return None, None

    peak = snapshots[0].equity
    max_pct = 0.0
    max_usd = 0.0
    for snap in snapshots:
        peak = max(peak, snap.equity)
        if peak <= 0.0:
            continue
        drop_usd = peak - snap.equity
        max_usd = max(max_usd, drop_usd)
        drop_pct = drop_usd / peak * 100.0
        max_pct = max(max_pct, drop_pct)
    return max_pct, max_usd


def compute_drawdown(snapshots: Sequence[AccountSnapshot]) -> DrawdownReport:
    """
    THE canonical drawdown methodology for the whole system.

    Peak-to-trough on the equity snapshot series, in percent of the running peak.
    Also reports how long the current drawdown has lasted and, when the account
    has recovered, how long the recovery took.
    """
    report = DrawdownReport(sample_count=len(snapshots))
    if not snapshots:
        return report

    ordered = sorted(snapshots, key=lambda s: s.timestamp)
    report.has_data = True

    peak_equity = ordered[0].equity
    peak_balance = ordered[0].balance
    peak_at = ordered[0].timestamp

    max_pct = 0.0
    max_usd = 0.0
    max_at = None
    #: Timestamp where the current uninterrupted drawdown began.
    current_dd_start: object = None
    #: (start, end) of the most recent completed peak->recovery cycle.
    last_recovery: tuple[object, object] | None = None
    pending_recovery_start: object = None

    for snap in ordered:
        if snap.equity >= peak_equity:
            if pending_recovery_start is not None:
                last_recovery = (pending_recovery_start, snap.timestamp)
                pending_recovery_start = None
            peak_equity = snap.equity
            peak_at = snap.timestamp
            current_dd_start = None
        else:
            if current_dd_start is None:
                current_dd_start = peak_at
                pending_recovery_start = peak_at
            if peak_equity > 0.0:
                drop_usd = peak_equity - snap.equity
                drop_pct = drop_usd / peak_equity * 100.0
                if drop_pct > max_pct:
                    max_pct = drop_pct
                    max_usd = drop_usd
                    max_at = snap.timestamp

        peak_balance = max(peak_balance, snap.balance)

    last = ordered[-1]
    report.current_equity = last.equity
    report.peak_equity = peak_equity
    report.peak_balance = peak_balance
    report.max_drawdown_pct = max_pct
    report.max_drawdown_usd = max_usd
    report.max_drawdown_at = max_at  # type: ignore[assignment]

    current_usd = max(0.0, peak_equity - last.equity)
    report.current_drawdown_usd = current_usd
    report.current_drawdown_pct = (current_usd / peak_equity * 100.0) if peak_equity > 0.0 else None
    report.in_drawdown = current_usd > 0.0

    if report.in_drawdown and current_dd_start is not None:
        report.drawdown_duration_sec = (
            last.timestamp - current_dd_start  # type: ignore[operator]
        ).total_seconds()
        # How much of the worst drawdown has already been recovered.
        if max_usd > 0.0:
            report.recovery_pct = max(0.0, (max_usd - current_usd) / max_usd * 100.0)
    elif not report.in_drawdown and max_usd > 0.0:
        report.recovery_pct = 100.0

    if last_recovery is not None:
        report.recovery_duration_sec = (
            last_recovery[1] - last_recovery[0]  # type: ignore[operator]
        ).total_seconds()

    return report


def compute_advanced_metrics(
    trades: Sequence[TradeRecord],
    equity_points: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Advanced risk/return statistics (validated, sample-honest).

    Computed from closed trades + the equity curve. Every ratio is None when
    the sample cannot support it (statistical honesty rule of this module).

    Returns:
        {
            sample_trades, net_pnl, gross_profit, gross_loss,
            profit_factor, expectancy, average_win, average_loss,
            win_rate, avg_r, max_consecutive_wins, max_consecutive_losses,
            sharpe_ratio, sortino_ratio, calmar_ratio, sqn,
            recovery_factor, payoff_ratio, equity_volatility_pct,
            annualized_volatility_pct, downside_volatility_pct,
            profit_standard_error (profit factor t-statistic proxy),
            loss_rate_decided, loss_rate_all, win_rate_all,
            pnl_weighted_win_rate, win_rate_denominator,
            expectancy_breakeven_incl, avg_pnl_per_decided,
            total_costs, cost_drag_pct, stop_loss_share, avg_loss_r,
            avg_mae_r, avg_mfe_r, win_mae_capture_pct, loss_efficiency_pct,
            profit_skew, loss_skew, avg_hold_sec, volume_total, commission_total,
            swap_total, avg_risk_usd, r_coverage_ratio
        }
    """
    import math

    out: dict[str, Any] = {
        "sample_trades": 0,
        "net_pnl": None,
        "gross_profit": None,
        "gross_loss": None,
        "profit_factor": None,
        "expectancy": None,
        "average_win": None,
        "average_loss": None,
        "win_rate": None,
        "avg_r": None,
        "avg_r_multiple": None,
        "r_sample_count": 0,
        "max_consecutive_wins": 0,
        "max_consecutive_losses": 0,
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "sqn": None,
        "recovery_factor": None,
        "payoff_ratio": None,
        "equity_volatility_pct": None,
        "downside_volatility_pct": None,
        "annualized_volatility_pct": None,
        "profit_standard_error": None,
        "loss_rate_decided": None,
        "loss_rate_all": None,
        "win_rate_all": None,
        "pnl_weighted_win_rate": None,
        "win_rate_denominator": "NONE",
        "expectancy_breakeven_incl": None,
        "avg_pnl_per_decided": None,
        "total_costs": None,
        "cost_drag_pct": None,
        "stop_loss_share": None,
        "avg_loss_r": None,
        "avg_mae_r": None,
        "avg_mfe_r": None,
        "win_mae_capture_pct": None,
        "loss_efficiency_pct": None,
        "profit_skew": None,
        "loss_skew": None,
        "avg_hold_sec": None,
        "volume_total": None,
        "commission_total": None,
        "swap_total": None,
        "avg_risk_usd": None,
        "r_coverage_ratio": None,
    }

    closed = [t for t in trades if t.closed_at is not None]
    if not closed:
        return out
    closed = sorted(closed, key=lambda t: t.closed_at)  # type: ignore[arg-type,return-value]

    out["sample_trades"] = len(closed)
    pnls = [float(t.net_pnl) for t in closed]
    net = sum(pnls)
    out["net_pnl"] = round(net, 2)
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    out["gross_profit"] = round(gross_profit, 2)
    out["gross_loss"] = round(gross_loss, 2)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    decided = len(wins) + len(losses)

    if decided:
        out["win_rate"] = round(len(wins) / decided * 100.0, 2)
    if losses:
        out["profit_factor"] = round(gross_profit / gross_loss, 4)
    if decided:
        out["expectancy"] = round(net / decided, 2)
    if wins:
        out["average_win"] = round(sum(wins) / len(wins), 2)
    if losses:
        out["average_loss"] = round(sum(losses) / len(losses), 2)
    if wins and losses:
        out["payoff_ratio"] = round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 4)

    # R-based stats (only where risk basis recovered)
    r_vals: list[float] = []
    for t in closed:
        r = getattr(t, "realized_r", None)
        if r is None:
            r = getattr(t, "risk_r", None)
        if r is not None and math.isfinite(float(r)):
            r_vals.append(float(r))
    if r_vals:
        out["r_sample_count"] = len(r_vals)
        out["avg_r"] = round(sum(r_vals) / len(r_vals), 4)

    # Streaks
    cur_w, cur_l, max_w, max_l = 0, 0, 0, 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        elif p < 0:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
        else:
            cur_w = 0
            cur_l = 0
    out["max_consecutive_wins"] = max_w
    out["max_consecutive_losses"] = max_l

    # PRO WIN-RATE / LOSS-RATE RECONCILIATION (Phase 16)
    # Mirrors the PeriodReport contract so the advanced panel and the period
    # panel can never disagree: same denominators, same evidence (signed PnL
    # on closed trades), and an explicit denominator label.
    decided_n = len(wins) + len(losses)
    all_n = len(closed)
    out["win_rate_denominator"] = (
        "DECIDED" if decided_n > 0 else ("ALL_TRADES" if all_n > 0 else "NONE")
    )
    if decided_n > 0:
        out["win_rate"] = round(len(wins) / decided_n * 100.0, 2)
        out["loss_rate_decided"] = round(len(losses) / decided_n * 100.0, 2)
        out["expectancy"] = round(net / decided_n, 2)
        out["avg_pnl_per_decided"] = round(net / decided_n, 2)
    if all_n > 0:
        out["win_rate_all"] = round(len(wins) / all_n * 100.0, 2)
        out["loss_rate_all"] = round(len(losses) / all_n * 100.0, 2)
        out["expectancy_breakeven_incl"] = round(net / all_n, 2)
    denom = gross_profit + gross_loss
    if denom > 0.0:
        out["pnl_weighted_win_rate"] = round(gross_profit / denom * 100.0, 2)

    # Cost drag (currency + share of gross profit)
    total_costs = sum(float(t.commission) + float(t.swap) for t in closed)
    out["total_costs"] = round(total_costs, 2)
    out["commission_total"] = round(sum(float(t.commission) for t in closed), 2)
    out["swap_total"] = round(sum(float(t.swap) for t in closed), 2)
    cost_denom = gross_profit + total_costs
    if cost_denom > 0.0:
        out["cost_drag_pct"] = round(total_costs / cost_denom * 100.0, 2)

    # Loss persistence + per-trade quality
    if losses:
        stop_losses = sum(
            1
            for t in closed
            if t.net_pnl < 0
            and t.exit_classification is not None
            and t.exit_classification.is_stop_exit
        )
        out["stop_loss_share"] = round(stop_losses / len(losses), 4)
        loss_r = [float(t.realized_r) for t in closed if t.net_pnl < 0 and t.realized_r is not None]
        if loss_r:
            out["avg_loss_r"] = round(sum(loss_r) / len(loss_r), 4)
    if wins:
        win_r = [float(t.realized_r) for t in closed if t.net_pnl > 0 and t.realized_r is not None]
        if win_r:
            out["avg_r_multiple"] = round(sum(win_r) / len(win_r), 4)
    hold = [float(t.duration_sec) for t in closed if t.duration_sec > 0.0]
    if hold:
        out["avg_hold_sec"] = round(sum(hold) / len(hold), 1)
    out["volume_total"] = round(sum(float(t.volume) for t in closed), 2)
    risk_vals = [float(t.risk_usd) for t in closed if t.risk_usd is not None]
    if risk_vals:
        out["avg_risk_usd"] = round(sum(risk_vals) / len(risk_vals), 2)
    if r_vals:
        out["r_coverage_ratio"] = round(len(r_vals) / len(closed), 4)
    # MAE/MFE excursion factors (how much of the move each side captures)
    mae_vals = [float(t.mae_r) for t in closed if t.mae_r is not None]
    if mae_vals:
        out["avg_mae_r"] = round(sum(mae_vals) / len(mae_vals), 4)
    mfe_vals = [float(t.mfe_r) for t in closed if t.mfe_r is not None]
    if mfe_vals:
        out["avg_mfe_r"] = round(sum(mfe_vals) / len(mfe_vals), 4)
    if wins:
        win_mae = [float(t.mae_usd) for t in closed if t.net_pnl > 0]
        if win_mae:
            # What fraction of a winner's adverse excursion was given back
            # before it closed green. 100% = perfect stop discipline.
            out["win_mae_capture_pct"] = round(100.0 / (1.0 + abs(sum(win_mae) / sum(wins))), 4)
    if losses:
        loss_mfe = [float(t.mfe_usd) for t in closed if t.net_pnl < 0]
        if loss_mfe:
            # What fraction of a loser's favourable excursion was left on the
            # table before it closed red. 0% = ideal (never gave back).
            out["loss_efficiency_pct"] = round(abs(sum(loss_mfe) / sum(losses)) * 100.0, 4)

    # Skewness of the profit / loss distributions (raw third moment, scaled).
    if len(wins) >= 2:
        wm = sum(wins) / len(wins)
        wsd = math.sqrt(sum((w - wm) ** 2 for w in wins) / len(wins)) or 1.0
        out["profit_skew"] = round(sum(((w - wm) / wsd) ** 3 for w in wins) / len(wins), 4)
    if len(losses) >= 2:
        lm = sum(losses) / len(losses)
        lsd = math.sqrt(sum((l - lm) ** 2 for l in losses) / len(losses)) or 1.0
        out["loss_skew"] = round(sum(((l - lm) / lsd) ** 3 for l in losses) / len(losses), 4)

    # Equity-curve risk (from snapshots when provided)
    if equity_points and len(equity_points) >= 3:
        eqs = [float(p.get("equity", 0.0)) for p in equity_points if p.get("equity") is not None]
        if len(eqs) >= 3 and all(e > 0 for e in eqs):
            rets = [(eqs[i] / eqs[i - 1]) - 1.0 for i in range(1, len(eqs))]
            import statistics

            mean_r = statistics.mean(rets)
            var_r = statistics.pvariance(rets) if len(rets) > 1 else 0.0
            std_r = math.sqrt(var_r)
            down = [r for r in rets if r < 0]
            down_var = (
                statistics.pvariance(down) if len(down) > 1 else (0.0 if down else float("nan"))
            )
            down_std = math.sqrt(down_var) if math.isfinite(down_var) else 0.0

            # annualized (assume ~252 trading days * 24h = 6048 equity intervals/day
            # is unrealistic; use per-point std as daily-proxy only when points
            # are trade-closed or snapshots; report per-interval ratios directly)
            if std_r > 0:
                out["sharpe_ratio"] = round(mean_r / std_r * math.sqrt(len(rets)), 4)
            if down_std > 0:
                out["sortino_ratio"] = round(mean_r / down_std * math.sqrt(len(rets)), 4)
            out["equity_volatility_pct"] = round(std_r * 100.0, 4)
            out["downside_volatility_pct"] = (
                round(down_std * 100.0, 4) if math.isfinite(down_std) else None
            )
            # Annualized vol proxy: sqrt( per-point variance * points-per-year )
            if len(rets) > 1:
                ppy_estimate = max(len(rets) / 7.0, 1.0)  # ~1 week of points per year-slice
                out["annualized_volatility_pct"] = round(std_r * math.sqrt(ppy_estimate) * 100.0, 4)

    # Drawdown-based ratios (Calmar, recovery factor) from equity points
    if equity_points and len(equity_points) >= 2:
        eqs = [float(p.get("equity", 0.0)) for p in equity_points if p.get("equity") is not None]
        peak, max_dd = 0.0, 0.0
        for e in eqs:
            peak = max(peak, e)
            if peak > 0:
                max_dd = max(max_dd, (peak - e) / peak)
        if max_dd > 0:
            # Calmar = annualized return / max drawdown (annualized return proxy:
            # total net return over the sample span)
            if net != 0 and eqs[0] > 0:
                total_ret = net / eqs[0]
                out["calmar_ratio"] = round(total_ret / max_dd, 4) if max_dd > 0 else None
            out["recovery_factor"] = round(abs(net) / (max_dd * eqs[0]) if eqs[0] > 0 else None, 4)

    # System Quality Number (SQN) with sample honesty
    if r_vals and len(r_vals) >= 5:
        import statistics as st

        mu = st.mean(r_vals)
        sd = st.stdev(r_vals) if len(r_vals) > 1 else 0.0
        if sd > 0:
            out["sqn"] = round(mu / sd * math.sqrt(len(r_vals)), 4)

    # Profit factor standard error (t-stat proxy on per-trade PnL)
    if len(pnls) >= 5:
        import statistics as st

        mu = st.mean(pnls)
        sd = st.stdev(pnls) if len(pnls) > 1 else 0.0
        if sd > 0:
            out["profit_standard_error"] = round(mu / sd * math.sqrt(len(pnls)), 4)

    return out
