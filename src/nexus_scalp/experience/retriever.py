"""
Experience Retriever & Context Fingerprinting
=============================================
Phase 08 causal, bounded retrieval.

Responsibilities:

1. Build a BOUNDED `StrategyContext` from live market state. Continuous values
   (ATR, HTF trend) are bucketed before hashing so experiences aggregate into
   families rather than producing one strategy per float vector.
2. Retrieve top-K historical experiences that are CAUSALLY VALID for a decision
   timestamp - exact family match first, hierarchical similarity second.

HARD INVARIANTS
---------------
* Every retrieval is bounded by `top_k` and by the ledger's own
  MAX_RETRIEVAL_LIMIT. No unbounded table scan on any tick.
* Every retrieval filters `decision_timestamp < decision_timestamp_of_now`.
  Future experiences can never inform a past decision.
* `build_context` never mutates the caller's `confluence_tokens` list. (The
  first Phase 08 revision appended to the caller's list, so repeated calls with
  a shared list produced a drifting fingerprint and a different strategy_id for
  identical market state - see agents/bugs.md BUG-009.)
"""

from __future__ import annotations

from datetime import datetime

from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import ExperienceRecord, StrategyContext
from nexus_scalp.features.regime_classifier import MarketRegimeState
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.retriever")

#: Minimum context similarity accepted for hierarchical (non-exact) matching.
MIN_GENERALIZED_SIMILARITY: float = 0.60

#: An exact strategy-family match must hold at least this many causally valid
#: samples before sibling-context evidence is NOT blended in. Below it, the
#: family is too thin to act alone, so context-similar evidence from other
#: strategy families in the same regime/session/setup enriches retrieval
#: (BUG-140 E2E finding). Sample-size safeguards remain in the decision gate.
MIN_FAMILY_SAMPLES_FOR_EXACT_ONLY: int = 5

#: Canonical setup families derived from the existing entry-reason taxonomy.
SETUP_FAMILIES: tuple[str, ...] = (
    "SMC_GOD_MODE",
    "FAST_LIQUIDITY_SWEEP",
    "PREDICTIVE_LIMIT",
    "PURE_AI",
)


