"""
Insight / Anomaly / Health-Score Engine
========================================
Deterministic enrichment over the report contract. All thresholds are
documented constants; nothing here touches trading, RiskEngine or the model.

Evidence policy (task §17):
    <5 samples  -> DO_NOT_RANK
    5-19        -> LOW_EVIDENCE
    20-49       -> USABLE
    50+         -> STRONGER_EVIDENCE
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from nexus_scalp.reporting.models import (
    AnomalyItem,
    EvidenceLevel,
    HealthScoreSection,
    InsightItem,
    PeriodCompareSection,
    ReportContainer,
    TrendClassification,
)

# ---------------------------------------------------------------------------
# Sample-size policy (task §17 — never overridden by consumers)
# ---------------------------------------------------------------------------


def evidence_level(sample: int) -> str:
    """Maps a sample size to the repository evidence policy."""
    if sample <= 0:
        return EvidenceLevel.DO_NOT_RANK.value
    if sample < 5:
        return EvidenceLevel.DO_NOT_RANK.value
    if sample < 20:
        return EvidenceLevel.LOW_EVIDENCE.value
    if sample < 50:
        return EvidenceLevel.USABLE.value
    return EvidenceLevel.STRONGER_EVIDENCE.value


# ---------------------------------------------------------------------------
# Period comparison (task §11)
# ---------------------------------------------------------------------------


def compare_periods(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    current_label: str,
    previous_label: str,
) -> PeriodCompareSection:
    """Deltas between two canonical period dictionaries (PeriodReport.to_dict)."""
    if previous is None:
        return PeriodCompareSection(
            current_label=current_label,
            previous_label=previous_label,
            has_data=False,
        )

    def _d(key: str) -> tuple[float | None, float | None]:
        c = _num(current.get(key))
        p = _num(previous.get(key))
        return c, p

    pnl_c, pnl_p = _d("net_pnl")
    wr_c, wr_p = _d("win_rate")
    exp_c, exp_p = _d("expectancy")
    dd_c, dd_p = _d("max_drawdown_pct")
    r_c, r_p = _d("average_r")
    trades_c = int(current.get("total_trades") or 0)
    trades_p = int(previous.get("total_trades") or 0)

    return PeriodCompareSection(
        current_label=current_label,
        previous_label=previous_label,
        pnl_delta=_delta(pnl_c, pnl_p),
        win_rate_delta=_delta(wr_c, wr_p),
        expectancy_delta=_delta(exp_c, exp_p),
        drawdown_delta=_delta(dd_c, dd_p),
        average_r_delta=_delta(r_c, r_p),
        trade_count_delta=trades_c - trades_p,
        current_trades=trades_c,
        previous_trades=trades_p,
        has_data=True,
    )


def _num(value: Any) -> float | None:
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _delta(c: float | None, p: float | None) -> float | None:
    """c - p when both present and finite."""
    if c is None or p is None:
        return None
    return c - p


def classify_trend(compare: PeriodCompareSection | None) -> str:
    """
    Multi-metric trend classification (NEVER single-metric):

      IMPROVING     : pnl_delta >= 0 and expectancy_delta >= 0 and
                      (win_rate_delta >= -1.0 or average_r_delta >= 0)
      DETERIORATING : pnl_delta < 0 and expectancy_delta < 0 and
                      (win_rate_delta <= 1.0 or average_r_delta < 0)
      STABLE        : the rest (mixed signals or near-zero deltas)
    """
    if compare is None or not compare.has_data:
        return TrendClassification.INSUFFICIENT_DATA.value

    pnl = compare.pnl_delta
    exp = compare.expectancy_delta
    wr = compare.win_rate_delta
    r = compare.average_r_delta

    if pnl is None and exp is None:
        return TrendClassification.INSUFFICIENT_DATA.value

    # Both directions must agree: a positive PnL on a worse expectancy is
    # mixed (e.g. one outsized winner) -> STABLE, never IMPROVING.
    if (pnl is None or pnl >= 0.0) and (exp is None or exp >= 0.0):
        if (wr is None or wr >= -1.0) or (r is None or r >= 0.0):
            return TrendClassification.IMPROVING.value
    if (pnl is None or pnl < 0.0) and (exp is None or exp < 0.0):
        if (wr is None or wr <= 1.0) or (r is None or r < 0.0):
            return TrendClassification.DETERIORATING.value
    return TrendClassification.STABLE.value


# ---------------------------------------------------------------------------
# Anomaly detection (task §13) — MAD / percentile / count-based robust rules
# ---------------------------------------------------------------------------

#: Consecutive losses beyond median streak + this multiple are anomalous.
_LOSS_STREAK_MULT = 2.0
#: A single trade losing more than this share of total period losses.
_LOSS_SHARE_ANOMALY = 0.5
#: Expectancy degradation threshold (in currency) vs previous period.
_EXPECTANCY_DEGRADE_USD = -20.0
#: Execution latency robust threshold (seconds).
_LATENCY_ANOMALY_SEC = 0.05


def compute_anomalies(
    report: ReportContainer,
    prev_expectancy: float | None = None,
) -> list[AnomalyItem]:
    """Returns deterministic anomaly items; empty list when nothing fires."""
    out: list[AnomalyItem] = []
    perf = report.performance

    # 1. Abnormally long loss streak (percentile/median-based).
    if perf.losses >= 5:
        # No stored streak history here; use the observed max-loss streak from
        # the period vs the expected geometric balance. A streak longer than
        # losses/2 with >= 6 losses is unusual.
        if report.streaks.max_loss_streak >= 6 and perf.losses >= 6:
            out.append(
                AnomalyItem(
                    anomaly_type="ABNORMAL_LOSS_STREAK",
                    severity="WARNING",
                    detail=(
                        f"max loss streak {report.streaks.max_loss_streak} "
                        f"with {perf.losses} total losses"
                    ),
                    value=float(report.streaks.max_loss_streak),
                    threshold=6.0,
                )
            )

    # 2. Unusually large loss (share of period losses).
    worst = perf.worst_trade
    if worst is not None and worst < 0.0 and (perf.gross_loss or 0.0) > 0.0:
        share = abs(worst) / perf.gross_loss
        if share >= _LOSS_SHARE_ANOMALY:
            out.append(
                AnomalyItem(
                    anomaly_type="UNUSUALLY_LARGE_LOSS",
                    severity="WARNING",
                    detail=(
                        f"worst trade ${abs(worst):,.2f} = {share * 100:.1f}% of period losses"
                    ),
                    value=share,
                    threshold=_LOSS_SHARE_ANOMALY,
                )
            )

    # 3. Sudden expectancy degradation vs previous period.
    if prev_expectancy is not None:
        exp = perf.expectancy
        if exp is not None and (exp - prev_expectancy) <= _EXPECTANCY_DEGRADE_USD:
            out.append(
                AnomalyItem(
                    anomaly_type="EXPECTANCY_DEGRADATION",
                    severity="WARNING",
                    detail=(
                        f"expectancy {exp:,.2f} vs previous {prev_expectancy:,.2f} "
                        f"(delta {exp - prev_expectancy:,.2f})"
                    ),
                    value=exp - prev_expectancy,
                    threshold=_EXPECTANCY_DEGRADE_USD,
                )
            )

    # 4. Abnormal execution latency (robust: > 3x the median of the sample).
    exec_section = report.execution
    if exec_section.has_data and exec_section.sample_count >= 5:
        avg = exec_section.avg_latency_sec
        worst = exec_section.worst_latency_sec
        if avg is not None and worst is not None and worst > max(avg * 3.0, _LATENCY_ANOMALY_SEC):
            out.append(
                AnomalyItem(
                    anomaly_type="ABNORMAL_EXECUTION_LATENCY",
                    severity="WARNING",
                    detail=(
                        f"worst latency {worst:.3f}s vs avg {avg:.3f}s "
                        f"(n={exec_section.sample_count})"
                    ),
                    value=worst,
                    threshold=round(max(avg * 3.0, _LATENCY_ANOMALY_SEC), 4),
                )
            )

    # 5. Unusual strategy concentration (+80% of trades on one strategy).
    if report.strategies:
        total = sum(s.trades for s in report.strategies)
        if total >= 5:
            top = max(report.strategies, key=lambda s: s.trades)
            share = top.trades / total
            if share >= 0.8:
                out.append(
                    AnomalyItem(
                        anomaly_type="STRATEGY_CONCENTRATION",
                        severity="INFO",
                        detail=(f"{top.strategy_id} = {share * 100:.0f}% of {total} trades"),
                        value=share,
                        threshold=0.8,
                    )
                )

    return out


# ---------------------------------------------------------------------------
# Account health score (task §14) — deterministic, explainable, report-only
# ---------------------------------------------------------------------------

_HEALTH_COMPONENT_MAX = 25  # 4 components * 25 = 100


def compute_health_score(report: ReportContainer) -> HealthScoreSection:
    """Deterministic composite score (0-100). Analytics only: it never feeds
    back into trading, risk, or model thresholds."""

    perf = report.performance
    exec_section = report.execution
    strategies = report.strategies

    # --- Profitability (0-25) ---
    profitability = 0
    if perf.trades > 0:
        if (perf.profit_factor or 0.0) >= 1.5 and (perf.expectancy or 0.0) > 0.0:
            profitability = 25
        elif (perf.profit_factor or 0.0) >= 1.0:
            profitability = max(8, 12 if (perf.expectancy or 0.0) > 0.0 else 8)
        else:
            profitability = min(8, max(0, int((perf.win_rate or 0) // 10)))
    # net PnL positive adds a small bonus
    if (perf.net_pnl or 0.0) > 0.0:
        profitability = min(25, profitability + 2)

    # --- Risk (0-25) ---
    risk_component = 18  # baseline
    avg_loss = perf.average_loss or 0.0
    avg_win = perf.average_win or 0.0
    if perf.losses > 0 and perf.wins > 0:
        # Smaller avg loss relative to avg win = better.
        ratio = abs(avg_loss) / abs(avg_win) if avg_win != 0.0 else 3.0
        if ratio <= 0.8:
            risk_component = 22
        elif ratio <= 1.2:
            risk_component = 18
        elif ratio <= 2.0:
            risk_component = 12
        else:
            risk_component = 6
    # large stop-loss share on losers is a risk positive (stops doing the work)
    if (perf.stop_loss_share or 0.0) >= 0.7:
        risk_component = min(25, risk_component + 3)
    # drawdown penalizes
    dd = report.drawdown.max_drawdown_pct or 0.0
    if dd > 10.0:
        risk_component -= 8
    elif dd > 5.0:
        risk_component -= 4
    risk_component = max(0, min(25, risk_component))

    # --- Consistency (0-25) ---
    consistency = 10
    if perf.trades >= 5:
        r = report.r.average_r
        r_std = report.r.r_std
        if r is not None and r_std is not None and r_std > 0.0:
            # Higher |avg R| / std = more consistent edge.
            stability = abs(r) / r_std
            if stability >= 0.5:
                consistency = 20
            elif stability >= 0.25:
                consistency = 15
            elif stability >= 0.1:
                consistency = 12
            else:
                consistency = 6
        # Win-rate stability proxy: closer to 50% with positive expectancy is
        # fine; extreme rates (>= 80%) with negative R distribution are fragile.
        wr = perf.win_rate or 0.0
        if wr >= 80.0 and (report.r.average_r or 0.0) <= 0.0:
            consistency = min(consistency, 5)
        if perf.trades >= 20:
            consistency = min(25, consistency + 2)
    else:
        consistency = 5

    # --- Execution (0-25) ---
    execution = 15
    if exec_section.has_data:
        fill = exec_section.fill_ratio
        if fill is not None:
            execution = round(fill * 25)
        if exec_section.sample_count >= 5:
            avg_lat = exec_section.avg_latency_sec or 0.0
            if avg_lat <= 0.02:
                execution = min(25, execution + 4)
            elif avg_lat >= 0.1:
                execution = max(0, execution - 4)
        if exec_section.rejection_count > 0:
            execution = max(0, execution - int(exec_section.rejection_count))
    execution = max(0, min(25, execution))

    # --- Strategy stability (0-25) ---
    stability = 12
    if len(strategies) >= 2:
        # Evidence-weighted: if the top strategy is stable (has USABLE+
        # evidence and positive expectancy) bump the score.
        top = max(strategies, key=lambda s: s.trades)
        if top.evidence in (EvidenceLevel.USABLE.value, EvidenceLevel.STRONGER_EVIDENCE.value):
            stability += 4
        if (top.expectancy or 0.0) > 0.0:
            stability += 3
        # concentration risk
        total = sum(s.trades for s in strategies)
        if total > 0 and (top.trades / total) > 0.8:
            stability -= 5
    elif len(strategies) == 1:
        stability = 10
    stability = max(0, min(25, stability))

    total = profitability + risk_component + consistency + execution + stability
    total = max(0, min(100, total))

    rationale = [
        f"profitability={profitability} (PF={_fmt(perf.profit_factor)}, "
        f"expectancy={_fmt(perf.expectancy)})",
        f"risk={risk_component} (avg_loss={_fmt(abs(avg_loss))}, stop_share={_fmt(perf.stop_loss_share)})",
        f"consistency={consistency} (avgR={_fmt(report.r.average_r)}, Rstd={_fmt(report.r.r_std)})",
        f"execution={execution} (fill={_fmt(exec_section.fill_ratio)}, "
        f"latency={_fmt(exec_section.avg_latency_sec)}s)",
        f"strategy_stability={stability} (strategies={len(strategies)})",
    ]

    return HealthScoreSection(
        total=total,
        profitability=profitability,
        risk=risk_component,
        consistency=consistency,
        execution=execution,
        strategy_stability=stability,
        rationale=rationale,
    )


def _fmt(value: float | None, ndigits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{ndigits}f}"


# ---------------------------------------------------------------------------
# Smart summary sentences (task §16) — deterministic, metric-grounded
# ---------------------------------------------------------------------------


def generate_insights(report: ReportContainer) -> list[InsightItem]:
    """13 deterministic analytical sentences grounded in actual metrics."""
    out: list[InsightItem] = []
    perf = report.performance
    r_section = report.r
    exc = report.excursion
    strategies = report.strategies
    regimes = report.regimes
    exits = report.exits
    model = report.model

    # 1. PnL / performance headline
    if perf.trades > 0:
        pnl = perf.net_pnl or 0.0
        direction = "profit" if pnl >= 0 else "loss"
        out.append(
            InsightItem(
                f"The period closed with {abs(pnl):,.2f} USD of realized {direction} "
                f"across {perf.trades} trades ({perf.wins}W/{perf.losses}L/"
                f"{perf.scratches}BE).",
                "SUMMARY",
            )
        )

    # 2. Win-rate vs expectancy nuance
    if perf.win_rate is not None and perf.expectancy is not None:
        if (perf.win_rate or 0.0) >= 50 and perf.expectancy < 0:
            out.append(
                InsightItem(
                    f"A {perf.win_rate:.1f}% win rate still produced negative "
                    f"expectancy ({perf.expectancy:,.2f}) — average loss "
                    f"({abs(perf.average_loss or 0):,.2f}) exceeds average win "
                    f"({perf.average_win or 0:,.2f}).",
                    "WARNING",
                )
            )
        elif (perf.win_rate or 0.0) < 50 and perf.expectancy > 0:
            out.append(
                InsightItem(
                    f"Low win rate ({perf.win_rate:.1f}%) with positive expectancy "
                    f"({perf.expectancy:,.2f}) — the payoff ratio is carrying the book.",
                    "INFO",
                )
            )

    # 3. R-multiple asymmetry
    if r_section.average_r is not None:
        avg_r = r_section.average_r
        win_r = r_section.win_avg_r
        loss_r = r_section.loss_avg_r
        if avg_r < 0 and win_r is not None and loss_r is not None:
            out.append(
                InsightItem(
                    f"Average R is negative ({avg_r:+.2f}R) because average loss "
                    f"({loss_r:+.2f}R) is materially larger than average win "
                    f"({win_r:+.2f}R).",
                    "WARNING",
                )
            )
        elif avg_r > 0 and win_r is not None and loss_r is not None:
            out.append(
                InsightItem(
                    f"Average R {avg_r:+.2f} (win {win_r:+.2f}R / loss {loss_r:+.2f}R) "
                    f"— the risk model is capturing more than it burns.",
                    "POSITIVE",
                )
            )

    # 4. MFE capture
    if exc.mfe_capture_ratio is not None:
        cap = exc.mfe_capture_ratio
        if cap < 0.35:
            out.append(
                InsightItem(
                    f"MFE capture is low ({cap * 100:.0f}%) — significant profit was "
                    f"left on the table after peak favourable excursion.",
                    "WARNING",
                )
            )
        elif cap > 0.65:
            out.append(
                InsightItem(
                    f"Strong MFE capture ({cap * 100:.0f}%) — exits are harvesting "
                    f"the favourable excursion.",
                    "POSITIVE",
                )
            )
        else:
            out.append(
                InsightItem(
                    f"MFE capture {cap * 100:.0f}% of peak favourable excursion — "
                    f"moderate profit retention.",
                    "INFO",
                )
            )
    elif exc.sample_count > 0 and exc.avg_mfe_usd is not None and exc.avg_mfe_usd > 0.0:
        # BUG-081: MFE exists but no aggregate capture computed (e.g. mixed
        # realized sign) — report the evidence instead of staying silent.
        out.append(
            InsightItem(
                f"Average MFE ${exc.avg_mfe_usd:,.2f} across {exc.sample_count} trades "
                f"(capture not computable from the aggregate).",
                "INFO",
            )
        )

    # 5. Exit-type leakage
    if exits:
        stop = next((e for e in exits if e.exit_type == "INITIAL_STOP"), None)
        tp = next((e for e in exits if e.exit_type == "TAKE_PROFIT"), None)
        if stop is not None and stop.count >= 3:
            out.append(
                InsightItem(
                    f"{stop.count} trades stopped out at the initial stop "
                    f"({stop.net_pnl:,.2f} USD) — the dominant exit mechanism.",
                    "INFO",
                )
            )
        if tp is not None and tp.net_pnl > 0:
            out.append(
                InsightItem(
                    f"Take-profit exits contributed {tp.net_pnl:,.2f} USD "
                    f"across {tp.count} trades.",
                    "POSITIVE",
                )
            )

    # 6. Hold-time insight
    if report.holding.avg_hold_sec is not None:
        mins = report.holding.avg_hold_sec / 60.0
        if mins >= 120:
            out.append(
                InsightItem(
                    f"Average hold {mins:.0f} min — longer than a scalping profile; "
                    f"check strategy expectations vs actual horizon.",
                    "INFO",
                )
            )
        elif mins < 15:
            out.append(
                InsightItem(
                    f"Average hold {mins:.1f} min — rapid turnover consistent with scalping.",
                    "INFO",
                )
            )

    # 7. Strategy intelligence (evidence-gated)
    if strategies:
        ranked = [s for s in strategies if s.evidence != "DO_NOT_RANK"]
        if ranked:
            best = max(ranked, key=lambda s: s.expectancy or -1e9)
            worst = min(ranked, key=lambda s: s.expectancy or 1e9)
            if (best.expectancy or 0.0) > 0:
                out.append(
                    InsightItem(
                        f"Strongest strategy: {best.strategy_id} "
                        f"(n={best.trades}, expectancy {best.expectancy:,.2f}, "
                        f"evidence={best.evidence}).",
                        "POSITIVE",
                    )
                )
            if (worst.expectancy or 0.0) < 0 and worst.strategy_id != best.strategy_id:
                out.append(
                    InsightItem(
                        f"Weakest strategy: {worst.strategy_id} "
                        f"(n={worst.trades}, expectancy {worst.expectancy:,.2f}, "
                        f"evidence={worst.evidence}).",
                        "WARNING",
                    )
                )
        else:
            out.append(
                InsightItem(
                    "Strategy ranking skipped: no strategy reached the minimum sample size (n>=5).",
                    "INFO",
                )
            )

    # 8. Regime intelligence
    if regimes:
        r_ranked = [r for r in regimes if r.evidence != "DO_NOT_RANK"]
        if r_ranked:
            r_best = max(r_ranked, key=lambda r: r.expectancy or -1e9)
            r_worst = min(r_ranked, key=lambda r: r.expectancy or 1e9)
            out.append(
                InsightItem(
                    f"Best regime: {r_best.regime} (n={r_best.trades}, "
                    f"expectancy {r_best.expectancy:,.2f}); worst: {r_worst.regime} "
                    f"(n={r_worst.trades}, expectancy {r_worst.expectancy:,.2f}).",
                    "INFO",
                )
            )
        else:
            out.append(
                InsightItem(
                    "Regime comparison skipped: no regime reached the minimum sample size.",
                    "INFO",
                )
            )

    # 9. Session insight
    if report.sessions:
        s_ranked = [s for s in report.sessions if s.trades >= 5]
        if s_ranked:
            s_best = max(s_ranked, key=lambda s: s.expectancy or -1e9)
            out.append(
                InsightItem(
                    f"Best session: {s_best.session} (n={s_best.trades}, PnL {s_best.net_pnl:,.2f}).",
                    "INFO",
                )
            )

    # 10. Cost drag
    if perf.total_costs and perf.total_costs > 0:
        share = perf.cost_drag_pct
        if share is not None and perf.trades > 0:
            out.append(
                InsightItem(
                    f"Trading costs {perf.total_costs:,.2f} USD "
                    f"({share:.1f}% of gross) — {'material' if share > 10 else 'modest'} "
                    f"drag on the book.",
                    "INFO",
                )
            )

    # 11. Model / decision funnel
    if model.has_data and model.prediction_count > 0:
        exec_rate = model.prediction_to_execution_rate
        if exec_rate is not None:
            out.append(
                InsightItem(
                    f"Model funnel: {model.prediction_count} predictions -> "
                    f"{exec_rate * 100:.1f}% executed ({model.trade_executed} trades)."
                    if exec_rate
                    else f"Model funnel: {model.prediction_count} predictions, none executed.",
                    "INFO",
                )
            )

    # 12. Execution quality
    if report.execution.has_data:
        avg_lat = report.execution.avg_latency_sec
        rej = report.execution.rejection_count
        if avg_lat is not None:
            out.append(
                InsightItem(
                    f"Execution: avg latency {avg_lat * 1000:.0f} ms, {rej} rejection(s).",
                    "INFO",
                )
            )

    # 13. Drawdown / recovery
    if report.drawdown.has_data:
        dd = report.drawdown.current_drawdown_pct
        if dd and dd > 0:
            out.append(
                InsightItem(
                    f"Currently {dd:.2f}% below peak equity "
                    f"(max drawdown {report.drawdown.max_drawdown_pct or 0:.2f}%).",
                    "WARNING",
                )
            )
        elif report.performance.trades > 0:
            out.append(
                InsightItem(
                    "No current drawdown — equity at/near peak.",
                    "POSITIVE",
                )
            )

    # Cap at 13 sentences (deterministic order preserved).
    return out[:13]


# ---------------------------------------------------------------------------
# Deterministic report/snapshot IDs (task §20)
# ---------------------------------------------------------------------------


def make_report_id(period_key: str, generated_at: datetime) -> str:
    """Stable report id: `report-<period_key>-<yyyymmddhhmmss>`."""
    return f"report-{period_key}-{generated_at.strftime('%Y%m%d%H%M%S')}"


def make_snapshot_id(period_key: str, generated_at: datetime) -> str:
    """Stable snapshot id: `snap-<period_key>-<yyyymmddhhmmss>`."""
    return f"snap-{period_key}-{generated_at.strftime('%Y%m%d%H%M%S')}"


# ---------------------------------------------------------------------------
# Session classification (task §5) — deterministic UTC buckets
# ---------------------------------------------------------------------------


def classify_session(utc_hour: int) -> str:
    """Classify a UTC hour into the canonical trading sessions.

    Sessions (UTC, approximate, deterministic for reporting):
        ASIAN / TOKYO : 00:00-07:59
        LONDON        : 08:00-12:59
        LONDON_NY     : 13:00-16:59 (overlap)
        NEW_YORK      : 17:00-21:59
        OFF_HOURS     : 22:00-23:59
    """
    if 0 <= utc_hour < 8:
        return "ASIAN_TOKYO"
    if 8 <= utc_hour < 13:
        return "LONDON"
    if 13 <= utc_hour < 17:
        return "LONDON_NY_OVERLAP"
    if 17 <= utc_hour < 22:
        return "NEW_YORK"
    return "OFF_HOURS"
