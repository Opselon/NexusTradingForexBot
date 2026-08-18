"""
Telegram formatter for the daily performance intelligence report.
==================================================================
Consumes the structured `ReportContainer` contract ONLY — never re-derives
numbers. Produces:

    format_telegram_daily(report)  -> compact summary message (MESSAGE 1)
    format_deep_report(report)     -> deep intelligence message (MESSAGE 2/3)

Messages use Telegram HTML entities (the notifier renders HTML). Extremely
long reports are split deterministically by the caller.
"""

from __future__ import annotations

import html
from typing import Any

from nexus_scalp.reporting.models import ReportContainer


def _esc(value: Any) -> str:
    """HTML-escape for Telegram parse_mode=HTML."""
    if value is None:
        return "n/a"
    return html.escape(str(value))


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f}"


def _fmt_pct(value: float | None, ndigits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{ndigits}f}%"


def _fmt_r(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}R"


def _fmt_ratio(value: float | None, ndigits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{ndigits}f}"


def _fmt_hold(sec: float | None) -> str:
    if sec is None:
        return "n/a"
    if sec >= 3600:
        return f"{sec / 3600:.1f}h"
    if sec >= 60:
        return f"{sec / 60:.0f}m"
    return f"{sec:.0f}s"


def _bar(value: float | None, width: int = 12) -> str:
    """Deterministic ASCII bar for a 0..1 fraction."""
    if value is None:
        return "·" * width
    v = max(0.0, min(1.0, float(value)))
    filled = round(v * width)
    return "█" * filled + "░" * (width - filled)


def format_telegram_daily(report: ReportContainer) -> str:
    """MESSAGE 1 — compact daily summary (fits in one Telegram message)."""
    p = report.performance
    acc = report.account
    r_s = report.r
    dd = report.drawdown
    health = report.health_score
    evidence = report.evidence

    lines = [
        "📊 <b>NEXUS DAILY PERFORMANCE INTELLIGENCE</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"📅 <b>Period:</b> <code>{_esc(report.period_start[:10])}</code> "
        f"→ <code>{_esc(report.period_end[:10])}</code> "
        f"(<i>{_esc(report.period_kind)}</i>)",
        f"🆔 <b>Report:</b> <code>{_esc(report.report_id)}</code>",
        "",
        "👤 <b>ACCOUNT</b>",
        f"💼 Balance: <code>{_fmt_usd(acc.balance)}</code> | "
        f"Equity: <code>{_fmt_usd(acc.equity)}</code>",
        f"📈 Floating: <code>{_fmt_usd(acc.floating_pnl)}</code> | "
        f"Realized: <code>{_fmt_usd(acc.realized_pnl)}</code>",
        f"📉 Drawdown: <code>{_fmt_pct(acc.drawdown_pct, 3)}</code> | "
        f"Free Margin: <code>{_fmt_usd(acc.available_margin)}</code>",
        "",
        "📊 <b>PERFORMANCE</b>",
        f"💼 Trades: <b>{p.trades}</b> | ✅ Wins: {p.wins} | ❌ Losses: {p.losses} "
        f"| 🟰 BE: {p.scratches}",
        f"🎯 Win Rate: <code>{_fmt_pct(p.win_rate)}</code> (decided) | "
        f"<code>{_fmt_pct(p.win_rate_all)}</code> (all)",
        f"💰 Net PnL: <b>{_fmt_usd(p.net_pnl)}</b> | PF: <code>{_fmt_ratio(p.profit_factor, 3)}</code>",
        f"🧮 Expectancy: <code>{_fmt_usd(p.expectancy)}</code>/trade | "
        f"Avg R: <code>{_fmt_r(r_s.average_r)}</code>",
        f"📊 Avg Win: <code>{_fmt_usd(p.average_win)}</code> | "
        f"Avg Loss: <code>{_fmt_usd(p.average_loss)}</code>",
        f"📐 Payoff: <code>{_fmt_ratio(p.payoff_ratio, 3)}</code> | "
        f"Median Trade: <code>{_fmt_usd(p.median_trade)}</code>",
        "",
        "🛡️ <b>RISK</b>",
        f"Avg Risk/Trade: <code>{_fmt_usd(report.risk.avg_risk_usd)}</code> | "
        f"Total Risk: <code>{_fmt_usd(report.risk.total_risk_deployed)}</code>",
        f"Avg MAE: <code>{_fmt_usd(report.excursion.avg_mae_usd)}</code> | "
        f"Avg MFE: <code>{_fmt_usd(report.excursion.avg_mfe_usd)}</code>",
        f"MFE Capture: <code>{_fmt_pct(report.excursion.mfe_capture_ratio * 100.0, 0) if report.excursion.mfe_capture_ratio is not None else 'n/a'}</code>",
        f"Max DD: <code>{_fmt_pct(dd.max_drawdown_pct, 3)}</code> | "
        f"Recovery: <code>{_fmt_pct(dd.recovery_pct, 0)}</code>",
        "",
        "⏱️ <b>HOLD / EXIT</b>",
        f"Avg Hold: <code>{_fmt_hold(report.holding.avg_hold_sec)}</code>",
        f"Best Exit: <code>{_best_exit(report)}</code> | "
        f"Worst Exit: <code>{_worst_exit(report)}</code>",
        f"Stop-Loss Share: <code>{_fmt_pct(p.stop_loss_share * 100.0, 0) if p.stop_loss_share is not None else 'n/a'}</code>",
        "",
        "🧠 <b>STRATEGY INTELLIGENCE</b>",
        *(_strategy_lines(report)),
        "",
        "🤖 <b>MODEL</b>",
        *(_model_lines(report)),
        "",
        "⚙️ <b>EXECUTION</b>",
        *(_execution_lines(report)),
        "",
        "📰 <b>NEWS</b>",
        *(_news_lines(report)),
        "",
        "🔻 <b>TOP LOSS DRIVER</b>",
        *(_loss_driver_lines(report)),
        "",
        "🔺 <b>TOP PROFIT DRIVER</b>",
        *(_profit_driver_lines(report)),
        "",
        "📈 <b>TREND</b>",
        _trend_line(report),
        "",
        "🏥 <b>ACCOUNT HEALTH</b>",
        f"<b>{health.total}/100</b>  "
        f"(Profit {health.profitability} | Risk {health.risk} | "
        f"Consist {health.consistency} | Exec {health.execution} | "
        f"StrStab {health.strategy_stability})",
        f"📊 Evidence: <code>{_esc(evidence)}</code>",
    ]
    return "\n".join(lines)