class ExperienceRetriever:
    """Builds bounded contexts and retrieves causally valid experiences."""

    def __init__(self, ledger: ExperienceLedger) -> None:
        self.ledger = ledger

    # ------------------------------------------------------------------
    # Context construction
    # ------------------------------------------------------------------

    @staticmethod
    def classify_volatility(atr_value: float) -> str:
        """Buckets ATR into a bounded volatility regime token."""
        atr = float(atr_value or 0.0)
        if atr <= 0.0:
            return "UNKNOWN"
        if atr < 0.80:
            return "LOW"
        if atr > 3.0:
            return "EXTREME"
        if atr > 2.0:
            return "HIGH"
        return "NORMAL"

    @staticmethod
    def classify_session(feature_vector: FeatureVector | None) -> str:
        """
        Derives the session token from the recorded session flags.

        Overlap is checked first because it is the most specific state.
        """
        if feature_vector is None:
            return "ALL"
        if bool(getattr(feature_vector, "session_overlap_london_ny", False)):
            return "OVERLAP_LONDON_NY"
        if bool(getattr(feature_vector, "session_london", False)):
            return "LONDON"
        if bool(getattr(feature_vector, "session_ny", False)):
            return "NY"
        if bool(getattr(feature_vector, "session_tokyo", False)):
            return "TOKYO"
        return "OFF_SESSION"

    @staticmethod
    def classify_trend(feature_vector: FeatureVector | None) -> str:
        """Buckets HTF alignment into a bounded trend token."""
        if feature_vector is None:
            return "NEUTRAL"
        h4 = float(getattr(feature_vector, "htf_h4_trend", 0.0) or 0.0)
        if h4 > 0.0:
            return "BULLISH"
        if h4 < 0.0:
            return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def classify_setup(entry_reason: str = "", execution_mode: str = "") -> str:
        """Maps the policy's reason/mode strings onto a canonical setup family."""
        blob = f"{entry_reason} {execution_mode}".upper()
        if "SMC_GOD_MODE" in blob:
            return "SMC_GOD_MODE"
        if "SWEEP" in blob:
            return "FAST_LIQUIDITY_SWEEP"
        if "LIMIT" in blob:
            return "PREDICTIVE_LIMIT"
        if blob.strip():
            return "PURE_AI"
        return "UNCLASSIFIED"

    @staticmethod
    def build_confluence_fingerprint(
        feature_vector: FeatureVector | None,
        confluence_tokens: list[str] | None = None,
    ) -> str:
        """
        Builds a deterministic confluence digest.

        Works on a LOCAL copy of `confluence_tokens`; the caller's list is never
        mutated, so repeated calls with the same inputs always yield the same
        fingerprint.
        """
        tokens: set[str] = set(confluence_tokens or ())
        if feature_vector is not None:
            if bool(getattr(feature_vector, "fvg_bullish_active", False)):
                tokens.add("FVG_BULL")
            if bool(getattr(feature_vector, "fvg_bearish_active", False)):
                tokens.add("FVG_BEAR")
            if bool(getattr(feature_vector, "choch_bullish", False)):
                tokens.add("CHOCH_BULL")
            if bool(getattr(feature_vector, "choch_bearish", False)):
                tokens.add("CHOCH_BEAR")
            ob_type = int(getattr(feature_vector, "order_block_type", 0) or 0)
            if ob_type != 0:
                tokens.add(f"OB_{ob_type}")
            sweep = int(getattr(feature_vector, "liquidity_sweep_signal", 0) or 0)
            if sweep != 0:
                tokens.add(f"SWEEP_{sweep}")
        return "_".join(sorted(tokens))

    def build_context(
        self,
        symbol: str,
        timeframe: str,
        feature_vector: FeatureVector | None,
        regime_state: MarketRegimeState | None = None,
        session: str | None = None,
        confluence_tokens: list[str] | None = None,
        entry_reason: str = "",
        execution_mode: str = "",
        strategy_version: str = "1.0.0",
    ) -> StrategyContext:
        """
        Constructs a bounded `StrategyContext` and its deterministic family id.

        The returned context is fully self-describing: the same market state
        always maps to the same `strategy_id`, which is what makes experience
        aggregation and pre-trade gating reproducible.
        """
        regime_str = regime_state.regime_type.value if regime_state else "UNKNOWN"
        atr_val = float(getattr(feature_vector, "atr_m1", 0.0) or 0.0) if feature_vector else 0.0

        resolved_session = session or self.classify_session(feature_vector)
        context_fields = {
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_version": strategy_version,
            "session": resolved_session,
            "regime": regime_str,
            "volatility_regime": self.classify_volatility(atr_val),
            "trend_state": self.classify_trend(feature_vector),
            "setup_type": self.classify_setup(entry_reason, execution_mode),
            "confluence_fingerprint": self.build_confluence_fingerprint(
                feature_vector, confluence_tokens
            ),
        }

        probe = StrategyContext(strategy_id="", **context_fields)
        return StrategyContext(
            strategy_id=self.ledger.generate_strategy_id(probe), **context_fields
        )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve_relevant_experiences(
        self,
        context: StrategyContext,
        decision_timestamp: datetime,
        top_k: int = 50,
    ) -> tuple[list[ExperienceRecord], float]:
        """
        Retrieves up to `top_k` causally valid experiences for a context.

        Strategy:
          1. Exact family match (similarity 1.0) - the common, indexed path.
          2. Hierarchical fallback: bounded symbol-scoped scan, keeping only
             contexts above MIN_GENERALIZED_SIMILARITY, ranked by similarity.

        Returns (experiences, similarity). An empty list with 0.0 similarity
        means "no evidence", which the decision gate must treat as
        INSUFFICIENT_EVIDENCE rather than as approval.
        """
        bounded_k = max(1, int(top_k))

        exact = self.ledger.get_experiences_for_strategy(
            strategy_id=context.strategy_id,
            limit=bounded_k,
            before_timestamp=decision_timestamp,
        )
        if len(exact) >= MIN_FAMILY_SAMPLES_FOR_EXACT_ONLY:
            return exact, 1.0

        # Young strategy family (or none): augment strategy-specific evidence
        # with causally-valid, context-similar sibling evidence so regime /
        # session / setup knowledge can still inform the next decision
        # (BUG-140 E2E finding: a 1-sample exact match previously hid 60
        # relevant regime-matched outcomes from the decision gate).
        seen_ids = {r.experience_id for r in exact}
        merged = list(exact)
        candidates = self.ledger.get_experiences_for_symbol(
            symbol=context.symbol,
            limit=bounded_k * 2,
            before_timestamp=decision_timestamp,
        )
        scored: list[tuple[ExperienceRecord, float]] = []
        for rec in candidates:
            if rec.experience_id in seen_ids:
                continue
            sim = self._calculate_context_similarity(context, rec.context)
            if sim >= MIN_GENERALIZED_SIMILARITY:
                scored.append((rec, sim))

        if not merged and not scored:
            return [], 0.0

        if not scored:
            # Exact-only family, but below the exact-only threshold: still
            # return what exists rather than fabricating similarity.
            return exact, 1.0

        scored.sort(key=lambda pair: pair[1], reverse=True)
        remaining = bounded_k - len(merged)
        top = scored[:max(0, remaining)]
        merged.extend(r for r, _ in top)

        sims = [1.0] * len(exact) + [s for _, s in top]
        avg_sim = float(sum(sims) / len(sims)) if sims else 0.0
        return merged, round(avg_sim, 4)

    @staticmethod
    def _calculate_context_similarity(c1: StrategyContext, c2: StrategyContext) -> float:
        """
        Weighted context similarity in [0.0, 1.0].

        Regime and trend dominate because they determine whether a historical
        outcome is even relevant; session and setup refine the match.
        """
        weights = {
            "symbol": 0.15,
            "regime": 0.25,
            "trend_state": 0.20,
            "volatility_regime": 0.15,
            "session": 0.10,
            "setup_type": 0.15,
        }
        score = 0.0
        if c1.symbol == c2.symbol:
            score += weights["symbol"]
        if c1.regime == c2.regime:
            score += weights["regime"]
        if c1.trend_state == c2.trend_state:
            score += weights["trend_state"]
        if c1.volatility_regime == c2.volatility_regime:
            score += weights["volatility_regime"]
        if c1.session == c2.session or "ALL" in (c1.session, c2.session):
            score += weights["session"]
        if c1.setup_type == c2.setup_type:
            score += weights["setup_type"]
        return float(round(score, 4))
