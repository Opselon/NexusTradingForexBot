"""
Accounting Domain Models
========================
Canonical value objects for the accounting + performance intelligence core.

DESIGN RULES
------------
1. NO FABRICATED NUMBERS. Every metric that cannot be derived from real stored
   evidence is `None`, never 0.0-as-a-placeholder. Consumers render "n/a" for
   `None`; a 0.0 would be indistinguishable from a genuine flat result.
2. WIN/LOSS IS DECIDED BY REALIZED MONEY, never by whether a stop had been moved
   to breakeven. `TradeRecord.outcome` derives from net PnL alone, while
   `exit_classification` and `risk_free_state` remain independently visible.
3. Every record keeps the identity chain (ticket -> order/request -> experience
   -> strategy -> model) so accounting and Experience Intelligence can join
   without inventing new identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from nexus_scalp.accounting.periods import PeriodKind


class ExitClassification(StrEnum):
    """
    How a position actually ended, derived from stored execution evidence.

    `BREAKEVEN_STOP` and `TRAILING_STOP` are deliberately distinct from
    `INITIAL_STOP`: all three are stop-outs, but they represent very different
    position-management quality and must stay separable in reporting.
    """

    TAKE_PROFIT = "TAKE_PROFIT"
    INITIAL_STOP = "INITIAL_STOP"
    BREAKEVEN_STOP = "BREAKEVEN_STOP"
    TRAILING_STOP = "TRAILING_STOP"
    MANUAL_EXIT = "MANUAL_EXIT"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"
    STRATEGY_EXIT = "STRATEGY_EXIT"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    OTHER_EXIT = "OTHER_EXIT"
    UNKNOWN = "UNKNOWN"

    @property
    def is_stop_exit(self) -> bool:
        """True for every flavour of protective-stop closure."""
        return self in (
            ExitClassification.INITIAL_STOP,
            ExitClassification.BREAKEVEN_STOP,
            ExitClassification.TRAILING_STOP,
        )


class TradeOutcome(StrEnum):
    """Financial result of a closed trade, decided by realized net PnL only."""

    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"


class LossAttribution(StrEnum):
    """
    Evidence-based classification of WHERE a losing trade failed.

    `UNKNOWN` is a first-class answer: when the stored evidence cannot separate
    strategy failure from execution failure, saying so is correct and blaming the
    strategy by default is not.
    """

    SIGNAL_FAILURE = "SIGNAL_FAILURE"
    STRATEGY_FAILURE = "STRATEGY_FAILURE"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    ENTRY_FAILURE = "ENTRY_FAILURE"
    RISK_FAILURE = "RISK_FAILURE"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    POSITION_MANAGEMENT_FAILURE = "POSITION_MANAGEMENT_FAILURE"
    EXIT_FAILURE = "EXIT_FAILURE"
    MODEL_CONFIDENCE_FAILURE = "MODEL_CONFIDENCE_FAILURE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AccountSnapshot:
    """
    A point-in-time account state.

    `floating_pnl`, `margin`, `margin_level` and `peak_balance` are optional
    because older snapshot rows predate those columns; reporting must degrade to
    "unavailable" rather than imputing zeros into a financial series.
    """

    timestamp: datetime
    balance: float
    equity: float
    margin_free: float
    peak_equity: float
    floating_pnl: float | None = None
    margin: float | None = None
    margin_level: float | None = None
    peak_balance: float | None = None
    realized_pnl: float | None = None
    currency: str = ""
    account_login: str = ""
    trading_state: str = ""

    @property
    def drawdown_pct(self) -> float | None:
        """Equity drawdown from peak equity, in percent."""
        if self.peak_equity <= 0.0:
            return None
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100.0)


@dataclass(frozen=True)
class TradeRecord:
    """
    One CLOSED trade, normalized into canonical accounting form.

    This is the single shape every consumer reads. `net_pnl` is authoritative and
    is computed exactly once (gross minus costs) by the accounting core, so no
    consumer can re-derive it with a different sign convention.
    """

    ticket: int
    symbol: str
    direction: str
    volume: float
    entry_price: float
    exit_price: float
    gross_pnl: float
    commission: float
    swap: float
    net_pnl: float
    opened_at: datetime | None
    closed_at: datetime | None
    duration_sec: float
    exit_mechanism_raw: str
    exit_classification: ExitClassification
    outcome: TradeOutcome
    risk_free_state: bool
    was_sl_modified: bool
    initial_sl: float
    final_sl: float
    mae_points: float
    mfe_points: float
    mae_usd: float
    mfe_usd: float
    status: str = ""
    order_id: str = ""
    entry_reason: str = ""
    confidence_at_open: float | None = None
    regime_at_open: str = ""
    balance_after: float | None = None
    equity_after: float | None = None
    #: Realized R multiple. None when the risk basis cannot be reconstructed from
    #: stored evidence (no initial stop, or no USD/point conversion available).
    realized_r: float | None = None
    #: Reconstructed dollar risk at entry (|entry-initial_sl| in money terms).
    risk_usd: float | None = None
    #: Identity chain into Experience Intelligence (empty when unlinked).
    experience_id: str = ""
    strategy_id: str = ""
    strategy_version: str = ""
    model_id: str = ""
    model_version: str = ""
    feature_schema_id: str = ""
    feature_dimension: int | None = None

    @property
    def is_win(self) -> bool:
        """True only when real money was made."""
        return self.outcome is TradeOutcome.WIN

    @property
    def mfe_r(self) -> float | None:
        """Peak favourable excursion expressed in R, when risk is known."""
        if not self.risk_usd:
            return None
        return abs(self.mfe_usd) / self.risk_usd

    @property
    def mae_r(self) -> float | None:
        """Peak adverse excursion expressed in R, when risk is known."""
        if not self.risk_usd:
            return None
        return abs(self.mae_usd) / self.risk_usd


@dataclass
class PeriodReport:
    """
    Aggregated performance for one canonical period.

    Money fields are always real sums. Ratio/expectancy fields are `None` when
    the sample cannot support them (e.g. profit factor with zero losses), which
    the dashboard renders as "n/a" instead of a misleading number.
    """

    kind: PeriodKind
    key: str
    label: str
    period_start: datetime
    period_end: datetime

    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    breakeven_count: int = 0

    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0
    commission_total: float = 0.0
    swap_total: float = 0.0

    starting_balance: float | None = None
    ending_balance: float | None = None
    starting_equity: float | None = None
    ending_equity: float | None = None
    pnl_pct: float | None = None

    win_rate: float | None = None
    average_win: float | None = None
    average_loss: float | None = None
    expectancy: float | None = None
    profit_factor: float | None = None
    average_r: float | None = None
    r_sample_count: int = 0

    # --- PRO WIN-RATE / LOSS-RATE RECONCILIATION (Phase 16) --------------
    # The UI shows win rate with an explicit denominator and loss rate as its
    # paired complement, so a low classic win rate can be audited at a glance:
    #   win_rate              = wins / (wins + losses)        [classic]
    #   loss_rate_decided     = losses / (wins + losses)     [complement]
    #   win_rate_all          = wins / total_trades          [incl. breakevens]
    #   loss_rate_all         = losses / total_trades
    #   pnl_weighted_win_rate = gross_profit / (gross_profit + gross_loss)
    #   win_rate_denominator  = "DECIDED" | "ALL_TRADES"     [source of truth]
    loss_rate_decided: float | None = None
    loss_rate_all: float | None = None
    win_rate_all: float | None = None
    pnl_weighted_win_rate: float | None = None
    win_rate_denominator: str = "NONE"

    # --- EXPECTANCY & COST INTELLIGENCE (Phase 16) -------------------------
    # Breakeven-inclusive expectancy and per-decided-trade PnL share the SAME
    # denominators as win_rate_all / win_rate so every pair reconciles:
    # wins + losses + breakevens == total_trades.
    expectancy_breakeven_incl: float | None = None
    avg_pnl_per_decided: float | None = None
    total_costs: float = 0.0
    cost_drag_pct: float | None = None

    # --- LOSS PERSISTENCE INTELLIGENCE (Phase 16) --------------------------
    # stop_loss_share: fraction of ALL losses that ended at a protective stop
    # (initial/breakeven/trailing) - high values mean the stop system is doing
    # the closing; low values mean losers bled out via manual/emergency/strategy
    # exits. avg_loss_r: average realized R on losers only (how much of planned
    # risk a typical loser actually burns; derived from the SAME realized_r
    # evidence as average_r - none is ever fabricated).
    stop_loss_share: float | None = None
    avg_loss_r: float | None = None

    max_drawdown_pct: float | None = None
    max_drawdown_usd: float | None = None
    best_trade: float | None = None
    worst_trade: float | None = None
    average_holding_sec: float | None = None
    total_risk_deployed: float | None = None
    total_volume: float = 0.0

    #: Exit-classification census, e.g. {"BREAKEVEN_STOP": 3, "TAKE_PROFIT": 5}.
    exit_breakdown: dict[str, int] = field(default_factory=dict)
    #: True when at least one authoritative record backed this report.
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for the REST layer."""
        return {
            "kind": self.kind.value,
            "key": self.key,
            "label": self.label,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "has_data": self.has_data,
            "total_trades": self.total_trades,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "breakeven_count": self.breakeven_count,
            "gross_profit": round(self.gross_profit, 2),
            "gross_loss": round(self.gross_loss, 2),
            "net_pnl": round(self.net_pnl, 2),
            "commission_total": round(self.commission_total, 2),
            "swap_total": round(self.swap_total, 2),
            "starting_balance": _round_opt(self.starting_balance),
            "ending_balance": _round_opt(self.ending_balance),
            "starting_equity": _round_opt(self.starting_equity),
            "ending_equity": _round_opt(self.ending_equity),
            "pnl_pct": _round_opt(self.pnl_pct, 3),
            "win_rate": _round_opt(self.win_rate, 2),
            "average_win": _round_opt(self.average_win),
            "average_loss": _round_opt(self.average_loss),
            "expectancy": _round_opt(self.expectancy),
            "profit_factor": _round_opt(self.profit_factor, 3),
            "average_r": _round_opt(self.average_r, 3),
            "r_sample_count": self.r_sample_count,
            "max_drawdown_pct": _round_opt(self.max_drawdown_pct, 3),
            "max_drawdown_usd": _round_opt(self.max_drawdown_usd),
            "best_trade": _round_opt(self.best_trade),
            "worst_trade": _round_opt(self.worst_trade),
            "average_holding_sec": _round_opt(self.average_holding_sec, 1),
            "total_risk_deployed": _round_opt(self.total_risk_deployed),
            "total_volume": round(self.total_volume, 2),
            "loss_rate_decided": _round_opt(self.loss_rate_decided, 2),
            "loss_rate_all": _round_opt(self.loss_rate_all, 2),
            "win_rate_all": _round_opt(self.win_rate_all, 2),
            "pnl_weighted_win_rate": _round_opt(self.pnl_weighted_win_rate, 2),
            "win_rate_denominator": self.win_rate_denominator,
            "expectancy_breakeven_incl": _round_opt(self.expectancy_breakeven_incl),
            "avg_pnl_per_decided": _round_opt(self.avg_pnl_per_decided),
            "total_costs": round(self.total_costs, 2),
            "cost_drag_pct": _round_opt(self.cost_drag_pct, 2),
            "stop_loss_share": _round_opt(self.stop_loss_share, 4),
            "avg_loss_r": _round_opt(self.avg_loss_r, 4),
            "exit_breakdown": dict(self.exit_breakdown),
        }


