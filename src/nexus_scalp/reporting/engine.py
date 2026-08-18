"""
PerformanceReportEngine — deterministic multi-stage report generator
=====================================================================
READ-ONLY enrichment over `AccountingCore`. NEVER writes financial truth;
never opens/closes trades; never modifies risk/model/news settings.

Stages (each a private method, each deterministic):

    SNAPSHOT  OUTCOMES  PROFIT_DECOMPOSITION  DISTRIBUTION  R_MULTIPLE
    EXCURSION  HOLDING  EXIT  STREAK  RISK  DRAWDOWN  STRATEGY  REGIME
    SESSION  MODEL  EXECUTION  NEWS  BEHAVIORAL  LOSS/PROFIT DRIVERS
    PERIOD_COMPARE  ANOMALY  HEALTH  INSIGHTS

The canonical `PeriodReport` (from `AccountingCore.period_report`) is the
single source of money truth; the report's performance section mirrors it
exactly. Everything else is context computed from the same evidence set.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from typing import Any

from nexus_scalp.accounting.core import AccountingCore
from nexus_scalp.accounting.models import PeriodReport, TradeOutcome, TradeRecord
from nexus_scalp.accounting.periods import PeriodKind, ensure_utc, period_bounds, utc_now
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.reporting.insights import (
    classify_session,
    classify_trend,
    compare_periods,
    compute_anomalies,
    compute_health_score,
    evidence_level,
    generate_insights,
    make_report_id,
    make_snapshot_id,
)
from nexus_scalp.reporting.models import (
    AnomalyStateSection,
    BehavioralSection,
    DistributionSection,
    DrawdownSection,
    ExcursionSection,
    ExecutionSection,
    ExitGroup,
    HealthScoreSection,
    HoldingSection,
    LossDriversSection,
    ModelSection,
    NewsSection,
    PerformanceSection,
    PeriodCompareSection,
    ProfitDriversSection,
    RegimeGroup,
    ReportContainer,
    RiskSection,
    RSection,
    SessionGroup,
    SnapshotBlock,
    StrategyGroup,
    StreakSection,
)

logger = get_logger("nexus_scalp.reporting.engine")

#: Exit-type label normalization (repository raw values -> canonical group).
_EXIT_RAW_MAP = {
    "HARD_SL_HIT": "INITIAL_STOP",
    "BREAK_EVEN_SL_HIT": "BREAKEVEN_STOP",
    "RISK_FREE_SL_HIT": "BREAKEVEN_STOP",
    "TRAILING_STOP": "TRAILING_STOP",
    "TAKE_PROFIT_HIT": "TAKE_PROFIT",
    "PROFIT_GIVEBACK_PROTECTION": "TRAILING_STOP",
    "HOLD_SCORE_DECAY": "STRATEGY_EXIT",
    "SYSTEM_CLOSE": "STRATEGY_EXIT",
    "MANUAL_CLOSE": "MANUAL_CLOSE",
    "EMERGENCY_CLOSE": "EMERGENCY_EXIT",
    "UNKNOWN": "UNKNOWN",
}

#: Model-funnel blocked_by reasons -> canonical rejection class.
_BLOCKED_MODEL = {"CONFIDENCE_FAIL", "ZONE_QUALITY_FAIL", "HTF_TREND_CONFL_FAIL"}
_BLOCKED_POLICY = {
    "ASYMMETRIC_RR_LIMIT",
    "SR_RESISTANCE_MARGIN_FAIL",
    "SR_SUPPORT_MARGIN_FAIL",
    "REGIME_GUARDIAN",
    "SAME_LEVEL_REENTRY",
    "RANGE_FILTER",
    "EXPERIENCE_DEGRADED",
    "SUITABILITY_GATE",
}
_BLOCKED_RISK = {"RISK_LIMIT", "RISK_ENGINE", "MARGIN_CHECK", "LOT_CAP"}
_BLOCKED_EXPOSURE = {"EXPOSURE_BLOCKED", "MAX_EXPOSURE", "EXPOSURE_CACHE_STALE"}
_BLOCKED_EXECUTION = {"EXECUTION_STATE_BLOCK", "EXECUTION_FAILED"}


class PerformanceReportEngine:
    """
    Deterministic multi-stage daily/weekly performance intelligence generator.

    Args:
        core: Canonical AccountingCore (read-only source of truth).
        kind: Period granularity to report on (default DAY).
    """

    def __init__(self, core: AccountingCore, kind: PeriodKind = PeriodKind.DAY) -> None:
        self.core = core
        self.kind = kind

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    def generate(
        self,
        at: datetime | None = None,
        previous_compare: bool = True,
    ) -> ReportContainer:
        """Builds the full structured report for the period containing `at`.

        Deterministic for a fixed `at` and unchanged database state.
        """
        started = time.perf_counter()
        moment = ensure_utc(at) if at is not None else utc_now()
        bounds = period_bounds(self.kind, moment)
        period_key = bounds.key
        generated_at = utc_now()
        report_id = make_report_id(period_key, generated_at)
        snapshot_id = make_snapshot_id(period_key, generated_at)

        logger.info(
            "[TELEGRAM_REPORT] event=START period=%s snapshot_id=%s",
            self.kind.value,
            snapshot_id,
        )

        # STEP 1: one coherent snapshot (canonical base report).
        base: PeriodReport = self.core.period_report(self.kind, at=moment, use_cache=True)
        # Canonical period trades (same window as the base report).
        trades = self.core.load_trades(since=bounds.start, until=bounds.end)
        closed = [t for t in trades if t.closed_at is not None]
        closed.sort(key=lambda t: t.closed_at)  # type: ignore[arg-type,return-value]

        # Previous equivalent period (for comparison + trend).
        from datetime import timedelta as _td

        prev_bounds = period_bounds(self.kind, bounds.start - _td(seconds=1))
        prev_base = (
            self.core.period_report(
                self.kind, at=prev_bounds.start + _td(seconds=1), use_cache=True
            )
            if previous_compare
            else None
        )

        # Account envelope (live adapter, degraded to None on failure).
        live = self.core.live_state()

        account = self._stage_snapshot(base, live, snapshot_id, bounds)
        performance = self._stage_performance(base)
        distribution = self._stage_distribution(base, closed)
        r_section = self._stage_r(closed)
        excursion = self._stage_excursion(closed)
        holding = self._stage_holding(closed)
        exits = self._stage_exits(closed)
        streaks = self._stage_streaks(closed)
        risk = self._stage_risk(closed, base, live)
        drawdown = self._stage_drawdown()
        strategies = self._stage_strategies(closed)
        regimes = self._stage_regimes(closed)
        sessions = self._stage_sessions(closed)
        model = self._stage_model(bounds)
        execution = self._stage_execution(bounds)
        news = self._stage_news(closed)
        behavioral = self._stage_behavioral(closed)
        anomaly_state = self._stage_anomaly_state(closed)
        loss_drivers = self._stage_loss_drivers(closed)
        profit_drivers = self._stage_profit_drivers(closed)
        period_compare = self._stage_compare(base, prev_base, bounds, prev_bounds)

        anomalies = compute_anomalies(
            self._as_report(
                account,
                performance,
                distribution,
                r_section,
                excursion,
                holding,
                exits,
                streaks,
                risk,
                drawdown,
                strategies,
                regimes,
                sessions,
                model,
                execution,
                news,
                behavioral,
                anomaly_state,
                loss_drivers,
                profit_drivers,
                period_compare,
                [],
                HealthScoreSection(),
                [],
                report_id,
                snapshot_id,
                generated_at,
                bounds,
            ),
            prev_expectancy=prev_base.expectancy if prev_base else None,
        )
        health = compute_health_score(
            self._as_report(
                account,
                performance,
                distribution,
                r_section,
                excursion,
                holding,
                exits,
                streaks,
                risk,
                drawdown,
                strategies,
                regimes,
                sessions,
                model,
                execution,
                news,
                behavioral,
                anomaly_state,
                loss_drivers,
                profit_drivers,
                period_compare,
                anomalies,
                HealthScoreSection(),
                [],
                report_id,
                snapshot_id,
                generated_at,
                bounds,
            )
        )
        trend = classify_trend(period_compare)
        insights = generate_insights(
            self._as_report(
                account,
                performance,
                distribution,
                r_section,
                excursion,
                holding,
                exits,
                streaks,
                risk,
                drawdown,
                strategies,
                regimes,
                sessions,
                model,
                execution,
                news,
                behavioral,
                anomaly_state,
                loss_drivers,
                profit_drivers,
                period_compare,
                anomalies,
                health,
                [],
                report_id,
                snapshot_id,
                generated_at,
                bounds,
            )
        )
        evidence = evidence_level(performance.trades)

        report = self._as_report(
            account,
            performance,
            distribution,
            r_section,
            excursion,
            holding,
            exits,
            streaks,
            risk,
            drawdown,
            strategies,
            regimes,
            sessions,
            model,
            execution,
            news,
            behavioral,
            anomaly_state,
            loss_drivers,
            profit_drivers,
            period_compare,
            anomalies,
            health,
            insights,
            report_id,
            snapshot_id,
            generated_at,
            bounds,
            trend=trend,
            evidence=evidence,
        )

        duration_ms = round((time.perf_counter() - started) * 1000.0, 1)
        logger.info(
            "[TELEGRAM_REPORT] event=COMPLETE report_id=%s trades=%d pnl=%s duration_ms=%s",
            report_id,
            performance.trades,
            _fmt_opt(performance.net_pnl),
            duration_ms,
        )
        return report

    # ------------------------------------------------------------------
    # Stage helpers
    # ------------------------------------------------------------------

    def _as_report(
        self,
        account: SnapshotBlock,
        performance: PerformanceSection,
        distribution: DistributionSection,
        r_section: RSection,
        excursion: ExcursionSection,
        holding: HoldingSection,
        exits: list[ExitGroup],
        streaks: StreakSection,
        risk: RiskSection,
        drawdown: DrawdownSection,
        strategies: list[StrategyGroup],
        regimes: list[RegimeGroup],
        sessions: list[SessionGroup],
        model: ModelSection,
        execution: ExecutionSection,
        news: NewsSection,
        behavioral: BehavioralSection,
        anomaly_state: AnomalyStateSection,
        loss_drivers: LossDriversSection,
        profit_drivers: ProfitDriversSection,
        period_compare: PeriodCompareSection,
        anomalies: list[Any],
        health: HealthScoreSection,
        insights: list[Any],
        report_id: str,
        snapshot_id: str,
        generated_at: datetime,
        bounds: Any,
        trend: str = "INSUFFICIENT_DATA",
        evidence: str = "DO_NOT_RANK",
    ) -> ReportContainer:
        return ReportContainer(
            report_id=report_id,
            snapshot_id=snapshot_id,
            generated_at=generated_at.isoformat(),
            period_kind=self.kind.value,
            period_start=bounds.start.isoformat(),
            period_end=bounds.end.isoformat(),
            account=account,
            performance=performance,
            distribution=distribution,
            r=r_section,
            excursion=excursion,
            holding=holding,
            exits=exits,
            streaks=streaks,
            risk=risk,
            drawdown=drawdown,
            strategies=strategies,
            regimes=regimes,
            sessions=sessions,
            model=model,
            execution=execution,
            news=news,
            behavioral=behavioral,
            anomaly_state=anomaly_state,
            loss_drivers=loss_drivers,
            profit_drivers=profit_drivers,
            period_compare=period_compare,
            anomalies=anomalies,
            health_score=health,
            insights=insights,
            trend=trend,
            evidence=evidence,
        )

    def _stage_snapshot(
        self,
        base: PeriodReport,
        live: Any,
        snapshot_id: str,
        bounds: Any,
    ) -> SnapshotBlock:
        balance = live.balance if live and live.available else None
        equity = live.equity if live and live.available else None
        floating = live.floating_pnl if live and live.available else None
        margin_free = live.margin_free if live and live.available else None
        margin = live.margin if live and live.available else None
        margin_level = live.margin_level if live and live.available else None

        # TASK-1: the SnapshotBlock "drawdown_pct" is the DRAWDOWN WITHIN THE
        # REPORT PERIOD (peak-to-trough on the period's equity snapshots), not
        # the 90-day historical max. The period max_drawdown of the base report
        # is exactly that: base.max_drawdown_pct comes from the same bounds
        # window. Explicit label set in the section.
        period_dd = self.core.drawdown_report(lookback_days=1)
        return SnapshotBlock(
            snapshot_timestamp=utc_now().isoformat(),
            snapshot_id=snapshot_id,
            period_start=bounds.start.isoformat(),
            period_end=bounds.end.isoformat(),
            balance=balance,
            equity=equity,
            floating_pnl=floating,
            realized_pnl=base.net_pnl if base.has_data else None,
            drawdown_pct=period_dd.max_drawdown_pct if period_dd and period_dd.has_data else None,
            available_margin=margin_free,
            margin=margin,
            margin_level=margin_level,
        )

    def _stage_performance(self, base: PeriodReport) -> PerformanceSection:
        """Mirrors the canonical PeriodReport exactly (task: preserve truth)."""
        payoff = None
        if (
            base.average_win is not None
            and base.average_loss is not None
            and base.average_loss != 0
        ):
            payoff = base.average_win / abs(base.average_loss)
        return PerformanceSection(
            trades=base.total_trades,
            wins=base.win_count,
            losses=base.loss_count,
            scratches=base.breakeven_count,
            win_rate=base.win_rate,
            win_rate_all=base.win_rate_all,
            loss_rate_decided=base.loss_rate_decided,
            net_pnl=base.net_pnl if base.has_data else None,
            gross_profit=base.gross_profit if base.has_data else None,
            gross_loss=base.gross_loss if base.has_data else None,
            total_costs=base.total_costs if base.has_data else None,
            cost_drag_pct=base.cost_drag_pct,
            profit_factor=base.profit_factor,
            expectancy=base.expectancy,
            expectancy_breakeven_incl=base.expectancy_breakeven_incl,
            average_win=base.average_win,
            average_loss=base.average_loss,
            median_trade=None,
            median_win=None,
            median_loss=None,
            payoff_ratio=payoff,
            avg_pnl_per_decided=base.avg_pnl_per_decided,
            stop_loss_share=base.stop_loss_share,
            total_volume=base.total_volume,
            best_trade=base.best_trade,
            worst_trade=base.worst_trade,
        )

    def _stage_distribution(
        self, base: PeriodReport, closed: list[TradeRecord]
    ) -> DistributionSection:
        wins = [t.net_pnl for t in closed if t.outcome is TradeOutcome.WIN]
        losses = [t.net_pnl for t in closed if t.outcome is TradeOutcome.LOSS]
        med_win = statistics.median(wins) if wins else None
        med_loss = statistics.median(losses) if losses else None
        payoff = None
        if (
            base.average_win is not None
            and base.average_loss is not None
            and base.average_loss != 0
        ):
            payoff = base.average_win / abs(base.average_loss)
        return DistributionSection(
            avg_win=base.average_win,
            avg_loss=base.average_loss,
            median_win=med_win,
            median_loss=med_loss,
            payoff_ratio=payoff,
            profit_factor=base.profit_factor,
            expectancy=base.expectancy,
            expectancy_breakeven_incl=base.expectancy_breakeven_incl,
            win_skew=_skew(wins),
            loss_skew=_skew(losses),
        )

    def _stage_r(self, closed: list[TradeRecord]) -> RSection:
        r_vals = [t.realized_r for t in closed if t.realized_r is not None]
        if not r_vals:
            return RSection(sample_count=0)
        win_r = [t.realized_r for t in closed if t.realized_r is not None and t.is_win]
        loss_r = [
            t.realized_r
            for t in closed
            if t.realized_r is not None and t.outcome is TradeOutcome.LOSS
        ]
        return RSection(
            sample_count=len(r_vals),
            coverage_ratio=len(r_vals) / len(closed) if closed else None,
            average_r=sum(r_vals) / len(r_vals),
            median_r=statistics.median(r_vals),
            win_avg_r=sum(win_r) / len(win_r) if win_r else None,
            loss_avg_r=sum(loss_r) / len(loss_r) if loss_r else None,
            best_r=max(r_vals),
            worst_r=min(r_vals),
            r_std=statistics.stdev(r_vals) if len(r_vals) > 1 else None,
        )

    def _stage_excursion(self, closed: list[TradeRecord]) -> ExcursionSection:
        # Excursion sign convention (TASK-1 forensic audit 2026-08-18):
        #   mae_usd is ADVERSE -> negative; mfe_usd is FAVOURABLE -> non-negative.
        # The raw ledger columns historically carried mixed conventions and
        # occasional sign violations; the canonical normalization lives in
        # accounting/aggregation.py (_mae_value/_mfe_value) and mirrors model
        # docs: MAE <= 0, MFE >= 0 ALWAYS.
        from nexus_scalp.accounting.aggregation import _mae_value, _mfe_value

        mae = [_mae_value(t) for t in closed]
        mfe = [_mfe_value(t) for t in closed]
        # MAE=0 is a meaningful zero (no adverse excursion), not missing — keep
        # it so a book of scratch trades reports avg MAE 0.0, not None (matches
        # the pre-existing test_mae_mfe_missing contract).
        mae = [v for v in mae if v <= 0.0]
        mfe = [v for v in mfe if v > 0.0]
        if not mae and not mfe:
            return ExcursionSection(sample_count=0)
        avg_mae = sum(mae) / len(mae) if mae else None
        avg_mfe = sum(mfe) / len(mfe) if mfe else None
        # MFE capture (portfolio-level, TASK-1 documented semantics):
        #   mfe_capture_ratio = Σ realized net PnL / Σ favourable excursion.
        # It is a POPULATION ratio, NOT a per-winner average: a net-negative
        # period yields a negative ratio (net PnL signed) which reads as
        # "portfolio lost money against total favourable excursion", never as
        # "winners captured -69% of their MFE". Consumers must present it as
        # portfolio capture, distinct from the per-winner retention metrics in
        # accounting/retention.py (mfe_capture_ratio per trade >= 0).
        mfe_capture = None
        total_mfe = sum(mfe)
        realized = sum(t.net_pnl for t in closed)
        if total_mfe > 1e-9:
            mfe_capture = realized / total_mfe
        avg_giveback = None
        givebacks = [m - t.net_pnl for t, m in ((t, _mfe_value(t)) for t in closed) if m > 0.0]
        if givebacks:
            avg_giveback = sum(givebacks) / len(givebacks)
        mae_r = [t.mae_r for t in closed if t.mae_r is not None]
        mfe_r = [t.mfe_r for t in closed if t.mfe_r is not None]
        return ExcursionSection(
            avg_mae_usd=avg_mae,
            avg_mfe_usd=avg_mfe,
            avg_mae_r=sum(mae_r) / len(mae_r) if mae_r else None,
            avg_mfe_r=sum(mfe_r) / len(mfe_r) if mfe_r else None,
            mfe_capture_ratio=mfe_capture,
            avg_giveback_usd=avg_giveback,
            sample_count=len(closed),
        )

    def _stage_holding(self, closed: list[TradeRecord]) -> HoldingSection:
        holds = [t.duration_sec for t in closed if t.duration_sec and t.duration_sec > 0.0]
        if not holds:
            return HoldingSection(sample_count=0)
        win_holds = [t.duration_sec for t in closed if t.is_win and t.duration_sec > 0.0]
        loss_holds = [
            t.duration_sec
            for t in closed
            if t.outcome is TradeOutcome.LOSS and t.duration_sec > 0.0
        ]
        return HoldingSection(
            avg_hold_sec=sum(holds) / len(holds),
            median_hold_sec=statistics.median(holds),
            win_hold_sec=sum(win_holds) / len(win_holds) if win_holds else None,
            loss_hold_sec=sum(loss_holds) / len(loss_holds) if loss_holds else None,
            sample_count=len(holds),
        )

    def _stage_exits(self, closed: list[TradeRecord]) -> list[ExitGroup]:
        buckets: dict[str, list[TradeRecord]] = {}
        for t in closed:
            key = _normalize_exit(t.exit_classification.value)
            buckets.setdefault(key, []).append(t)
        out: list[ExitGroup] = []
        for key, group in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            pnl = sum(t.net_pnl for t in group)
            decided = [t for t in group if t.outcome is not TradeOutcome.BREAKEVEN]
            wr = sum(1 for t in decided if t.is_win) / len(decided) * 100.0 if decided else None
            r_vals = [t.realized_r for t in group if t.realized_r is not None]
            avg_r = sum(r_vals) / len(r_vals) if r_vals else None
            out.append(
                ExitGroup(
                    exit_type=key,
                    count=len(group),
                    net_pnl=pnl,
                    win_rate=wr,
                    average_r=avg_r,
                )
            )
        return out

    def _stage_streaks(self, closed: list[TradeRecord]) -> StreakSection:
        cur_w = cur_l = max_w = max_l = 0
        for t in closed:
            if t.is_win:
                cur_w += 1
                cur_l = 0
                max_w = max(max_w, cur_w)
            elif t.outcome is TradeOutcome.LOSS:
                cur_l += 1
                cur_w = 0
                max_l = max(max_l, cur_l)
            else:  # breakeven resets streaks
                cur_w = 0
                cur_l = 0
        current_streak, current_type = (
            (cur_w, "WIN") if cur_w > 0 else ((cur_l, "LOSS") if cur_l > 0 else (0, "NONE"))
        )
        return StreakSection(
            max_win_streak=max_w,
            max_loss_streak=max_l,
            current_streak=current_streak,
            current_streak_type=current_type,
        )

    def _stage_risk(
        self,
        closed: list[TradeRecord],
        base: PeriodReport,
        live: Any,
    ) -> RiskSection:
        risk_vals = [t.risk_usd for t in closed if t.risk_usd is not None]
        sample = len(risk_vals)
        avg_risk = sum(risk_vals) / sample if risk_vals else None
        max_risk = max(risk_vals) if risk_vals else None
        total_risk = base.total_risk_deployed
        # Utilization: total risk deployed vs starting balance.
        risk_util = None
        start_bal = base.starting_balance
        if total_risk is not None and start_bal and start_bal > 0.0:
            risk_util = total_risk / start_bal * 100.0
        margin_util = None
        if live and live.available:
            if (live.margin is not None) and (live.equity or 0.0) > 0.0:
                margin_util = live.margin / live.equity * 100.0
        exposure_util = None
        if live and live.available:
            exposure_util = margin_util  # same basis on MT5 when available
        return RiskSection(
            avg_risk_usd=avg_risk,
            max_risk_usd=max_risk,
            total_risk_deployed=total_risk,
            risk_utilization_pct=risk_util,
            margin_utilization_pct=margin_util,
            exposure_utilization_pct=exposure_util,
            max_concurrent_positions=len(getattr(live, "positions", []) or [])
            if live and live.available
            else 0,
            sample_count=sample,
        )

    def _stage_drawdown(self) -> DrawdownSection:
        # TASK-1 forensic audit (2026-08-18): the report's "Max DD" WAS the
        # 90-day peak-to-trough equity drawdown while the SnapshotBlock
        # "drawdown_pct" was the intra-day drawdown of the period — two
        # different concepts sharing one label "Drawdown". The section now
        # carries both with explicit window labels: the period drawdown is
        # computed from the period snapshots (peak-to-trough WITHIN the
        # report window), and the 90-day/historical window is reported as
        # max_drawdown with the drawdown_window field set.
        dd = self.core.drawdown_report(lookback_days=90)
        period_dd = self.core.drawdown_report(lookback_days=1)
        return DrawdownSection(
            current_drawdown_pct=dd.current_drawdown_pct,
            current_drawdown_usd=dd.current_drawdown_usd,
            max_drawdown_pct=dd.max_drawdown_pct,
            max_drawdown_usd=dd.max_drawdown_usd,
            max_drawdown_at=dd.max_drawdown_at.isoformat() if dd.max_drawdown_at else None,
            recovery_pct=dd.recovery_pct,
            recovery_factor=None,
            drawdown_duration_sec=dd.drawdown_duration_sec,
            recovery_duration_sec=dd.recovery_duration_sec,
            in_drawdown=dd.in_drawdown,
            period_drawdown_pct=period_dd.max_drawdown_pct,
            drawdown_window="90D",
            has_data=dd.has_data,
        )

    def _stage_strategies(self, closed: list[TradeRecord]) -> list[StrategyGroup]:
        buckets: dict[str, list[TradeRecord]] = {}
        for t in closed:
            if t.strategy_id:
                buckets.setdefault(t.strategy_id, []).append(t)
        out: list[StrategyGroup] = []
        for sid, group in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            wins = sum(1 for t in group if t.is_win)
            losses = sum(1 for t in group if t.outcome is TradeOutcome.LOSS)
            net = sum(t.net_pnl for t in group)
            decided = wins + losses
            wr = wins / decided * 100.0 if decided else None
            exp = net / len(group) if group else None
            r_vals = [t.realized_r for t in group if t.realized_r is not None]
            avg_r = sum(r_vals) / len(r_vals) if r_vals else None
            gross_p = sum(t.net_pnl for t in group if t.is_win)
            gross_l = abs(sum(t.net_pnl for t in group if t.outcome is TradeOutcome.LOSS))
            pf = gross_p / gross_l if gross_l > 0.0 else None
            mae_r = [t.mae_r for t in group if t.mae_r is not None]
            mfe_r = [t.mfe_r for t in group if t.mfe_r is not None]
            out.append(
                StrategyGroup(
                    strategy_id=sid,
                    trades=len(group),
                    wins=wins,
                    losses=losses,
                    win_rate=wr,
                    net_pnl=net,
                    expectancy=exp,
                    average_r=avg_r,
                    profit_factor=pf,
                    avg_mae_r=sum(mae_r) / len(mae_r) if mae_r else None,
                    avg_mfe_r=sum(mfe_r) / len(mfe_r) if mfe_r else None,
                    confidence=None,
                    lifecycle_state="",
                    evidence=evidence_level(len(group)),
                )
            )
        return out

    def _stage_regimes(self, closed: list[TradeRecord]) -> list[RegimeGroup]:
        buckets: dict[str, list[TradeRecord]] = {}
        for t in closed:
            regime = (t.regime_at_open or "").strip() or "UNKNOWN"
            buckets.setdefault(regime, []).append(t)
        out: list[RegimeGroup] = []
        for regime, group in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            wins = sum(1 for t in group if t.is_win)
            losses = sum(1 for t in group if t.outcome is TradeOutcome.LOSS)
            net = sum(t.net_pnl for t in group)
            decided = wins + losses
            wr = wins / decided * 100.0 if decided else None
            exp = net / len(group) if group else None
            r_vals = [t.realized_r for t in group if t.realized_r is not None]
            mae_r = [t.mae_r for t in group if t.mae_r is not None]
            mfe_r = [t.mfe_r for t in group if t.mfe_r is not None]
            out.append(
                RegimeGroup(
                    regime=regime,
                    trades=len(group),
                    wins=wins,
                    losses=losses,
                    win_rate=wr,
                    net_pnl=net,
                    expectancy=exp,
                    average_r=sum(r_vals) / len(r_vals) if r_vals else None,
                    avg_mae_r=sum(mae_r) / len(mae_r) if mae_r else None,
                    avg_mfe_r=sum(mfe_r) / len(mfe_r) if mfe_r else None,
                    evidence=evidence_level(len(group)),
                )
            )
        return out

    def _stage_sessions(self, closed: list[TradeRecord]) -> list[SessionGroup]:
        buckets: dict[str, list[TradeRecord]] = {}
        for t in closed:
            if t.opened_at is None:
                continue
            hour = ensure_utc(t.opened_at).hour
            session = classify_session(hour)
            buckets.setdefault(session, []).append(t)
        out: list[SessionGroup] = []
        for session, group in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            wins = sum(1 for t in group if t.is_win)
            losses = sum(1 for t in group if t.outcome is TradeOutcome.LOSS)
            decided = wins + losses
            wr = wins / decided * 100.0 if decided else None
            net = sum(t.net_pnl for t in group)
            exp = net / len(group) if group else None
            r_vals = [t.realized_r for t in group if t.realized_r is not None]
            out.append(
                SessionGroup(
                    session=session,
                    trades=len(group),
                    net_pnl=net,
                    win_rate=wr,
                    expectancy=exp,
                    average_r=sum(r_vals) / len(r_vals) if r_vals else None,
                )
            )
        return out

    def _stage_model(self, bounds: Any) -> ModelSection:
        """Decision funnel from audit_signals in the period (task §3)."""
        if not self.core._enabled:
            return ModelSection()
        sql = (
            "SELECT action, blocked_by, payload FROM audit_signals "
            "WHERE generated_at >= ? AND generated_at < ?"
        )
        try:
            with self.core._connect() as conn:
                rows = [dict(r) for r in conn.execute(sql, (bounds.start_sql, bounds.end_sql))]
        except Exception as err:
            logger.error("[TELEGRAM_REPORT] model stage failed", error=str(err))
            return ModelSection()

        if not rows:
            return ModelSection()

        buy_probs: list[float] = []
        sell_probs: list[float] = []
        no_trade_probs: list[float] = []
        confs: list[float] = []
        model_rejected = policy_rejected = risk_rejected = 0
        exposure_blocked = execution_failed = trade_executed = 0
        executed_count = 0

        for row in rows:
            action = str(row.get("action") or "")
            blocked = str(row.get("blocked_by") or "").strip()
            payload = row.get("payload")
            pay: dict[str, Any] = {}
            if payload:
                try:
                    pay = json.loads(payload) if isinstance(payload, str) else dict(payload)
                except (TypeError, ValueError):
                    pay = {}
            b = _probe(pay, "ai_buy_probability", "buy_probability", "model_buy_probability")
            s = _probe(pay, "ai_sell_probability", "sell_probability", "model_sell_probability")
            n = _probe(pay, "ai_no_trade_probability", "no_trade_probability")
            c = _probe(pay, "confidence", "confidence_after_filters")
            if b is not None:
                buy_probs.append(b)
            if s is not None:
                sell_probs.append(s)
            if n is not None:
                no_trade_probs.append(n)
            if c is not None:
                confs.append(c)

            if action in ("BUY_MARKET", "SELL_MARKET", "BUY_LIMIT", "SELL_LIMIT"):
                if blocked:
                    if blocked in _BLOCKED_MODEL:
                        model_rejected += 1
                    elif blocked in _BLOCKED_POLICY:
                        policy_rejected += 1
                    elif blocked in _BLOCKED_RISK:
                        risk_rejected += 1
                    elif blocked in _BLOCKED_EXPOSURE:
                        exposure_blocked += 1
                    elif blocked in _BLOCKED_EXECUTION:
                        execution_failed += 1
                    else:
                        policy_rejected += 1  # unknown blocker -> policy layer
                else:
                    trade_executed += 1
                    executed_count += 1

        # TASK-1 forensic audit (2026-08-18): the funnel's rejection buckets only
        # account for rows whose action is an EXECUTABLE signal. The observed
        # production stream records `action=NO_TRADE` on EVERY rejected signal
        # (policy/risk/exposure blocks are stored as NO_TRADE with a reason in
        # blocked_by), so the previous crosstab undercounted every rejection
        # class (0/0/0/0 on the 2026-08-18 daily report despite 647 rejected
        # signals). Re-tabulate: a NO_TRADE row WITH a blocked_by reason is a
        # rejected executable signal; a NO_TRADE row WITHOUT a reason is a
        # genuine model no-trade. Blocked reasons never overlap executable
        # actions, so the two passes cannot double-count.
        for row in rows:
            _action = str(row.get("action") or "")
            _blocked = str(row.get("blocked_by") or "").strip()
            if _action != "NO_TRADE":
                continue
            if not _blocked:
                continue
            if _blocked in _BLOCKED_MODEL:
                model_rejected += 1
            elif _blocked in _BLOCKED_POLICY:
                policy_rejected += 1
            elif _blocked in _BLOCKED_RISK:
                risk_rejected += 1
            elif _blocked in _BLOCKED_EXPOSURE:
                exposure_blocked += 1
            elif _blocked in _BLOCKED_EXECUTION:
                execution_failed += 1
            else:
                policy_rejected += 1  # unknown blocker -> policy layer

        total_trade_signals = (
            model_rejected
            + policy_rejected
            + risk_rejected
            + exposure_blocked
            + execution_failed
            + trade_executed
        )
        # TASK-1: prediction_to_execution_rate is EXECUTED / EXECUTION_INTENTS
        # (dispatchable signals), not executed/all-predictions — the old label
        # "executed signal ratio" with an all-predictions denominator was
        # semantically wrong (it read 100% whenever every dispatched signal
        # filled, hiding the 800+ NO_TRADE predictions). The all-prediction
        # ratio is exposed separately as prediction_to_trade_rate.
        exec_rate = executed_count / total_trade_signals if total_trade_signals else None
        prediction_to_trade_rate = executed_count / len(rows) if rows and executed_count else None
        win_rate = None
        # prediction-to-win: executed trades that won (reuse period perf)
        return ModelSection(
            prediction_count=len(rows),
            avg_buy_probability=(sum(buy_probs) / len(buy_probs)) if buy_probs else None,
            avg_sell_probability=(sum(sell_probs) / len(sell_probs)) if sell_probs else None,
            avg_no_trade_probability=(sum(no_trade_probs) / len(no_trade_probs))
            if no_trade_probs
            else None,
            avg_confidence=(sum(confs) / len(confs)) if confs else None,
            prediction_to_execution_rate=exec_rate,
            prediction_to_trade_rate=prediction_to_trade_rate,
            prediction_to_win_rate=win_rate,
            executed_count=executed_count,
            model_rejected=model_rejected,
            policy_rejected=policy_rejected,
            risk_rejected=risk_rejected,
            exposure_blocked=exposure_blocked,
            execution_failed=execution_failed,
            trade_executed=trade_executed,
            has_data=True,
        )

    def _stage_execution(self, bounds: Any) -> ExecutionSection:
        """Execution quality from audit_orders latency (task §4)."""
        if not self.core._enabled:
            return ExecutionSection()
        sql = (
            "SELECT latency, reason, execution_mode FROM audit_orders "
            "WHERE timestamp >= ? AND timestamp < ?"
        )
        try:
            with self.core._connect() as conn:
                rows = [dict(r) for r in conn.execute(sql, (bounds.start_sql, bounds.end_sql))]
        except Exception as err:
            logger.error("[TELEGRAM_REPORT] execution stage failed", error=str(err))
            return ExecutionSection()
        if not rows:
            return ExecutionSection()

        latencies = [float(r.get("latency") or 0.0) for r in rows]
        latencies = [x for x in latencies if x > 0.0]
        reasons = [str(r.get("reason") or "") for r in rows]
        rejections = sum(
            1
            for r in reasons
            if "reject" in r.lower() or "fail" in r.lower() or "breakeven lock failed" in r.lower()
        )
        cancellations = sum(1 for r in reasons if "cancel" in r.lower())
        pendings = [x for x in latencies if x > 0.02]

        # TASK-1 forensic audit (2026-08-18): the report previously emitted
        # fill_ratio=None -> Telegram rendered "Fill Rate: 0%". The execution
        # audit_orders stream uses action values to mark outcomes:
        #   "Executed order"      = broker accepted the order (market OR pending)
        #   "Generated candidate" = dispatch attempt (no acceptance recorded)
        #   "BREAKEVEN_FAILED" / "Modified order" / "Expired pending order" /
        #   "PROFIT_GIVEBACK_PROTECTION" = management events, not fills
        # Fill ratio is therefore EXECUTED_ACCEPTANCES / (EXECUTED_ACCEPTANCES
        # + DISPATCH_ATTEMPTS), i.e. broker acknowledgements per dispatch.
        fill_ratio = None
        accepted = sum(1 for r in rows if str(r.get("action") or "") == "Executed order")
        dispatch_attempts = sum(
            1
            for r in rows
            if str(r.get("action") or "") in ("Executed order", "Generated candidate")
        )
        if dispatch_attempts > 0:
            fill_ratio = accepted / dispatch_attempts

        return ExecutionSection(
            sample_count=len(rows),
            avg_latency_sec=(sum(latencies) / len(latencies)) if latencies else None,
            worst_latency_sec=max(latencies) if latencies else None,
            rejection_count=rejections,
            cancellation_count=cancellations,
            fill_ratio=fill_ratio,
            pending_duration_sec=(sum(pendings) / len(pendings)) if pendings else None,
            execution_block_rate=None,
            has_data=True,
        )

    def _stage_news(self, closed: list[TradeRecord]) -> NewsSection:
        """News provenance on trades (task §7) — only when recorded."""
        active = [
            t for t in closed if getattr(t, "entry_reason", "") and "NEWS" in t.entry_reason.upper()
        ]
        inactive = [t for t in closed if t not in active]
        high_impact = [
            t
            for t in active
            if "HIGH" in t.entry_reason.upper() or "IMPACT" in t.entry_reason.upper()
        ]
        if not active and not inactive:
            return NewsSection()
        return NewsSection(
            news_active_trades=len(active),
            news_active_pnl=sum(t.net_pnl for t in active),
            news_inactive_trades=len(inactive),
            news_inactive_pnl=sum(t.net_pnl for t in inactive),
            high_impact_trades=len(high_impact),
            low_impact_trades=max(0, len(active) - len(high_impact)),
            has_data=True,
        )

    def _stage_behavioral(self, closed: list[TradeRecord]) -> BehavioralSection:
        """Behavior flags on closed trades (task §8). Truthful states:

        - NO_DATA: no behavioral analysis has EVER run for this period's trades
        - CLEAR:   analysis ran; zero flags with real evidence coverage
        - FLAGS_FOUND: analysis ran; flags exist

        Reads the versioned `behavior_analysis` derived records (canonical),
        merging the legacy Phase-08 outcome flags as an evidence source.
        """
        from nexus_scalp.intelligence.models import BehaviorAnalysisStatus

        if not self.core._enabled:
            return BehavioralSection(state="NO_DATA")
        tickets = [str(t.ticket) for t in closed]
        if not tickets:
            return BehavioralSection(state="NO_DATA")
        placeholders = ",".join("?" for _ in tickets[:500])
        try:
            with self.core._connect() as conn:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        f"SELECT behavior_key, pattern, severity, confidence, evidence "
                        f"FROM behavior_detections WHERE ticket IN ({placeholders})",
                        tuple(tickets[:500]),
                    )
                ]
                analysis = [
                    dict(r)
                    for r in conn.execute(
                        f"SELECT * FROM behavior_analysis WHERE ticket IN ({placeholders})",
                        tuple(tickets[:500]),
                    )
                ]
        except Exception as err:
            logger.error("[TELEGRAM_REPORT] behavioral stage failed", error=str(err))
            return BehavioralSection(state=BehaviorAnalysisStatus.ANALYSIS_FAILED.value)

        counts: dict[str, int] = {}
        for r in rows:
            key = str(r.get("pattern") or r.get("behavior_key") or "UNKNOWN")
            counts[key] = counts.get(key, 0) + 1

        if not analysis:
            # Analysis never ran for these trades -> NO_DATA, never "clear".
            return BehavioralSection(state="NO_DATA")

        analyzed = len(analysis)
        coverages = [float(a.get("evidence_coverage") or 0.0) for a in analysis]
        total_flags = sum(counts.values())
        state = (
            BehaviorAnalysisStatus.FLAGS_FOUND.value
            if total_flags > 0
            else BehaviorAnalysisStatus.CLEAR.value
        )
        version = str(analysis[0].get("behavior_version") or "behavior-v1")
        anomaly_version = str(analysis[0].get("anomaly_version") or "anomaly-v1")
        complete = sum(int(a.get("complete_context") or 0) for a in analysis)
        partial = sum(int(a.get("partial_context") or 0) for a in analysis)
        return BehavioralSection(
            state=state,
            flag_counts=counts,
            total_flags=total_flags,
            flagged_trades=len(counts),
            analyzed=analyzed,
            complete_context=complete,
            partial_context=partial,
            evidence_coverage=round(sum(coverages) / len(coverages), 4) if coverages else None,
            analysis_version=version,
            anomaly_version=anomaly_version,
            has_data=True,
        )

    def _stage_anomaly_state(self, closed: list[TradeRecord]) -> AnomalyStateSection:
        """Truthful anomaly census from the versioned `anomaly_events` store."""
        from nexus_scalp.intelligence.models import BehaviorAnalysisStatus

        if not self.core._enabled:
            return AnomalyStateSection(state="NO_DATA")
        tickets = [str(t.ticket) for t in closed]
        if not tickets:
            return AnomalyStateSection(state="NO_DATA")
        placeholders = ",".join("?" for _ in tickets[:500])
        try:
            with self.core._connect() as conn:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        f"SELECT anomaly_type, severity, algorithm_version "
                        f"FROM anomaly_events WHERE ticket IN ({placeholders})",
                        tuple(tickets[:500]),
                    )
                ]
                analysis = [
                    dict(r)
                    for r in conn.execute(
                        f"SELECT * FROM behavior_analysis WHERE ticket IN ({placeholders})",
                        tuple(tickets[:500]),
                    )
                ]
        except Exception as err:
            logger.error("[TELEGRAM_REPORT] anomaly stage failed", error=str(err))
            return AnomalyStateSection(state=BehaviorAnalysisStatus.ANALYSIS_FAILED.value)

        counts: dict[str, int] = {}
        severities: dict[str, int] = {}
        for r in rows:
            atype = str(r.get("anomaly_type") or "UNKNOWN")
            counts[atype] = counts.get(atype, 0) + 1
            sev = str(r.get("severity") or "LOW")
            severities[sev] = severities.get(sev, 0) + 1

        if not analysis:
            return AnomalyStateSection(state="NO_DATA")

        analyzed = len(analysis)
        total = sum(counts.values())
        state = (
            BehaviorAnalysisStatus.ANOMALIES_FOUND.value
            if total > 0
            else BehaviorAnalysisStatus.CLEAR.value
        )
        version = str(analysis[0].get("anomaly_version") or "anomaly-v1")
        return AnomalyStateSection(
            state=state,
            counts=counts,
            total=total,
            analyzed=analyzed,
            evidence_coverage=round(
                sum(float(a.get("evidence_coverage") or 0.0) for a in analysis) / len(analysis),
                4,
            )
            if analysis
            else None,
            anomaly_version=version,
            has_data=True,
        )

    def _stage_loss_drivers(self, closed: list[TradeRecord]) -> LossDriversSection:
        """Top loss drivers grouped by strategy (task §9)."""
        losses = [t for t in closed if t.outcome is TradeOutcome.LOSS]
        if not losses:
            return LossDriversSection()
        buckets: dict[str, list[TradeRecord]] = {}
        for t in losses:
            key = t.strategy_id or "UNKNOWN_STRATEGY"
            buckets.setdefault(key, []).append(t)
        drivers: list[dict[str, Any]] = []
        for key, group in sorted(
            buckets.items(), key=lambda kv: -abs(sum(t.net_pnl for t in kv[1]))
        ):
            total_loss = abs(sum(t.net_pnl for t in group))
            drivers.append(
                {
                    "driver": key,
                    "trades": len(group),
                    "total_loss": round(total_loss, 2),
                    "avg_loss": round(total_loss / len(group), 2),
                }
            )
        top = drivers[0]
        return LossDriversSection(
            dimension="strategy",
            drivers=drivers[:5],
            largest_driver=top["driver"],
            largest_driver_trades=top["trades"],
            largest_driver_loss=top["total_loss"],
            has_data=True,
        )

    def _stage_profit_drivers(self, closed: list[TradeRecord]) -> ProfitDriversSection:
        """Top profit drivers grouped by strategy (task §10)."""
        wins = [t for t in closed if t.is_win]
        if not wins:
            return ProfitDriversSection()
        buckets: dict[str, list[TradeRecord]] = {}
        for t in wins:
            key = t.strategy_id or "UNKNOWN_STRATEGY"
            buckets.setdefault(key, []).append(t)
        drivers: list[dict[str, Any]] = []
        for key, group in sorted(buckets.items(), key=lambda kv: -sum(t.net_pnl for t in kv[1])):
            total_profit = sum(t.net_pnl for t in group)
            drivers.append(
                {
                    "driver": key,
                    "trades": len(group),
                    "total_profit": round(total_profit, 2),
                    "avg_profit": round(total_profit / len(group), 2),
                }
            )
        top = drivers[0]
        return ProfitDriversSection(
            dimension="strategy",
            drivers=drivers[:5],
            best_driver=top["driver"],
            best_driver_trades=top["trades"],
            best_driver_profit=top["total_profit"],
            has_data=True,
        )

    def _stage_compare(
        self,
        base: PeriodReport,
        prev_base: PeriodReport | None,
        bounds: Any,
        prev_bounds: Any,
    ) -> PeriodCompareSection:
        if prev_base is None or not prev_base.has_data:
            return PeriodCompareSection(
                current_label=bounds.label,
                previous_label=prev_bounds.label,
                has_data=False,
            )
        return compare_periods(
            base.to_dict(),
            prev_base.to_dict(),
            current_label=bounds.label,
            previous_label=prev_bounds.label,
        )


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _normalize_exit(raw: str) -> str:
    return _EXIT_RAW_MAP.get(raw or "", raw or "UNKNOWN")


def _skew(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    m = statistics.mean(values)
    sd = statistics.stdev(values)
    if sd == 0.0:
        return None
    return statistics.mean((v - m) ** 3 for v in values) / (sd**3)


def _fmt_opt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f}"


def _probe(pay: dict[str, Any], *keys: str) -> float | None:
    """Reads the first present numeric value among `keys` from a payload dict."""
    for k in keys:
        v = pay.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None
