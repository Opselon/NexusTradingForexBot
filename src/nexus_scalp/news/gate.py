"""News decision gate (PHASE 12).

Bounded integration of News Intelligence into the existing trading decision
path. HARD RULES (enforced by tests):

    * NEWS can NEVER force BUY or SELL.
    * NEWS can NEVER bypass RiskEngine / exposure / kill switch / position
      protection.
    * News influence is a bounded contextual modifier: a news alignment may
      adjust a proposal's confidence by at most ``max_confidence_boost``
      (default 0.05) and a conflict may lower confidence by at most
      ``max_confidence_penalty`` (default 0.10) - never by an unbounded
      amount.
    * News materially influences decisions ONLY when the strategy direction
      aligns with the news direction (and regime alignment is passed in by
      the caller): aligned -> bounded boost; conflicted -> CAUTION / NO_TRADE
      for otherwise-weak setups.

The gate holds NO adapter, NO order manager, NO risk engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.news.config import NewsConfig
from nexus_scalp.news.models import CurrentNewsContext, NewsDirection, NewsState


#: Actions the news gate may decide.
class NewsGateDecision:
    CONFIRM = "CONFIRM"
    CONFLICT = "CONFLICT"
    IGNORE = "IGNORE"
    CAUTION = "CAUTION"


@dataclass
class NewsGateVerdict:
    """Explainable outcome of the news gate for one proposal."""

    decision: str = NewsGateDecision.IGNORE
    confidence_adjustment: float = 0.0  # signed, bounded
    news_direction: str = "NEUTRAL"
    strategy_direction: str = "NEUTRAL"
    aligned: bool = False
    conflicted: bool = False
    cautioned: bool = False
    blocked: bool = False
    reason: str = ""
    context_state: str = NewsState.NORMAL.value
    news_relevance: float = 0.0
    news_confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "confidence_adjustment": self.confidence_adjustment,
            "news_direction": self.news_direction,
            "strategy_direction": self.strategy_direction,
            "aligned": self.aligned,
            "conflicted": self.conflicted,
            "cautioned": self.cautioned,
            "blocked": self.blocked,
            "reason": self.reason,
            "context_state": self.context_state,
            "news_relevance": self.news_relevance,
            "news_confidence": self.news_confidence,
            "notes": self.notes,
        }


class NewsGate:
    """Bounded news -> decision modifier (never a decision engine itself)."""

    def __init__(self, config: NewsConfig | None = None) -> None:
        self.config = config or NewsConfig()

    # ------------------------------------------------------------------
    # Main evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        context: CurrentNewsContext,
        proposal_action: str,
        strategy_direction: str,
        proposal_confidence: float,
        regime_aligned: bool,
    ) -> NewsGateVerdict:
        """Evaluates one proposal against the current news context.

        Args:
            context: cached CurrentNewsContext (safe defaults when news off).
            proposal_action: the proposal's action (BUY/SELL/NO_TRADE/...).
            strategy_direction: 'BULLISH' / 'BEARISH' / 'NEUTRAL' inferred
                from the strategy/model signal underlying the proposal.
            proposal_confidence: the pre-news confidence (0..1).
            regime_aligned: whether the market regime aligns with the
                strategy direction (supplied by the caller - the news gate
                itself never classifies the regime).
        """
        bounds = self.config.bounds

        # News unavailable / stale -> IGNORE (no fake influence).
        if not context.available or context.stale:
            return NewsGateVerdict(
                decision=NewsGateDecision.IGNORE,
                reason="NEWS_UNAVAILABLE_OR_STALE",
                context_state=context.state.value,
            )

        if proposal_action in (
            "NO_TRADE",
            "CLOSE_POSITION",
            "PARTIAL_CLOSE",
            "MODIFY_SL_TP",
            "CANCEL_ORDER",
        ):
            # Position-protection actions are NEVER gated by news.
            return NewsGateVerdict(
                decision=NewsGateDecision.IGNORE,
                reason="NON_ENTRY_ACTION_NOT_GATED",
                context_state=context.state.value,
            )

        # Blocking states: HIGH_IMPACT / BREAKING with an active relevant
        # event -> CAUTION with optional block for weak setups.
        is_entry = proposal_action in (
            "BUY",
            "SELL",
            "BUY_MARKET",
            "SELL_MARKET",
            "BUY_LIMIT",
            "SELL_LIMIT",
            "BUY_STOP",
            "SELL_STOP",
        )
        if not is_entry:
            return NewsGateVerdict(
                decision=NewsGateDecision.IGNORE,
                reason="NON_ENTRY_ACTION_NOT_GATED",
                context_state=context.state.value,
            )

        relevance = max(context.xauusd_relevance, context.usd_relevance)
        if relevance < 0.25:
            # News has no meaningful bearing on this asset -> IGNORE.
            return NewsGateVerdict(
                decision=NewsGateDecision.IGNORE,
                reason="LOW_NEWS_RELEVANCE",
                context_state=context.state.value,
                news_relevance=relevance,
                news_confidence=context.confidence,
            )

        news_dir = self._net_direction(context)
        verdict = NewsGateVerdict(
            strategy_direction=strategy_direction,
            news_direction=news_dir,
            context_state=context.state.value,
            news_relevance=relevance,
            news_confidence=context.confidence,
        )

        if context.state in bounds.blocked_states and context.confidence >= 0.35:
            verdict.decision = NewsGateDecision.CAUTION
            verdict.cautioned = True
            verdict.blocked = proposal_confidence < 0.6  # only weak setups blocked
            verdict.reason = "HIGH_IMPACT_NEWS_EVENT_CAUTION"
            verdict.notes.append("event window: risk-off posture (bounded)")
            return verdict

        if context.state in bounds.caution_states:
            verdict.decision = NewsGateDecision.CAUTION
            verdict.cautioned = True
            verdict.reason = "NEWS_CONFLICTED_OR_ELEVATED_CAUTION"
            verdict.confidence_adjustment = -min(
                bounds.max_confidence_penalty * 0.5, proposal_confidence * 0.5
            )

        # Alignment check: news direction vs strategy direction.
        aligned = self._aligned(news_dir, strategy_direction)
        conflicted = self._conflicted(news_dir, strategy_direction)
        verdict.aligned = aligned
        verdict.conflicted = conflicted

        if aligned and regime_aligned:
            boost = bounds.max_confidence_boost * context.confidence * relevance
            verdict.decision = NewsGateDecision.CONFIRM
            verdict.confidence_adjustment = round(min(boost, bounds.max_confidence_boost), 4)
            verdict.reason = "NEWS_STRATEGY_REGIME_ALIGNED"
            verdict.notes.append("bounded confidence boost")
        elif conflicted:
            # Strategy and news disagree -> bounded caution, never direction flip.
            penalty = bounds.max_confidence_penalty * context.confidence
            penalty = min(penalty, bounds.max_confidence_penalty)
            verdict.decision = NewsGateDecision.CONFLICT
            verdict.confidence_adjustment = round(-penalty, 4)
            verdict.reason = "NEWS_STRATEGY_CONFLICT_CAUTION"
            verdict.notes.append("news must never force direction")
        else:
            verdict.decision = NewsGateDecision.IGNORE
            verdict.reason = "NEWS_NEUTRAL_OR_WEAK_ALIGNMENT"

        return verdict

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _net_direction(self, context: CurrentNewsContext) -> str:
        net = context.bullish_score - context.bearish_score
        if net > 0.08:
            return NewsDirection.BULLISH.value
        if net < -0.08:
            return NewsDirection.BEARISH.value
        if context.conflict_score > context.confidence:
            return NewsDirection.CONFLICTED.value
        return NewsDirection.NEUTRAL.value

    @staticmethod
    def _aligned(news_dir: str, strategy_dir: str) -> bool:
        if news_dir in (
            NewsDirection.NEUTRAL.value,
            NewsDirection.MIXED.value,
            NewsDirection.CONFLICTED.value,
        ):
            return False
        return news_dir == strategy_dir

    @staticmethod
    def _conflicted(news_dir: str, strategy_dir: str) -> bool:
        if strategy_dir in (NewsDirection.NEUTRAL.value, NewsDirection.CONFLICTED.value):
            return False
        if news_dir in (NewsDirection.NEUTRAL.value, NewsDirection.MIXED.value):
            return False
        return news_dir != strategy_dir