@dataclass
class DrawdownReport:
    """
    Canonical drawdown state derived from the real equity/balance series.

    ONE methodology, used by dashboard, API and reports alike: peak-to-trough on
    the equity snapshot series, in percent of peak.
    """

    current_equity: float | None = None
    peak_equity: float | None = None
    peak_balance: float | None = None
    current_drawdown_pct: float | None = None
    current_drawdown_usd: float | None = None
    max_drawdown_pct: float | None = None
    max_drawdown_usd: float | None = None
    max_drawdown_at: datetime | None = None
    drawdown_duration_sec: float | None = None
    recovery_duration_sec: float | None = None
    recovery_pct: float | None = None
    in_drawdown: bool = False
    sample_count: int = 0
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for the REST layer."""
        return {
            "has_data": self.has_data,
            "sample_count": self.sample_count,
            "current_equity": _round_opt(self.current_equity),
            "peak_equity": _round_opt(self.peak_equity),
            "peak_balance": _round_opt(self.peak_balance),
            "current_drawdown_pct": _round_opt(self.current_drawdown_pct, 3),
            "current_drawdown_usd": _round_opt(self.current_drawdown_usd),
            "max_drawdown_pct": _round_opt(self.max_drawdown_pct, 3),
            "max_drawdown_usd": _round_opt(self.max_drawdown_usd),
            "max_drawdown_at": self.max_drawdown_at.isoformat() if self.max_drawdown_at else None,
            "drawdown_duration_sec": _round_opt(self.drawdown_duration_sec, 1),
            "recovery_duration_sec": _round_opt(self.recovery_duration_sec, 1),
            "recovery_pct": _round_opt(self.recovery_pct, 3),
            "in_drawdown": self.in_drawdown,
        }


@dataclass
class LiveAccountState:
    """
    Current account truth, sourced from the broker adapter.

    `available=False` means the adapter could not be read. Consumers MUST render
    an explicit unavailable state; there is no synthetic fallback balance.
    """

    available: bool = False
    source: str = "UNAVAILABLE"
    balance: float | None = None
    equity: float | None = None
    floating_pnl: float | None = None
    margin: float | None = None
    margin_free: float | None = None
    margin_level: float | None = None
    currency: str = ""
    leverage: int | None = None
    open_positions: int | None = None
    open_volume: float | None = None
    account_login: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for the REST layer."""
        return {
            "available": self.available,
            "source": self.source,
            "balance": _round_opt(self.balance),
            "equity": _round_opt(self.equity),
            "floating_pnl": _round_opt(self.floating_pnl),
            "margin": _round_opt(self.margin),
            "margin_free": _round_opt(self.margin_free),
            "margin_level": _round_opt(self.margin_level, 2),
            "currency": self.currency,
            "leverage": self.leverage,
            "open_positions": self.open_positions,
            "open_volume": _round_opt(self.open_volume, 2),
            "account_login": self.account_login,
            "error": self.error,
        }


