"""
Candle Intelligence Engine
==========================
Orchestrator for the local, isolated candle-close analysis module (BUG-061).

Ingests ticks / OHLC bars / candle-close events, runs the close classifier,
the pattern engine and the decision engine, and persists every intermediate
and final result to the isolated SQLite store. Produces the spec §11 output
contract for every processed candle.

The candle close is a GATE: no entry/hold/fast-exit decision is made without
close validation unless a higher-priority rule (hard veto) mandates it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nexus_scalp.candle_intelligence.classifier import CandleCloseClassifier
from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.candle_intelligence.decision import CandleDecisionEngine
from nexus_scalp.candle_intelligence.models import (
    CandleCloseSummary,
    PatternDetection,
    RegimeState,
    RiskEvaluation,
    TradeBias,
)
from nexus_scalp.candle_intelligence.patterns import Candle, PatternEngine
from nexus_scalp.candle_intelligence.store import CandleIntelStore
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.candle_intelligence.engine")


class CandleOutput:
    """Spec §11 output contract for one processed candle."""

    __slots__ = (
        "candle_close_summary",
        "confidence_score",
        "database_write_status",
        "detected_patterns",
        "entry_allowed",
        "fast_exit_required",
        "hold_allowed",
        "no_trade_reason",
        "regime_state",
        "trade_bias",
    )

    def __init__(
        self,
        candle_close_summary: CandleCloseSummary,
        detected_patterns: list[PatternDetection],
        regime_state: RegimeState,
        trade_bias: TradeBias,
        confidence_score: float,
        entry_allowed: bool,
        hold_allowed: bool,
        fast_exit_required: bool,
        no_trade_reason: str,
        database_write_status: str,
    ) -> None:
        self.candle_close_summary = candle_close_summary
        self.detected_patterns = detected_patterns
        self.regime_state = regime_state
        self.trade_bias = trade_bias
        self.confidence_score = confidence_score
        self.entry_allowed = entry_allowed
        self.hold_allowed = hold_allowed
        self.fast_exit_required = fast_exit_required
        self.no_trade_reason = no_trade_reason
        self.database_write_status = database_write_status

    def to_dict(self) -> dict[str, Any]:
        return {
            "candle_close_summary": self.candle_close_summary.model_dump_for_db(),
            "detected_patterns": [p.model_dump() for p in self.detected_patterns],
            "regime_state": self.regime_state.model_dump(),
            "trade_bias": self.trade_bias.value,
            "confidence_score": self.confidence_score,
            "entry_allowed": self.entry_allowed,
            "hold_allowed": self.hold_allowed,
            "fast_exit_required": self.fast_exit_required,
            "no_trade_reason": self.no_trade_reason,
            "database_write_status": self.database_write_status,
        }


class CandleIntelligenceEngine:
    """Local candle-close analysis + decision pipeline."""

    def __init__(
        self,
        config: CandleIntelligenceConfig | None = None,
        store: CandleIntelStore | None = None,
    ) -> None:
        self.config = config or CandleIntelligenceConfig()
        self.store = store or CandleIntelStore(self.config)
        self.classifier = CandleCloseClassifier(self.config)
        self.pattern_engine = PatternEngine(self.config)
        self.decision_engine = CandleDecisionEngine(self.config)

        # Window of recent candles for multi-candle patterns.
        self._window: list[Candle] = []
        self._max_window = 12

        self.decision_count = 0
        self.entry_count = 0
        self.fast_exit_count = 0
        self.no_trade_count = 0

    # ------------------------------------------------------------------
    # ingest API
    # ------------------------------------------------------------------

    def ingest_bar(
        self,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
        is_complete: bool = True,
        regime_state: RegimeState | None = None,
        risk: RiskEvaluation | None = None,
        holding_position: bool = False,
        position_pnl: float | None = None,
    ) -> CandleOutput | None:
        """Ingest one OHLC bar. Full pipeline runs only on COMPLETE bars
        (candle-close events). Forming bars are stored raw, not decided."""
        self._window.append(Candle(symbol, timeframe, timestamp, open_, high, low, close, volume))
        if len(self._window) > self._max_window:
            self._window = self._window[-self._max_window :]
        self.store.record_candle(
            symbol,
            timeframe,
            timestamp,
            open_,
            high,
            low,
            close,
            volume,
            regime=regime_state.regime if regime_state else "",
            raw_payload={"is_complete": is_complete},
        )
        if not is_complete:
            return None
        return self.process_candle_close(
            symbol,
            timeframe,
            timestamp,
            open_,
            high,
            low,
            close,
            volume,
            regime_state=regime_state,
            risk=risk,
            holding_position=holding_position,
            position_pnl=position_pnl,
        )

    def process_candle_close(
        self,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
        regime_state: RegimeState | None = None,
        risk: RiskEvaluation | None = None,
        holding_position: bool = False,
        position_pnl: float | None = None,
    ) -> CandleOutput:
        """Run the FULL pipeline on a completed candle (the gate)."""
        summary = self.classifier.classify(symbol, timeframe, timestamp, open_, high, low, close)
        self.store.record_candle_closure(
            summary, regime=regime_state.regime if regime_state else ""
        )

        regime = regime_state or RegimeState(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
        )
        self.store.record_regime(regime)

        patterns = self.pattern_engine.detect(self._window, regime=regime)
        self.store.record_patterns(symbol, timeframe, timestamp, patterns, regime=regime.regime)

        risk_eval = risk or RiskEvaluation()
        self.store.record_risk(risk_eval, symbol, timeframe, timestamp)

        decision = self.decision_engine.decide(
            close_summary=summary,
            patterns=patterns,
            regime=regime,
            risk=risk_eval,
            holding_position=holding_position,
            position_pnl=position_pnl,
        )
        ok = self.store.record_decision(decision)
        status = "WRITTEN" if ok else "WRITE_FAILED"

        # Counters + audit log on vetoes.
        self.decision_count += 1
        if decision.entry_allowed:
            self.entry_count += 1
        if decision.fast_exit_required:
            self.fast_exit_count += 1
        if decision.decision_type.value == "NO_TRADE":
            self.no_trade_count += 1
            if decision.no_trade_reason:
                self.store.record_veto(
                    symbol,
                    timeframe,
                    timestamp,
                    level=3,
                    rule=decision.no_trade_reason,
                    reason=decision.no_trade_reason,
                    regime=regime.regime,
                    reason_codes=decision.reason_codes,
                )

        return CandleOutput(
            candle_close_summary=summary,
            detected_patterns=patterns,
            regime_state=regime,
            trade_bias=decision.trade_bias,
            confidence_score=decision.confidence_score,
            entry_allowed=decision.entry_allowed,
            hold_allowed=decision.hold_allowed,
            fast_exit_required=decision.fast_exit_required,
            no_trade_reason=decision.no_trade_reason,
            database_write_status=status,
        )

    def ingest_tick(self) -> None:
        """Placeholder for tick ingestion — bars are the decision unit."""
        return None  # ticks feed the bar aggregator upstream; no decision here

    # ------------------------------------------------------------------
    # queries (bounded read facade)
    # ------------------------------------------------------------------

    def recent_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.query_recent("trade_decisions", limit)

    def recent_closures(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.query_recent("candle_closures", limit)

    def recent_vetoes(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.query_recent("rule_vetoes", limit)

    def db_size_bytes(self) -> int:
        return self.store.db_size_bytes()
