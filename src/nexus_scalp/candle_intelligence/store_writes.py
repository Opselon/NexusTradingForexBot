"""
Candle Intelligence Store — Write API
======================================
Record methods for CandleIntelStore (BUG-061). Every INSERT is built from a
column list + values dict so placeholder counts can never drift from the
argument tuple: placeholders are generated from the same `cols` list.

All writes are bounded, validated (finite numbers only), deterministically
serialized, and carry the common audit columns (ts, symbol, timeframe, regime,
pattern_name, pattern_score, candle_close_classification, decision_type,
risk_state, reason_codes, raw_payload, computed_payload).
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from nexus_scalp.candle_intelligence.models import (
    CandleCloseSummary,
    CandleDecision,
    PatternDetection,
    RegimeState,
    RiskEvaluation,
)
from nexus_scalp.candle_intelligence.store import _common_kwargs, _now_iso


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _bar_ts(t: datetime | str) -> str:
    if isinstance(t, datetime):
        return t.isoformat()
    return str(t)


def _insert(self: Any, table: str, cols: list[str], values: list[Any]) -> int:
    """Enqueue an INSERT OR IGNORE via the store's RAM ring + async worker.

    Hot path: O(1) in-memory op, no disk I/O on the caller's thread. The
    background worker persists the row to SQLite in a batched transaction.
    """
    return 1 if self.enqueue(table, cols, values) else 0


def record_candle(
    self: Any,
    symbol: str,
    timeframe: str,
    timestamp: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 0.0,
    regime: str = "",
    raw_payload: dict[str, Any] | None = None,
) -> bool:
    """Store one raw OHLC candle (idempotent per symbol/timeframe/ts)."""
    if any(not math.isfinite(v) for v in (open_, high, low, close)):
        return False
    ts = _now_iso()
    kw = _common_kwargs(ts, symbol, timeframe, regime=regime, raw_payload=raw_payload)
    cols = [
        "bar_ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "is_complete",
        "ts",
        "symbol",
        "timeframe",
        "regime",
        "pattern_name",
        "pattern_score",
        "candle_close_classification",
        "decision_type",
        "risk_state",
        "reason_codes",
        "raw_payload",
        "computed_payload",
    ]
    vals = [
        _bar_ts(timestamp),
        open_,
        high,
        low,
        close,
        _safe_float(volume),
        1,
        ts,
        symbol,
        timeframe,
        regime,
        "",
        0.0,
        "",
        "",
        "",
        kw["reason_codes"],
        kw["raw_payload"],
        "{}",
    ]
    return _insert(self, "candles", cols, vals) > 0


def record_candle_closure(self: Any, summary: CandleCloseSummary, regime: str = "") -> bool:
    """Store the full close classification for one candle (idempotent)."""
    ts = _now_iso()
    kw = _common_kwargs(
        ts,
        summary.symbol,
        summary.timeframe,
        regime=regime,
        candle_close_classification=summary.close_class.value,
        raw_payload={
            "open": summary.open,
            "high": summary.high,
            "low": summary.low,
            "close": summary.close,
        },
    )
    cols = [
        "bar_ts",
        "open",
        "high",
        "low",
        "close",
        "range",
        "body",
        "upper_wick",
        "lower_wick",
        "body_ratio",
        "upper_wick_ratio",
        "lower_wick_ratio",
        "close_position_in_range",
        "open_to_close_direction",
        "close_strength",
        "rejection_score",
        "continuation_score",
        "reversal_score",
        "indecision_score",
        "momentum_decay_score",
        "close_quality",
        "ts",
        "symbol",
        "timeframe",
        "regime",
        "pattern_name",
        "pattern_score",
        "candle_close_classification",
        "decision_type",
        "risk_state",
        "reason_codes",
        "raw_payload",
        "computed_payload",
    ]
    vals = [
        _bar_ts(summary.timestamp),
        summary.open,
        summary.high,
        summary.low,
        summary.close,
        summary.range,
        summary.body,
        summary.upper_wick,
        summary.lower_wick,
        summary.body_ratio,
        summary.upper_wick_ratio,
        summary.lower_wick_ratio,
        summary.close_position_in_range,
        summary.open_to_close_direction,
        summary.close_strength,
        summary.rejection_score,
        summary.continuation_score,
        summary.reversal_score,
        summary.indecision_score,
        summary.momentum_decay_score,
        summary.close_quality,
        ts,
        summary.symbol,
        summary.timeframe,
        regime,
        "",
        0.0,
        summary.close_class.value,
        "",
        "",
        kw["reason_codes"],
        kw["raw_payload"],
        "{}",
    ]
    return _insert(self, "candle_closures", cols, vals) > 0


def record_patterns(
    self: Any,
    symbol: str,
    timeframe: str,
    timestamp: datetime,
    patterns: list[PatternDetection],
    regime: str = "",
) -> int:
    """Store all detected patterns for one candle. Returns rows written."""
    ts = _now_iso()
    n = 0
    cols = [
        "bar_ts",
        "direction",
        "raw_score",
        "context_weight",
        "confidence_score",
        "requires_confirmation",
        "ts",
        "symbol",
        "timeframe",
        "regime",
        "pattern_name",
        "pattern_score",
        "candle_close_classification",
        "decision_type",
        "risk_state",
        "reason_codes",
        "raw_payload",
        "computed_payload",
    ]
    for p in patterns:
        kw = _common_kwargs(
            ts,
            symbol,
            timeframe,
            regime=regime,
            pattern_name=p.pattern_name,
            pattern_score=p.confidence_score,
            reason_codes=p.reason_codes,
        )
        vals = [
            _bar_ts(timestamp),
            p.direction,
            p.raw_score,
            p.context_weight,
            p.confidence_score,
            1 if p.requires_confirmation else 0,
            ts,
            symbol,
            timeframe,
            regime,
            p.pattern_name,
            p.confidence_score,
            "",
            "",
            "",
            kw["reason_codes"],
            "{}",
            "{}",
        ]
        n += _insert(self, "candle_patterns", cols, vals)
    return n


def record_regime(self: Any, regime: RegimeState) -> bool:
    ts = _now_iso()
    kw = _common_kwargs(
        ts,
        regime.symbol,
        regime.timeframe,
        regime=regime.regime,
        raw_payload={"atr": regime.atr, "spread": regime.spread},
    )
    cols = [
        "bar_ts",
        "volatility_state",
        "atr",
        "spread",
        "ts",
        "symbol",
        "timeframe",
        "regime",
        "pattern_name",
        "pattern_score",
        "candle_close_classification",
        "decision_type",
        "risk_state",
        "reason_codes",
        "raw_payload",
        "computed_payload",
    ]
    vals = [
        _bar_ts(regime.timestamp),
        regime.volatility_state,
        _safe_float(regime.atr),
        _safe_float(regime.spread),
        ts,
        regime.symbol,
        regime.timeframe,
        regime.regime,
        "",
        0.0,
        "",
        "",
        "",
        kw["reason_codes"],
        kw["raw_payload"],
        "{}",
    ]
    return _insert(self, "market_regimes", cols, vals) > 0


def record_risk(self: Any, risk: RiskEvaluation, symbol: str, timeframe: str, ts: datetime) -> bool:
    now = _now_iso()
    kw = _common_kwargs(
        now,
        symbol,
        timeframe,
        risk_state=risk.risk_state.value,
        reason_codes=risk.reason_codes,
    )
    cols = [
        "bar_ts",
        "risk_allowed",
        "risk_notes",
        "ts",
        "symbol",
        "timeframe",
        "regime",
        "pattern_name",
        "pattern_score",
        "candle_close_classification",
        "decision_type",
        "risk_state",
        "reason_codes",
        "raw_payload",
        "computed_payload",
    ]
    vals = [
        _bar_ts(ts),
        1 if risk.risk_allowed else 0,
        "",
        now,
        symbol,
        timeframe,
        "",
        "",
        0.0,
        "",
        "",
        kw["risk_state"],
        kw["reason_codes"],
        "{}",
        "{}",
    ]
    return _insert(self, "risk_evaluations", cols, vals) > 0


def record_decision(self: Any, decision: CandleDecision) -> bool:
    """Store the final decision record (the core audit record)."""
    ts = _now_iso()
    kw = _common_kwargs(
        ts,
        decision.symbol,
        decision.timeframe,
        regime=decision.regime_state.regime,
        candle_close_classification=decision.close_summary.close_class.value,
        decision_type=decision.decision_type.value,
        risk_state=decision.risk_evaluation.risk_state.value,
        reason_codes=decision.reason_codes,
        raw_payload=decision.raw_payload,
        computed_payload=decision.computed_payload,
    )
    cols = [
        "bar_ts",
        "trade_bias",
        "confidence_score",
        "entry_allowed",
        "hold_allowed",
        "fast_exit_required",
        "exit_required",
        "modify_order",
        "cancel_pending",
        "no_trade_reason",
        "ts",
        "symbol",
        "timeframe",
        "regime",
        "pattern_name",
        "pattern_score",
        "candle_close_classification",
        "decision_type",
        "risk_state",
        "reason_codes",
        "raw_payload",
        "computed_payload",
    ]
    vals = [
        _bar_ts(decision.timestamp),
        decision.trade_bias.value,
        decision.confidence_score,
        1 if decision.entry_allowed else 0,
        1 if decision.hold_allowed else 0,
        1 if decision.fast_exit_required else 0,
        1 if decision.exit_required else 0,
        1 if decision.modify_order else 0,
        1 if decision.cancel_pending else 0,
        decision.no_trade_reason,
        ts,
        decision.symbol,
        decision.timeframe,
        kw["regime"],
        "",
        0.0,
        kw["candle_close_classification"],
        kw["decision_type"],
        kw["risk_state"],
        kw["reason_codes"],
        kw["raw_payload"],
        kw["computed_payload"],
    ]
    return _insert(self, "trade_decisions", cols, vals) > 0


def record_veto(
    self: Any,
    symbol: str,
    timeframe: str,
    timestamp: datetime,
    level: int,
    rule: str,
    reason: str,
    regime: str = "",
    reason_codes: list[str] | None = None,
) -> bool:
    ts = _now_iso()
    kw = _common_kwargs(ts, symbol, timeframe, regime=regime, reason_codes=reason_codes or [rule])
    cols = [
        "bar_ts",
        "veto_level",
        "veto_rule",
        "veto_reason",
        "ts",
        "symbol",
        "timeframe",
        "regime",
        "pattern_name",
        "pattern_score",
        "candle_close_classification",
        "decision_type",
        "risk_state",
        "reason_codes",
        "raw_payload",
        "computed_payload",
    ]
    vals = [
        _bar_ts(timestamp),
        int(level),
        rule,
        reason,
        ts,
        symbol,
        timeframe,
        regime,
        "",
        0.0,
        "",
        "",
        "",
        kw["reason_codes"],
        "{}",
        "{}",
    ]
    return _insert(self, "rule_vetoes", cols, vals) > 0


def record_audit_log(
    self: Any,
    symbol: str,
    timeframe: str,
    timestamp: datetime,
    event: str,
    detail: str = "",
    regime: str = "",
) -> bool:
    ts = _now_iso()
    kw = _common_kwargs(ts, symbol, timeframe, regime=regime)
    cols = [
        "bar_ts",
        "event",
        "detail",
        "ts",
        "symbol",
        "timeframe",
        "regime",
        "pattern_name",
        "pattern_score",
        "candle_close_classification",
        "decision_type",
        "risk_state",
        "reason_codes",
        "raw_payload",
        "computed_payload",
    ]
    vals = [
        _bar_ts(timestamp),
        event,
        detail,
        ts,
        symbol,
        timeframe,
        regime,
        "",
        0.0,
        "",
        "",
        "",
        kw["reason_codes"],
        "{}",
        "{}",
    ]
    return _insert(self, "audit_log", cols, vals) > 0