@dataclass
class StrategyContribution:
    """Per-strategy accounting contribution, joined to Experience Intelligence."""

    strategy_id: str
    trade_count: int = 0
    net_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float | None = None
    profit_factor: float | None = None
    average_r: float | None = None
    r_sample_count: int = 0
    worst_trade: float | None = None
    best_trade: float | None = None
    #: Share of total account loss this strategy is responsible for, 0..1.
    loss_share: float | None = None
    lifecycle_state: str = ""
    confidence: float | None = None
    expectancy_r: float | None = None
    recent_expectancy_r: float | None = None
    sample_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for the REST layer."""
        return {
            "strategy_id": self.strategy_id,
            "trade_count": self.trade_count,
            "net_pnl": round(self.net_pnl, 2),
            "gross_profit": round(self.gross_profit, 2),
            "gross_loss": round(self.gross_loss, 2),
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": _round_opt(self.win_rate, 2),
            "profit_factor": _round_opt(self.profit_factor, 3),
            "average_r": _round_opt(self.average_r, 3),
            "r_sample_count": self.r_sample_count,
            "best_trade": _round_opt(self.best_trade),
            "worst_trade": _round_opt(self.worst_trade),
            "loss_share": _round_opt(self.loss_share, 4),
            "lifecycle_state": self.lifecycle_state,
            "confidence": _round_opt(self.confidence, 4),
            "expectancy_r": _round_opt(self.expectancy_r, 4),
            "recent_expectancy_r": _round_opt(self.recent_expectancy_r, 4),
            "sample_count": self.sample_count,
        }


@dataclass
class TradeForensicTrace:
    """
    Full forensic reconstruction of one closed trade.

    Assembled by joining authoritative records only: ledger row, order events,
    experience decision and its outcome, and strategy score at decision time.
    Anything the evidence does not contain stays absent rather than guessed.
    """

    found: bool = False
    ticket: int = 0
    trade: dict[str, Any] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)
    entry: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    position_path: dict[str, Any] = field(default_factory=dict)
    exit_detail: dict[str, Any] = field(default_factory=dict)
    outcome: dict[str, Any] = field(default_factory=dict)
    strategy_context: dict[str, Any] = field(default_factory=dict)
    model_context: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    behavioral_flags: list[str] = field(default_factory=list)
    loss_attribution: str = ""
    order_events: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for the REST layer."""
        return {
            "found": self.found,
            "ticket": self.ticket,
            "trade": self.trade,
            "identity": self.identity,
            "entry": self.entry,
            "risk": self.risk,
            "position_path": self.position_path,
            "exit": self.exit_detail,
            "outcome": self.outcome,
            "strategy_context": self.strategy_context,
            "model_context": self.model_context,
            "quality": self.quality,
            "behavioral_flags": list(self.behavioral_flags),
            "loss_attribution": self.loss_attribution,
            "order_events": self.order_events,
            "notes": list(self.notes),
        }


def _round_opt(value: float | None, digits: int = 2) -> float | None:
    """Rounds a float for transport, preserving `None` as a real 'unavailable'."""
    if value is None:
        return None
    return round(float(value), digits)