def format_deep_report(report: ReportContainer) -> str:
    """MESSAGE 2/3 — deep forensic intelligence (split-friendly)."""
    lines = [
        "🔬 <b>NEXUS DEEP PERFORMANCE INTELLIGENCE</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🆔 <b>Report:</b> <code>{_esc(report.report_id)}</code> | "
        f"Snapshot: <code>{_esc(report.snapshot_id)}</code>",
        f"🕒 Generated: <code>{_esc(report.generated_at)}</code>",
        "",
        "🧮 <b>DISTRIBUTION</b>",
        f"Avg Win: <code>{_fmt_usd(report.distribution.avg_win)}</code> | "
        f"Median Win: <code>{_fmt_usd(report.distribution.median_win)}</code>",
        f"Avg Loss: <code>{_fmt_usd(report.distribution.avg_loss)}</code> | "
        f"Median Loss: <code>{_fmt_usd(report.distribution.median_loss)}</code>",
        f"Payoff: <code>{_fmt_ratio(report.distribution.payoff_ratio, 3)}</code> | "
        f"PF: <code>{_fmt_ratio(report.distribution.profit_factor, 3)}</code>",
        "",
        "🎲 <b>R-MULTIPLE</b>",
        f"Avg R: <code>{_fmt_r(report.r.average_r)}</code> | "
        f"Median R: <code>{_fmt_r(report.r.median_r)}</code>",
        f"Win R: <code>{_fmt_r(report.r.win_avg_r)}</code> | "
        f"Loss R: <code>{_fmt_r(report.r.loss_avg_r)}</code>",
        f"Best R: <code>{_fmt_r(report.r.best_r)}</code> | "
        f"Worst R: <code>{_fmt_r(report.r.worst_r)}</code> | "
        f"R std: <code>{_fmt_pct(report.r.r_std, 3) if report.r.r_std is not None else 'n/a'}</code>",
        f"R Coverage: <code>{_fmt_pct(report.r.coverage_ratio * 100.0, 0) if report.r.coverage_ratio is not None else 'n/a'}</code> "
        f"(n={report.r.sample_count})",
        "",
        "🧗 <b>EXCURSION</b>",
        f"Avg MAE: <code>{_fmt_usd(report.excursion.avg_mae_usd)}</code> | "
        f"MAE(R): <code>{_fmt_r(report.excursion.avg_mae_r)}</code>",
        f"Avg MFE: <code>{_fmt_usd(report.excursion.avg_mfe_usd)}</code> | "
        f"MFE(R): <code>{_fmt_r(report.excursion.avg_mfe_r)}</code>",
        f"MFE Capture: <code>{_fmt_pct(report.excursion.mfe_capture_ratio * 100.0, 0) if report.excursion.mfe_capture_ratio is not None else 'n/a'}</code> | "
        f"Giveback: <code>{_fmt_usd(report.excursion.avg_giveback_usd)}</code>",
        "",
        "⏳ <b>HOLDING</b>",
        f"Avg Hold: <code>{_fmt_hold(report.holding.avg_hold_sec)}</code> | "
        f"Median: <code>{_fmt_hold(report.holding.median_hold_sec)}</code>",
        f"Win Hold: <code>{_fmt_hold(report.holding.win_hold_sec)}</code> | "
        f"Loss Hold: <code>{_fmt_hold(report.holding.loss_hold_sec)}</code>",
        "",
        "🚪 <b>EXITS</b>",
        *(_exit_lines(report)),
        "",
        "🔗 <b>STREAKS</b>",
        f"Max Win Streak: <code>{report.streaks.max_win_streak}</code> | "
        f"Max Loss Streak: <code>{report.streaks.max_loss_streak}</code> | "
        f"Current: <code>{report.streaks.current_streak_type} {report.streaks.current_streak}</code>",
        "",
        "📚 <b>STRATEGIES</b>",
        *(_deep_strategy_lines(report)),
        "",
        "🌡️ <b>REGIMES</b>",
        *(_regime_lines(report)),
        "",
        "🕐 <b>SESSIONS</b>",
        *(_session_lines(report)),
        "",
        "💊 <b>BEHAVIORAL</b>",
        *(_behavioral_lines(report)),
        "",
        "🔍 <b>ANOMALIES</b>",
        *(_anomaly_lines(report)),
        "",
        "🧭 <b>PERIOD COMPARE</b>",
        *(_compare_lines(report)),
        "",
        "💡 <b>INSIGHTS</b>",
        *(_insight_lines(report)),
        "",
        "🏥 <b>ACCOUNT HEALTH</b>",
        f"<b>{report.health_score.total}/100</b>",
        f"Profitability: {report.health_score.profitability}/25 | "
        f"Risk: {report.health_score.risk}/25 | "
        f"Consistency: {report.health_score.consistency}/25 | "
        f"Execution: {report.health_score.execution}/25 | "
        f"Strategy Stability: {report.health_score.strategy_stability}/25",
        *(f"📌 {_esc(r)}" for r in report.health_score.rationale),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section line builders
# ---------------------------------------------------------------------------


def _best_exit(report: ReportContainer) -> str:
    if not report.exits:
        return "n/a"
    positive = [e for e in report.exits if e.net_pnl > 0]
    if not positive:
        return "none profitable"
    best = max(positive, key=lambda e: e.net_pnl)
    return f"{best.exit_type} ({_fmt_usd(best.net_pnl)})"


def _worst_exit(report: ReportContainer) -> str:
    if not report.exits:
        return "n/a"
    negative = [e for e in report.exits if e.net_pnl < 0]
    if not negative:
        return "none losing"
    worst = min(negative, key=lambda e: e.net_pnl)
    return f"{worst.exit_type} ({_fmt_usd(worst.net_pnl)})"


def _strategy_lines(report: ReportContainer) -> list[str]:
    if not report.strategies:
        return ["n/a"]
    ranked = sorted(
        [s for s in report.strategies if s.evidence != "DO_NOT_RANK"],
        key=lambda s: s.expectancy or -1e9,
        reverse=True,
    )
    if not ranked:
        return [f"n/a ({len(report.strategies)} strategies below sample floor)"]
    best = ranked[0]
    worst = ranked[-1]
    lines = [
        f"✅ Best: <code>{_esc(best.strategy_id[:24])}</code> "
        f"exp={_fmt_usd(best.expectancy)} n={best.trades} [{_esc(best.evidence)}]",
        f"❌ Worst: <code>{_esc(worst.strategy_id[:24])}</code> "
        f"exp={_fmt_usd(worst.expectancy)} n={worst.trades} [{_esc(worst.evidence)}]",
    ]
    if len(ranked) > 2:
        lines.append(f"📌 {len(report.strategies)} strategies tracked this period.")
    return lines


def _model_lines(report: ReportContainer) -> list[str]:
    m = report.model
    if not m.has_data or m.prediction_count == 0:
        return ["n/a (no prediction data in period)"]
    lines = [
        f"Avg BUY: <code>{_fmt_pct((m.avg_buy_probability or 0.0) * 100.0, 1)}</code> | "
        f"Avg SELL: <code>{_fmt_pct((m.avg_sell_probability or 0.0) * 100.0, 1)}</code> | "
        f"Avg NO_TRADE: <code>{_fmt_pct((m.avg_no_trade_probability or 0.0) * 100.0, 1)}</code>",
        f"Executed signal ratio: <code>{_fmt_pct((m.prediction_to_execution_rate or 0.0) * 100.0, 1)}</code> "
        f"({m.executed_count} executed / {m.prediction_count} predictions)",
        f"Decisions: 🤖 model_rej={m.model_rejected} | 🧾 policy_rej={m.policy_rejected} | "
        f"🛡️ risk_rej={m.risk_rejected} | ⛔ exposure={m.exposure_blocked} | "
        f"💥 exec_fail={m.execution_failed} | ✅ executed={m.trade_executed}",
    ]
    return lines


def _execution_lines(report: ReportContainer) -> list[str]:
    e = report.execution
    if not e.has_data:
        return ["n/a (no execution rows in period)"]
    return [
        f"Fill Rate: <code>{_fmt_pct((e.fill_ratio or 0.0) * 100.0, 0)}</code> | "
        f"Avg Latency: <code>{(e.avg_latency_sec or 0.0) * 1000:.0f} ms</code> | "
        f"Worst: <code>{(e.worst_latency_sec or 0.0) * 1000:.0f} ms</code>",
        f"Rejections: <code>{e.rejection_count}</code> | "
        f"Cancellations: <code>{e.cancellation_count}</code> | "
        f"n={e.sample_count}",
    ]


def _news_lines(report: ReportContainer) -> list[str]:
    n = report.news
    if not n.has_data:
        return ["n/a (no news provenance recorded)"]
    return [
        f"News-active: <code>{n.news_active_trades}</code> trades / "
        f"PnL <code>{_fmt_usd(n.news_active_pnl)}</code> | "
        f"High-impact: {n.high_impact_trades}",
        f"Non-news: <code>{n.news_inactive_trades}</code> trades / "
        f"PnL <code>{_fmt_usd(n.news_inactive_pnl)}</code>",
    ]


def _loss_driver_lines(report: ReportContainer) -> list[str]:
    ld = report.loss_drivers
    if not ld.has_data:
        return ["n/a (no losing trades)"]
    lines = [
        f"🔻 <code>{_esc(ld.largest_driver[:30])}</code>: "
        f"{ld.largest_driver_trades} trades / "
        f"<code>-{_fmt_usd(ld.largest_driver_loss)}</code>",
    ]
    for d in ld.drivers[1:4]:
        lines.append(
            f"  • {_esc(d['driver'][:26])}: {d['trades']} / "
            f"<code>-{_fmt_usd(d['total_loss'])}</code>"
        )
    return lines


def _profit_driver_lines(report: ReportContainer) -> list[str]:
    pd = report.profit_drivers
    if not pd.has_data:
        return ["n/a (no winning trades)"]
    lines = [
        f"🔺 <code>{_esc(pd.best_driver[:30])}</code>: "
        f"{pd.best_driver_trades} trades / "
        f"<code>+{_fmt_usd(pd.best_driver_profit)}</code>",
    ]
    for d in pd.drivers[1:4]:
        lines.append(
            f"  • {_esc(d['driver'][:26])}: {d['trades']} / "
            f"<code>+{_fmt_usd(d['total_profit'])}</code>"
        )
    return lines


def _trend_line(report: ReportContainer) -> str:
    trend = report.trend
    emoji = {"IMPROVING": "📈", "STABLE": "-", "DETERIORATING": "📉", "INSUFFICIENT_DATA": "❓"}
    label = {
        "IMPROVING": "IMPROVING",
        "STABLE": "STABLE",
        "DETERIORATING": "DETERIORATING",
        "INSUFFICIENT_DATA": "INSUFFICIENT DATA",
    }
    return f"{emoji.get(trend, '❓')} <b>{label.get(trend, trend)}</b>" + (
        f" (PnL Δ {_fmt_usd(report.period_compare.pnl_delta)} | "
        f"WinRate Δ {_fmt_pct(report.period_compare.win_rate_delta)} | "
        f"Expectancy Δ {_fmt_usd(report.period_compare.expectancy_delta)})"
        if report.period_compare.has_data
        else ""
    )


def _exit_lines(report: ReportContainer) -> list[str]:
    if not report.exits:
        return ["n/a"]
    out = []
    for e in report.exits:
        out.append(
            f"• <code>{_esc(e.exit_type)}</code>: {e.count} | "
            f"PnL <code>{_fmt_usd(e.net_pnl)}</code> | "
            f"WR <code>{_fmt_pct(e.win_rate, 0)}</code> | "
            f"R <code>{_fmt_r(e.average_r)}</code>"
        )
    return out


def _deep_strategy_lines(report: ReportContainer) -> list[str]:
    if not report.strategies:
        return ["n/a"]
    out = []
    for s in report.strategies[:8]:
        out.append(
            f"• <code>{_esc(s.strategy_id[:26])}</code>: n={s.trades} "
            f"wins={s.wins} | PnL <code>{_fmt_usd(s.net_pnl)}</code> | "
            f"exp <code>{_fmt_usd(s.expectancy)}</code> | "
            f"R <code>{_fmt_r(s.average_r)}</code> | [{_esc(s.evidence)}]"
        )
    return out


def _regime_lines(report: ReportContainer) -> list[str]:
    if not report.regimes:
        return ["n/a"]
    out = []
    for r in report.regimes[:8]:
        out.append(
            f"• <code>{_esc(r.regime[:24])}</code>: n={r.trades} | "
            f"PnL <code>{_fmt_usd(r.net_pnl)}</code> | "
            f"exp <code>{_fmt_usd(r.expectancy)}</code> | "
            f"R <code>{_fmt_r(r.average_r)}</code> | [{_esc(r.evidence)}]"
        )
    return out


def _session_lines(report: ReportContainer) -> list[str]:
    if not report.sessions:
        return ["n/a"]
    out = []
    for s in report.sessions:
        out.append(
            f"• <code>{_esc(s.session)}</code>: n={s.trades} | "
            f"PnL <code>{_fmt_usd(s.net_pnl)}</code> | "
            f"WR <code>{_fmt_pct(s.win_rate, 0)}</code> | "
            f"exp <code>{_fmt_usd(s.expectancy)}</code> | "
            f"R <code>{_fmt_r(s.average_r)}</code>"
        )
    return out


def _behavioral_lines(report: ReportContainer) -> list[str]:
    b = report.behavioral
    if not b.has_data:
        return ["n/a (no behavioral flags recorded)"]
    out = [f"Total flags: {b.total_flags} across {b.flagged_trades} trade(s)"]
    for key, count in sorted(b.flag_counts.items(), key=lambda kv: -kv[1])[:8]:
        out.append(f"• <code>{_esc(key)}</code>: {count}")
    return out


def _anomaly_lines(report: ReportContainer) -> list[str]:
    if not report.anomalies:
        return ["none detected"]
    out = []
    for a in report.anomalies:
        sev = {"WARNING": "⚠️", "INFO": "📌"}.get(a.severity, "📌")
        out.append(f"{sev} <code>{_esc(a.anomaly_type)}</code>: {_esc(a.detail)}")
    return out


def _compare_lines(report: ReportContainer) -> list[str]:
    c = report.period_compare
    if not c.has_data:
        return [f"n/a (no previous {report.period_kind} with data)"]
    return [
        f"{_esc(c.current_label)} vs {_esc(c.previous_label)}",
        f"PnL: <code>{_fmt_usd(c.pnl_delta)}</code> | "
        f"WinRate: <code>{_fmt_pct(c.win_rate_delta)}</code> | "
        f"Expectancy: <code>{_fmt_usd(c.expectancy_delta)}</code>",
        f"AvgR: <code>{_fmt_r(c.average_r_delta)}</code> | "
        f"Trades: <code>{c.trade_count_delta:+d}</code> ({c.current_trades} vs {c.previous_trades})",
    ]


def _insight_lines(report: ReportContainer) -> list[str]:
    if not report.insights:
        return ["n/a"]
    out = []
    for i in report.insights:
        icon = {"WARNING": "⚠️", "POSITIVE": "✅", "SUMMARY": "📊", "INFO": "📌"}.get(i.kind, "📌")
        out.append(f"{icon} {_esc(i.text)}")
    return out
