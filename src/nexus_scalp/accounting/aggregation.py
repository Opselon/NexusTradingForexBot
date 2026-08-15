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
