"""
Report JSON contract (structured, deterministic)
==================================================
Every section of the daily performance intelligence report is a frozen
dataclass so the contract is auditable and serializable. Telegram formatting
consumes these objects — numbers are NEVER re-derived inside string code.

All `None` values mean "cannot be derived from stored evidence" (the
accounting-core honesty rule): render as n/a, never as 0.0-as-a-placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvidenceLevel(StrEnum):
    """Sample-size evidence policy for advanced conclusions (task §17)."""

    DO_NOT_RANK = "DO_NOT_RANK"
    LOW_EVIDENCE = "LOW_EVIDENCE"
    USABLE = "USABLE"
    STRONGER_EVIDENCE = "STRONGER_EVIDENCE"


class TrendClassification(StrEnum):
    """Period-over-period trend state (task §11)."""

    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    DETERIORATING = "DETERIORATING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class SnapshotBlock:
    """One coherent accounting snapshot (task §1/STEP 1)."""

    snapshot_timestamp: str
    snapshot_id: str
    period_start: str
    period_end: str
    balance: float | None = None
    equity: float | None = None
    floating_pnl: float | None = None
    realized_pnl: float | None = None
    drawdown_pct: float | None = None
    available_margin: float | None = None
    margin: float | None = None
    margin_level: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_timestamp": self.snapshot_timestamp,
            "snapshot_id": self.snapshot_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "balance": _r(self.balance),
            "equity": _r(self.equity),
            "floating_pnl": _r(self.floating_pnl),
            "realized_pnl": _r(self.realized_pnl),
            "drawdown_pct": _r(self.drawdown_pct, 4),
            "available_margin": _r(self.available_margin),
            "margin": _r(self.margin),
            "margin_level": _r(self.margin_level, 2),
        }


@dataclass(frozen=True)
class PerformanceSection:
    """Core performance metrics (task §2/STEP 2-4; preserves period_report)."""

    trades: int = 0
    wins: int = 0
    losses: int = 0
    scratches: int = 0
    win_rate: float | None = None
    win_rate_all: float | None = None
    loss_rate_decided: float | None = None
    net_pnl: float | None = None
    gross_profit: float | None = None
    gross_loss: float | None = None
    total_costs: float | None = None
    cost_drag_pct: float | None = None
    profit_factor: float | None = None
    expectancy: float | None = None
    expectancy_breakeven_incl: float | None = None
    average_win: float | None = None
    average_loss: float | None = None
    median_trade: float | None = None
    median_win: float | None = None
    median_loss: float | None = None
    payoff_ratio: float | None = None
    avg_pnl_per_decided: float | None = None
    stop_loss_share: float | None = None
    total_volume: float = 0.0
    best_trade: float | None = None
    worst_trade: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "scratches": self.scratches,
            "win_rate": _r(self.win_rate, 2),
            "win_rate_all": _r(self.win_rate_all, 2),
            "loss_rate_decided": _r(self.loss_rate_decided, 2),
            "net_pnl": _r(self.net_pnl),
            "gross_profit": _r(self.gross_profit),
            "gross_loss": _r(self.gross_loss),
            "total_costs": _r(self.total_costs),
            "cost_drag_pct": _r(self.cost_drag_pct, 2),
            "profit_factor": _r(self.profit_factor, 3),
            "expectancy": _r(self.expectancy),
            "expectancy_breakeven_incl": _r(self.expectancy_breakeven_incl),
            "average_win": _r(self.average_win),
            "average_loss": _r(self.average_loss),
            "median_trade": _r(self.median_trade),
            "median_win": _r(self.median_win),
            "median_loss": _r(self.median_loss),
            "payoff_ratio": _r(self.payoff_ratio, 3),
            "avg_pnl_per_decided": _r(self.avg_pnl_per_decided),
            "stop_loss_share": _r(self.stop_loss_share, 4),
            "total_volume": round(self.total_volume, 2),
            "best_trade": _r(self.best_trade),
            "worst_trade": _r(self.worst_trade),
        }


@dataclass(frozen=True)
class DistributionSection:
    """Distribution of trade outcomes (task STEP 4)."""

    avg_win: float | None = None
    avg_loss: float | None = None
    median_win: float | None = None
    median_loss: float | None = None
    payoff_ratio: float | None = None
    profit_factor: float | None = None
    expectancy: float | None = None
    expectancy_breakeven_incl: float | None = None
    win_skew: float | None = None
    loss_skew: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "avg_win": _r(self.avg_win),
            "avg_loss": _r(self.avg_loss),
            "median_win": _r(self.median_win),
            "median_loss": _r(self.median_loss),
            "payoff_ratio": _r(self.payoff_ratio, 3),
            "profit_factor": _r(self.profit_factor, 3),
            "expectancy": _r(self.expectancy),
            "expectancy_breakeven_incl": _r(self.expectancy_breakeven_incl),
            "win_skew": _r(self.win_skew, 4),
            "loss_skew": _r(self.loss_skew, 4),
        }


@dataclass(frozen=True)
class RSection:
    """R-multiple statistics (task STEP 5)."""

    sample_count: int = 0
    coverage_ratio: float | None = None
    average_r: float | None = None
    median_r: float | None = None
    win_avg_r: float | None = None
    loss_avg_r: float | None = None
    best_r: float | None = None
    worst_r: float | None = None
    r_std: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "coverage_ratio": _r(self.coverage_ratio, 4),
            "average_r": _r(self.average_r, 4),
            "median_r": _r(self.median_r, 4),
            "win_avg_r": _r(self.win_avg_r, 4),
            "loss_avg_r": _r(self.loss_avg_r, 4),
            "best_r": _r(self.best_r, 4),
            "worst_r": _r(self.worst_r, 4),
            "r_std": _r(self.r_std, 4),
        }


@dataclass(frozen=True)
class ExcursionSection:
    """MAE/MFE analysis (task STEP 6)."""

    avg_mae_usd: float | None = None
    avg_mfe_usd: float | None = None
    avg_mae_r: float | None = None
    avg_mfe_r: float | None = None
    mfe_capture_ratio: float | None = None
    avg_giveback_usd: float | None = None
    sample_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "avg_mae_usd": _r(self.avg_mae_usd),
            "avg_mfe_usd": _r(self.avg_mfe_usd),
            "avg_mae_r": _r(self.avg_mae_r, 4),
            "avg_mfe_r": _r(self.avg_mfe_r, 4),
            "mfe_capture_ratio": _r(self.mfe_capture_ratio, 4),
            "avg_giveback_usd": _r(self.avg_giveback_usd),
            "sample_count": self.sample_count,
        }


@dataclass(frozen=True)
class HoldingSection:
    """Holding / exit analysis (task STEP 7)."""

    avg_hold_sec: float | None = None
    median_hold_sec: float | None = None
    win_hold_sec: float | None = None
    loss_hold_sec: float | None = None
    sample_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "avg_hold_sec": _r(self.avg_hold_sec, 1),
            "median_hold_sec": _r(self.median_hold_sec, 1),
            "win_hold_sec": _r(self.win_hold_sec, 1),
            "loss_hold_sec": _r(self.loss_hold_sec, 1),
            "sample_count": self.sample_count,
        }


@dataclass(frozen=True)
class ExitGroup:
    """Per-exit-type census (task STEP 7)."""

    exit_type: str
    count: int = 0
    net_pnl: float = 0.0
    win_rate: float | None = None
    average_r: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_type": self.exit_type,
            "count": self.count,
            "net_pnl": _r(self.net_pnl),
            "win_rate": _r(self.win_rate, 2),
            "average_r": _r(self.average_r, 4),
        }


@dataclass(frozen=True)
class StreakSection:
    """Win/loss streaks (task STEP 8)."""

    max_win_streak: int = 0
    max_loss_streak: int = 0
    current_streak: int = 0
    current_streak_type: str = "NONE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_win_streak": self.max_win_streak,
            "max_loss_streak": self.max_loss_streak,
            "current_streak": self.current_streak,
            "current_streak_type": self.current_streak_type,
        }


@dataclass(frozen=True)
class RiskSection:
    """Risk utilisation (task STEP 9; reporting only — never changes RiskEngine)."""

    avg_risk_usd: float | None = None
    max_risk_usd: float | None = None
    total_risk_deployed: float | None = None
    risk_utilization_pct: float | None = None
    margin_utilization_pct: float | None = None
    exposure_utilization_pct: float | None = None
    max_concurrent_positions: int = 0
    sample_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "avg_risk_usd": _r(self.avg_risk_usd),
            "max_risk_usd": _r(self.max_risk_usd),
            "total_risk_deployed": _r(self.total_risk_deployed),
            "risk_utilization_pct": _r(self.risk_utilization_pct, 2),
            "margin_utilization_pct": _r(self.margin_utilization_pct, 2),
            "exposure_utilization_pct": _r(self.exposure_utilization_pct, 2),
            "max_concurrent_positions": self.max_concurrent_positions,
            "sample_count": self.sample_count,
        }


@dataclass(frozen=True)
class DrawdownSection:
    """Drawdown analysis (task STEP 10)."""

    current_drawdown_pct: float | None = None
    current_drawdown_usd: float | None = None
    max_drawdown_pct: float | None = None
    max_drawdown_usd: float | None = None
    max_drawdown_at: str | None = None
    recovery_pct: float | None = None
    recovery_factor: float | None = None
    drawdown_duration_sec: float | None = None
    recovery_duration_sec: float | None = None
    in_drawdown: bool = False
    period_drawdown_pct: float | None = None
    drawdown_window: str = ""
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_drawdown_pct": _r(self.current_drawdown_pct, 3),
            "current_drawdown_usd": _r(self.current_drawdown_usd),
            "max_drawdown_pct": _r(self.max_drawdown_pct, 3),
            "max_drawdown_usd": _r(self.max_drawdown_usd),
            "max_drawdown_at": self.max_drawdown_at,
            "recovery_pct": _r(self.recovery_pct, 3),
            "recovery_factor": _r(self.recovery_factor, 4),
            "drawdown_duration_sec": _r(self.drawdown_duration_sec, 1),
            "recovery_duration_sec": _r(self.recovery_duration_sec, 1),
            "in_drawdown": self.in_drawdown,
            "period_drawdown_pct": _r(self.period_drawdown_pct, 3),
            "drawdown_window": self.drawdown_window,
            "has_data": self.has_data,
        }


@dataclass(frozen=True)
class StrategyGroup:
    """Per-strategy attribution (task STEP 11)."""

    strategy_id: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float | None = None
    net_pnl: float = 0.0
    expectancy: float | None = None
    average_r: float | None = None
    profit_factor: float | None = None
    avg_mae_r: float | None = None
    avg_mfe_r: float | None = None
    confidence: float | None = None
    lifecycle_state: str = ""
    evidence: str = "DO_NOT_RANK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": _r(self.win_rate, 2),
            "net_pnl": _r(self.net_pnl),
            "expectancy": _r(self.expectancy),
            "average_r": _r(self.average_r, 4),
            "profit_factor": _r(self.profit_factor, 3),
            "avg_mae_r": _r(self.avg_mae_r, 4),
            "avg_mfe_r": _r(self.avg_mfe_r, 4),
            "confidence": _r(self.confidence, 4),
            "lifecycle_state": self.lifecycle_state,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class RegimeGroup:
    """Per-regime attribution (task §6)."""

    regime: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float | None = None
    net_pnl: float = 0.0
    expectancy: float | None = None
    average_r: float | None = None
    avg_mae_r: float | None = None
    avg_mfe_r: float | None = None
    evidence: str = "DO_NOT_RANK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": _r(self.win_rate, 2),
            "net_pnl": _r(self.net_pnl),
            "expectancy": _r(self.expectancy),
            "average_r": _r(self.average_r, 4),
            "avg_mae_r": _r(self.avg_mae_r, 4),
            "avg_mfe_r": _r(self.avg_mfe_r, 4),
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class SessionGroup:
    """Per-session attribution (task §5; derived from UTC open time)."""

    session: str
    trades: int = 0
    net_pnl: float = 0.0
    win_rate: float | None = None
    expectancy: float | None = None
    average_r: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "trades": self.trades,
            "net_pnl": _r(self.net_pnl),
            "win_rate": _r(self.win_rate, 2),
            "expectancy": _r(self.expectancy),
            "average_r": _r(self.average_r, 4),
        }


@dataclass(frozen=True)
class ModelSection:
    """Model prediction / decision quality (task §3)."""

    prediction_count: int = 0
    avg_buy_probability: float | None = None
    avg_sell_probability: float | None = None
    avg_no_trade_probability: float | None = None
    avg_confidence: float | None = None
    prediction_to_execution_rate: float | None = None
    #: executed / ALL predictions (TASK-1); complements
    #: prediction_to_execution_rate which is executed / execution intents.
    prediction_to_trade_rate: float | None = None
    prediction_to_win_rate: float | None = None
    executed_count: int = 0
    model_rejected: int = 0
    policy_rejected: int = 0
    risk_rejected: int = 0
    exposure_blocked: int = 0
    execution_failed: int = 0
    trade_executed: int = 0
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_count": self.prediction_count,
            "avg_buy_probability": _r(self.avg_buy_probability, 4),
            "avg_sell_probability": _r(self.avg_sell_probability, 4),
            "avg_no_trade_probability": _r(self.avg_no_trade_probability, 4),
            "avg_confidence": _r(self.avg_confidence, 4),
            "prediction_to_execution_rate": _r(self.prediction_to_execution_rate, 3),
            "prediction_to_trade_rate": _r(self.prediction_to_trade_rate, 3),
            "prediction_to_win_rate": _r(self.prediction_to_win_rate, 3),
            "executed_count": self.executed_count,
            "model_rejected": self.model_rejected,
            "policy_rejected": self.policy_rejected,
            "risk_rejected": self.risk_rejected,
            "exposure_blocked": self.exposure_blocked,
            "execution_failed": self.execution_failed,
            "trade_executed": self.trade_executed,
            "has_data": self.has_data,
        }


@dataclass(frozen=True)
class ExecutionSection:
    """Execution quality (task §4)."""

    sample_count: int = 0
    avg_latency_sec: float | None = None
    worst_latency_sec: float | None = None
    rejection_count: int = 0
    cancellation_count: int = 0
    fill_ratio: float | None = None
    pending_duration_sec: float | None = None
    execution_block_rate: float | None = None
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "avg_latency_sec": _r(self.avg_latency_sec, 4),
            "worst_latency_sec": _r(self.worst_latency_sec, 4),
            "rejection_count": self.rejection_count,
            "cancellation_count": self.cancellation_count,
            "fill_ratio": _r(self.fill_ratio, 3),
            "pending_duration_sec": _r(self.pending_duration_sec, 1),
            "execution_block_rate": _r(self.execution_block_rate, 3),
            "has_data": self.has_data,
        }


@dataclass(frozen=True)
class NewsSection:
    """News-impact comparison (task §7; provenance only, no causality claims)."""

    news_active_trades: int = 0
    news_active_pnl: float = 0.0
    news_inactive_trades: int = 0
    news_inactive_pnl: float = 0.0
    high_impact_trades: int = 0
    low_impact_trades: int = 0
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "news_active_trades": self.news_active_trades,
            "news_active_pnl": _r(self.news_active_pnl),
            "news_inactive_trades": self.news_inactive_trades,
            "news_inactive_pnl": _r(self.news_inactive_pnl),
            "high_impact_trades": self.high_impact_trades,
            "low_impact_trades": self.low_impact_trades,
            "has_data": self.has_data,
        }


@dataclass(frozen=True)
class BehavioralSection:
    """Behavioral flag census with a truthful analysis state (§14/§15).

    `state` distinguishes: NOT_ANALYZED / ANALYZING / ANALYSIS_FAILED /
    INSUFFICIENT_EVIDENCE / CLEAR / FLAGS_FOUND. `has_data` remains True as
    soon as ANY analysis ran — a CLEAR zero-flag result is real data.
    """

    state: str = "NOT_ANALYZED"
    flag_counts: dict[str, int] = field(default_factory=dict)
    total_flags: int = 0
    flagged_trades: int = 0
    analyzed: int = 0
    complete_context: int = 0
    partial_context: int = 0
    evidence_coverage: float | None = None
    analysis_version: str = ""
    anomaly_version: str = ""
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "flag_counts": dict(self.flag_counts),
            "total_flags": self.total_flags,
            "flagged_trades": self.flagged_trades,
            "analyzed": self.analyzed,
            "complete_context": self.complete_context,
            "partial_context": self.partial_context,
            "evidence_coverage": self.evidence_coverage,
            "analysis_version": self.analysis_version,
            "anomaly_version": self.anomaly_version,
            "has_data": self.has_data,
        }


@dataclass(frozen=True)
class AnomalyStateSection:
    """Anomaly census with a truthful state (never 'none detected' by silence).

    `state`: NOT_ANALYZED / ANALYZING / ANALYSIS_FAILED / INSUFFICIENT_EVIDENCE
    / CLEAR / ANOMALIES_FOUND.
    """

    state: str = "NOT_ANALYZED"
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    analyzed: int = 0
    evidence_coverage: float | None = None
    anomaly_version: str = ""
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "counts": dict(self.counts),
            "total": self.total,
            "analyzed": self.analyzed,
            "evidence_coverage": self.evidence_coverage,
            "anomaly_version": self.anomaly_version,
            "has_data": self.has_data,
        }


@dataclass(frozen=True)
class LossDriversSection:
    """Top loss drivers (task §9)."""

    dimension: str = ""
    drivers: list[dict[str, Any]] = field(default_factory=list)
    largest_driver: str = ""
    largest_driver_trades: int = 0
    largest_driver_loss: float = 0.0
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "drivers": self.drivers,
            "largest_driver": self.largest_driver,
            "largest_driver_trades": self.largest_driver_trades,
            "largest_driver_loss": _r(self.largest_driver_loss),
            "has_data": self.has_data,
        }


@dataclass(frozen=True)
class ProfitDriversSection:
    """Top profit drivers (task §10)."""

    dimension: str = ""
    drivers: list[dict[str, Any]] = field(default_factory=list)
    best_driver: str = ""
    best_driver_trades: int = 0
    best_driver_profit: float = 0.0
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "drivers": self.drivers,
            "best_driver": self.best_driver,
            "best_driver_trades": self.best_driver_trades,
            "best_driver_profit": _r(self.best_driver_profit),
            "has_data": self.has_data,
        }


@dataclass(frozen=True)
class PeriodCompareSection:
    """Current vs previous equivalent period (task §11)."""

    current_label: str = ""
    previous_label: str = ""
    pnl_delta: float | None = None
    win_rate_delta: float | None = None
    expectancy_delta: float | None = None
    drawdown_delta: float | None = None
    average_r_delta: float | None = None
    trade_count_delta: int = 0
    current_trades: int = 0
    previous_trades: int = 0
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_label": self.current_label,
            "previous_label": self.previous_label,
            "pnl_delta": _r(self.pnl_delta),
            "win_rate_delta": _r(self.win_rate_delta, 2),
            "expectancy_delta": _r(self.expectancy_delta),
            "drawdown_delta": _r(self.drawdown_delta, 3),
            "average_r_delta": _r(self.average_r_delta, 4),
            "trade_count_delta": self.trade_count_delta,
            "current_trades": self.current_trades,
            "previous_trades": self.previous_trades,
            "has_data": self.has_data,
        }


@dataclass(frozen=True)
class AnomalyItem:
    """One detected anomaly (task §13)."""

    anomaly_type: str
    severity: str = "INFO"
    detail: str = ""
    value: float | None = None
    threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_type": self.anomaly_type,
            "severity": self.severity,
            "detail": self.detail,
            "value": _r(self.value, 4),
            "threshold": _r(self.threshold, 4),
        }


@dataclass(frozen=True)
class InsightItem:
    """One deterministic analytical sentence (task §16)."""

    text: str
    kind: str = "INFO"

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "kind": self.kind}


@dataclass(frozen=True)
class HealthScoreSection:
    """Deterministic composite account-health score (task §14)."""

    total: int = 0
    profitability: int = 0
    risk: int = 0
    consistency: int = 0
    execution: int = 0
    strategy_stability: int = 0
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "profitability": self.profitability,
            "risk": self.risk,
            "consistency": self.consistency,
            "execution": self.execution,
            "strategy_stability": self.strategy_stability,
            "rationale": list(self.rationale),
        }


@dataclass(frozen=True)
class ReportContainer:
    """The complete structured daily report (task §21)."""

    report_id: str
    snapshot_id: str
    generated_at: str
    period_kind: str
    period_start: str
    period_end: str
    account: SnapshotBlock
    performance: PerformanceSection
    distribution: DistributionSection
    r: RSection
    excursion: ExcursionSection
    holding: HoldingSection
    exits: list[ExitGroup] = field(default_factory=list)
    streaks: StreakSection = field(default_factory=StreakSection)
    risk: RiskSection = field(default_factory=RiskSection)
    drawdown: DrawdownSection = field(default_factory=DrawdownSection)
    strategies: list[StrategyGroup] = field(default_factory=list)
    regimes: list[RegimeGroup] = field(default_factory=list)
    sessions: list[SessionGroup] = field(default_factory=list)
    model: ModelSection = field(default_factory=ModelSection)
    execution: ExecutionSection = field(default_factory=ExecutionSection)
    news: NewsSection = field(default_factory=NewsSection)
    behavioral: BehavioralSection = field(default_factory=BehavioralSection)
    anomaly_state: AnomalyStateSection = field(default_factory=AnomalyStateSection)
    loss_drivers: LossDriversSection = field(default_factory=LossDriversSection)
    profit_drivers: ProfitDriversSection = field(default_factory=ProfitDriversSection)
    period_compare: PeriodCompareSection = field(default_factory=PeriodCompareSection)
    anomalies: list[AnomalyItem] = field(default_factory=list)
    health_score: HealthScoreSection = field(default_factory=HealthScoreSection)
    insights: list[InsightItem] = field(default_factory=list)
    trend: str = TrendClassification.INSUFFICIENT_DATA.value
    evidence: str = EvidenceLevel.DO_NOT_RANK.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "snapshot_id": self.snapshot_id,
            "generated_at": self.generated_at,
            "period_kind": self.period_kind,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "account": self.account.to_dict(),
            "performance": self.performance.to_dict(),
            "distribution": self.distribution.to_dict(),
            "r": self.r.to_dict(),
            "excursion": self.excursion.to_dict(),
            "holding": self.holding.to_dict(),
            "exits": [e.to_dict() for e in self.exits],
            "streaks": self.streaks.to_dict(),
            "risk": self.risk.to_dict(),
            "drawdown": self.drawdown.to_dict(),
            "strategies": [s.to_dict() for s in self.strategies],
            "regimes": [r.to_dict() for r in self.regimes],
            "sessions": [s.to_dict() for s in self.sessions],
            "model": self.model.to_dict(),
            "execution": self.execution.to_dict(),
            "news": self.news.to_dict(),
            "behavioral": self.behavioral.to_dict(),
            "anomaly_state": self.anomaly_state.to_dict(),
            "loss_drivers": self.loss_drivers.to_dict(),
            "profit_drivers": self.profit_drivers.to_dict(),
            "period_compare": self.period_compare.to_dict(),
            "anomalies": [a.to_dict() for a in self.anomalies],
            "health_score": self.health_score.to_dict(),
            "insights": [i.to_dict() for i in self.insights],
            "trend": self.trend,
            "evidence": self.evidence,
        }


def _r(value: float | None, ndigits: int = 2) -> float | None:
    """Rounds a float for serialization; None stays None (honesty rule)."""
    if value is None:
        return None
    return round(float(value), ndigits)
