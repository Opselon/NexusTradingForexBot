"""
Candle Intelligence Decision Engine
====================================
Turns one candle-close summary + pattern detections + regime + risk inputs into
a single immutable decision (BUG-061). The candle close is a GATE: a weak,
contradictory or invalid close downgrades confidence, blocks entry, or
accelerates exit — before any pattern or risk logic runs.

Rule hierarchy (spec §10):
  1. hard safety veto      (invalid data / NaN / malformed)
  2. regime filter         (unsafe regime -> no trade)
  3. candle-close validation (close quality gate)
  4. pattern confirmation  (multi-factor, never single-pattern)
  5. risk sizing           (risk_state must allow)
  6. execution decision    (entry / hold / fast-exit / modify / cancel / none)

Deterministic: same inputs -> same decision. Every veto stores reason codes.
"""

from __future__ import annotations

from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.candle_intelligence.models import (
    CandleCloseClass,
    CandleCloseSummary,
    CandleDecision,
    DecisionType,
    PatternDetection,
    RegimeState,
    RiskEvaluation,
    RiskState,
    TradeBias,
)


class CandleDecisionEngine:
    """Pure decision logic; never touches an adapter or order manager."""

    #: Regimes that hard-block entry (conservative set).
    BLOCKED_REGIMES = frozenset({"UNKNOWN", "ERRATIC", "CRASH", "GAP_FILL_HUNT", "NEWS_SPIKE"})

    def __init__(self, config: CandleIntelligenceConfig | None = None) -> None:
        self.config = config or CandleIntelligenceConfig()

    def decide(
        self,
        close_summary: CandleCloseSummary,
        patterns: list[PatternDetection],
        regime: RegimeState,
        risk: RiskEvaluation | None = None,
        prior_bias: TradeBias | None = None,
        holding_position: bool = False,
        position_pnl: float | None = None,
    ) -> CandleDecision:
        """Produce the full decision record for one candle close."""
        cfg = self.config
        risk = risk or RiskEvaluation()
        reason_codes: list[str] = []
        bias = TradeBias.NEUTRAL
        confidence = 0.0
        entry_allowed = False
        hold_allowed = True
        fast_exit = False
        exit_required = False
        modify = False
        cancel = False
        decision = DecisionType.NO_TRADE
        no_trade_reason = ""

        # ---------------------------------------------------------------
        # Level 1: HARD SAFETY VETO
        # ---------------------------------------------------------------
        if close_summary.close_class == CandleCloseClass.INVALID:
            no_trade_reason = "INVALID_CANDLE_DATA"
            reason_codes.append("VETO:INVALID_CANDLE")
            return self._build(
                close_summary,
                patterns,
                regime,
                risk,
                bias,
                0.0,
                entry_allowed=False,
                hold_allowed=False,
                fast_exit=False,
                exit_required=holding_position,
                decision=DecisionType.NO_TRADE,
                no_trade_reason=no_trade_reason,
                reason_codes=reason_codes,
            )

        if not risk.risk_allowed or risk.risk_state == RiskState.BLOCKED:
            no_trade_reason = "RISK_BLOCKED"
            reason_codes.append("VETO:RISK_BLOCKED")
            return self._build(
                close_summary,
                patterns,
                regime,
                risk,
                TradeBias.NO_TRADE,
                0.0,
                entry_allowed=False,
                hold_allowed=holding_position,
                fast_exit=holding_position,
                exit_required=holding_position,
                decision=DecisionType.EXIT if holding_position else DecisionType.NO_TRADE,
                no_trade_reason=no_trade_reason,
                reason_codes=reason_codes,
            )

        # ---------------------------------------------------------------
        # Level 2: REGIME FILTER
        # ---------------------------------------------------------------
        regime_name = (regime.regime or "UNKNOWN").upper()
        if regime_name in self.BLOCKED_REGIMES:
            no_trade_reason = f"REGIME_BLOCKED:{regime_name}"
            reason_codes.append(f"VETO:REGIME:{regime_name}")
            return self._build(
                close_summary,
                patterns,
                regime,
                risk,
                TradeBias.NO_TRADE,
                0.0,
                entry_allowed=False,
                hold_allowed=holding_position,
                fast_exit=holding_position,
                exit_required=holding_position,
                decision=DecisionType.EXIT if holding_position else DecisionType.NO_TRADE,
                no_trade_reason=no_trade_reason,
                reason_codes=reason_codes,
            )

        # ---------------------------------------------------------------
        # Level 3: CANDLE-CLOSE VALIDATION (THE GATE)
        # ---------------------------------------------------------------
        cc = close_summary.close_class
        close_blocks = cc in (
            CandleCloseClass.INDECISION,
            CandleCloseClass.WEAK_CLOSE,
            CandleCloseClass.TRAPPED_BREAKOUT,
            CandleCloseClass.FALSE_BREAKOUT,
            CandleCloseClass.EXHAUSTION,
        )

        # Derive trade bias from close class (shape level).
        bias, close_conf = self._bias_from_close(cc, close_summary)
        confidence = close_conf
        reason_codes.append(f"CLOSE:{cc.value}")

        if cc in (CandleCloseClass.BULLISH_REVERSAL, CandleCloseClass.BEARISH_REVERSAL):
            reason_codes.append("CLOSE:REVERSAL_SHAPE")

        # False breakout / trapped breakout: reduce confidence immediately.
        if cc == CandleCloseClass.FALSE_BREAKOUT:
            confidence -= cfg.false_breakout_reduces_confidence_by
            reason_codes.append("CONF:-FALSE_BREAKOUT")
        elif cc == CandleCloseClass.TRAPPED_BREAKOUT:
            confidence -= cfg.trapped_breakout_reduces_confidence_by
            reason_codes.append("CONF:-TRAPPED_BREAKOUT")

        confidence = max(0.0, min(1.0, confidence))

        # ---------------------------------------------------------------
        # Level 4: PATTERN CONFIRMATION (multi-factor)
        # ---------------------------------------------------------------
        aligned_patterns = self._aligned_patterns(patterns, bias)
        if aligned_patterns and confidence < cfg.entry_min_confidence:
            # A strong aligned pattern can lift a decent close over the gate.
            best = max(p.confidence_score for p in aligned_patterns)
            if best >= cfg.pattern_min_confidence:
                confidence = max(confidence, (close_conf + best) / 2.0)
                reason_codes.append(f"PATTERN:+{aligned_patterns[0].pattern_name}")

        pattern_confirms = len(aligned_patterns) >= cfg.multi_factor_min_confirmations
        if not pattern_confirms and confidence < cfg.entry_min_confidence:
            reason_codes.append("PATTERN:INSUFFICIENT_CONFIRMATION")

        # ---------------------------------------------------------------
        # Level 5: RISK STATE
        # ---------------------------------------------------------------
        risk_cap = 1.0
        if risk.risk_state == RiskState.CAUTION:
            risk_cap = 0.6
            reason_codes.append("RISK:CAUTION_CAP")
        elif risk.risk_state == RiskState.REDUCED:
            risk_cap = 0.4
            reason_codes.append("RISK:REDUCED_CAP")
        confidence = min(confidence, risk_cap)

        # ---------------------------------------------------------------
        # Level 6: EXECUTION DECISION
        # ---------------------------------------------------------------
        if holding_position:
            entry_allowed = False
            decision, fast_exit, exit_required, hold_allowed = self._manage_position(
                cc, confidence, position_pnl
            )
            if fast_exit:
                reason_codes.append("EXEC:FAST_EXIT")
            if exit_required:
                reason_codes.append("EXEC:EXIT_REQUIRED")
            if decision == DecisionType.MODIFY_SL_TP:
                modify = True
                reason_codes.append("EXEC:MODIFY")
            if decision == DecisionType.CANCEL_PENDING:
                cancel = True
                reason_codes.append("EXEC:CANCEL_PENDING")
        # No position: entry or no-trade.
        elif close_blocks and cfg.weak_close_blocks_entry:
            no_trade_reason = f"WEAK_CLOSE_BLOCKS_ENTRY:{cc.value}"
            reason_codes.append(f"VETO:WEAK_CLOSE:{cc.value}")
            decision = DecisionType.NO_TRADE
        elif confidence >= cfg.entry_min_confidence and bias != TradeBias.NEUTRAL:
            entry_allowed = True
            decision = DecisionType.ENTRY
            reason_codes.append("EXEC:ENTRY_ALLOWED")
        else:
            no_trade_reason = "CONFIDENCE_BELOW_ENTRY_GATE"
            reason_codes.append("VETO:LOW_CONFIDENCE")
            decision = DecisionType.NO_TRADE

        if no_trade_reason and not no_trade_reason:
            no_trade_reason = "NO_TRADE"

        return self._build(
            close_summary,
            patterns,
            regime,
            risk,
            bias,
            confidence,
            entry_allowed=entry_allowed,
            hold_allowed=hold_allowed,
            fast_exit=fast_exit,
            exit_required=exit_required,
            modify_order=modify,
            cancel_pending=cancel,
            decision=decision,
            no_trade_reason=no_trade_reason,
            reason_codes=reason_codes,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _bias_from_close(
        self, cc: CandleCloseClass, s: CandleCloseSummary
    ) -> tuple[TradeBias, float]:
        """Map close class -> trade bias + base confidence."""
        if cc in (
            CandleCloseClass.BULLISH_CONTINUATION,
            CandleCloseClass.BULLISH_REVERSAL,
        ):
            return TradeBias.BULLISH, s.close_strength * 0.8 + 0.2
        if cc in (
            CandleCloseClass.BEARISH_CONTINUATION,
            CandleCloseClass.BEARISH_REVERSAL,
        ):
            return TradeBias.BEARISH, s.close_strength * 0.8 + 0.2
        if cc == CandleCloseClass.EXHAUSTION:
            # Exhaustion implies the END of the current direction.
            return TradeBias.NEUTRAL, s.rejection_score * 0.6
        if cc == CandleCloseClass.TRAPPED_BREAKOUT:
            return TradeBias.NEUTRAL, s.rejection_score * 0.5
        if cc == CandleCloseClass.FALSE_BREAKOUT:
            return TradeBias.NEUTRAL, 0.3
        if cc == CandleCloseClass.WEAK_CLOSE:
            return TradeBias.NEUTRAL, s.close_strength * 0.4
        return TradeBias.NEUTRAL, s.indecision_score * 0.3

    def _aligned_patterns(
        self, patterns: list[PatternDetection], bias: TradeBias
    ) -> list[PatternDetection]:
        if bias == TradeBias.NEUTRAL:
            return []
        want = "BULLISH" if bias == TradeBias.BULLISH else "BEARISH"
        return [p for p in patterns if p.direction == want]

    def _manage_position(
        self,
        cc: CandleCloseClass,
        confidence: float,
        position_pnl: float | None,
    ) -> tuple[DecisionType, bool, bool, bool]:
        """Position-management branch: hold / fast-exit / exit / modify."""
        cfg = self.config

        # Hard close classes always accelerate exit evaluation.
        if cc in (
            CandleCloseClass.TRAPPED_BREAKOUT,
            CandleCloseClass.FALSE_BREAKOUT,
        ):
            return DecisionType.FAST_EXIT, True, True, False

        if cc == CandleCloseClass.EXHAUSTION:
            # Reduce exposure: trail/modify toward breakeven, allow hold.
            return DecisionType.MODIFY_SL_TP, False, False, True

        if cc == CandleCloseClass.INDECISION and confidence < cfg.hold_min_confidence:
            return DecisionType.FAST_EXIT, True, False, False

        if position_pnl is not None and position_pnl < 0:
            # Losing position + degrading close -> fast exit.
            if cc == CandleCloseClass.WEAK_CLOSE and confidence < cfg.fast_exit_confidence:
                return DecisionType.FAST_EXIT, True, False, False

        if confidence < cfg.hold_min_confidence:
            return DecisionType.NO_TRADE, False, False, False

        return DecisionType.HOLD, False, False, True

    def _build(
        self,
        close_summary: CandleCloseSummary,
        patterns: list[PatternDetection],
        regime: RegimeState,
        risk: RiskEvaluation,
        bias: TradeBias,
        confidence: float,
        *,
        entry_allowed: bool,
        hold_allowed: bool,
        fast_exit: bool,
        exit_required: bool,
        decision: DecisionType,
        no_trade_reason: str,
        reason_codes: list[str],
        modify_order: bool = False,
        cancel_pending: bool = False,
    ) -> CandleDecision:
        return CandleDecision(
            symbol=close_summary.symbol,
            timeframe=close_summary.timeframe,
            timestamp=close_summary.timestamp,
            close_summary=close_summary,
            detected_patterns=patterns,
            regime_state=regime,
            risk_evaluation=risk,
            trade_bias=bias,
            confidence_score=round(max(0.0, min(1.0, confidence)), 6),
            entry_allowed=entry_allowed,
            hold_allowed=hold_allowed,
            fast_exit_required=fast_exit,
            exit_required=exit_required,
            modify_order=modify_order,
            cancel_pending=cancel_pending,
            decision_type=decision,
            no_trade_reason=no_trade_reason,
            reason_codes=reason_codes,
            computed_payload={
                "close_class": close_summary.close_class.value,
                "close_quality": close_summary.close_quality,
                "body_ratio": close_summary.body_ratio,
                "close_strength": close_summary.close_strength,
                "rejection_score": close_summary.rejection_score,
                "continuation_score": close_summary.continuation_score,
                "reversal_score": close_summary.reversal_score,
                "indecision_score": close_summary.indecision_score,
                "momentum_decay_score": close_summary.momentum_decay_score,
                "patterns": [
                    {"name": p.pattern_name, "score": p.confidence_score} for p in patterns
                ],
            },
        )
