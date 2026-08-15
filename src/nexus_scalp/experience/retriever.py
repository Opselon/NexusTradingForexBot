"""
Experience Retriever Subsystem
==============================
Phase 08 Experience Intelligence context matching and experience retrieval.

Provides sparse, hierarchical context fingerprinting and similarity-based retrieval
for top-K historical experiences matching live market state.
"""

from datetime import datetime

from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import ExperienceRecord, StrategyContext
from nexus_scalp.features.regime_classifier import MarketRegimeState
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.retriever")


class ExperienceRetriever:
    """
    Retrieves causally valid historical experience records and strategy candidates
    matching live market context.
    """

    def __init__(self, ledger: ExperienceLedger) -> None:
        self.ledger = ledger

    def build_context(
        self,
        symbol: str,
        timeframe: str,
        feature_vector: FeatureVector,
        regime_state: MarketRegimeState | None = None,
        session: str = "ALL",
        confluence_tokens: list[str] | None = None,
    ) -> StrategyContext:
        """
        Constructs a sparse, hierarchical StrategyContext fingerprint from live market state.
        """
        regime_str = regime_state.regime_type.value if regime_state else "UNKNOWN"

        # Determine volatility regime from M1 ATR relative to baseline
        raw_atr = getattr(feature_vector, "atr_m1", 1.50)
        atr_val = float(raw_atr) if raw_atr is not None else 1.50

        if atr_val < 0.80:
            vol_regime = "LOW"
        elif atr_val > 3.0:
            vol_regime = "EXTREME"
        elif atr_val > 2.0:
            vol_regime = "HIGH"
        else:
            vol_regime = "NORMAL"

        # Determine higher-timeframe trend state
        h4_trend = float(getattr(feature_vector, "htf_h4_trend", 0.0) or 0.0)
        if h4_trend > 0.0:
            trend_state = "BULLISH"
        elif h4_trend < 0.0:
            trend_state = "BEARISH"
        else:
            trend_state = "NEUTRAL"

        # Construct confluence fingerprint string
        tokens = confluence_tokens or []
        if getattr(feature_vector, "fvg_bullish_active", False):
            tokens.append("FVG_BULL")
        if getattr(feature_vector, "fvg_bearish_active", False):
            tokens.append("FVG_BEAR")
        if getattr(feature_vector, "choch_bullish", False):
            tokens.append("CHOCH_BULL")
        if getattr(feature_vector, "choch_bearish", False):
            tokens.append("CHOCH_BEAR")
        if getattr(feature_vector, "order_block_type", 0) != 0:
            tokens.append(f"OB_{getattr(feature_vector, 'order_block_type', 0)}")

        confluence_str = "_".join(sorted(tokens))

        temp_ctx = StrategyContext(
            strategy_id="",
            symbol=symbol,
            timeframe=timeframe,
            session=session,
            regime=regime_str,
            volatility_regime=vol_regime,
            trend_state=trend_state,
            confluence_fingerprint=confluence_str,
        )

        strategy_id = self.ledger.generate_strategy_id(temp_ctx)
        return StrategyContext(
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            session=session,
            regime=regime_str,
            volatility_regime=vol_regime,
            trend_state=trend_state,
            confluence_fingerprint=confluence_str,
        )

    def retrieve_relevant_experiences(
        self,
        context: StrategyContext,
        decision_timestamp: datetime,
        top_k: int = 50,
    ) -> tuple[list[ExperienceRecord], float]:
        """
        Retrieves top-K historical experiences matching the given context before decision_timestamp.
        Returns a tuple of (retrieved_experiences, similarity_score).
        """
        # 1. Exact Strategy ID match query
        exact_matches = self.ledger.get_experiences_for_strategy(
            strategy_id=context.strategy_id,
            limit=top_k,
            before_timestamp=decision_timestamp,
        )

        if exact_matches:
            return exact_matches, 1.0

        # 2. Hierarchical / Generalized Match (matching symbol + regime + trend_state)
        # Query experiences table for generalized matches
        if not self.ledger.audit_repo._is_sqlite:
            return [], 0.0

        import json
        import sqlite3

        generalized_matches = []
        try:
            with sqlite3.connect(self.ledger.audit_repo._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                query = """
                    SELECT payload FROM audit_experiences
                    WHERE symbol = ? AND decision_timestamp < ?
                    ORDER BY decision_timestamp DESC LIMIT ?;
                """
                cursor = conn.execute(
                    query, (context.symbol, decision_timestamp.isoformat(), top_k * 2)
                )

                for row in cursor.fetchall():
                    raw_payload = row["payload"]
                    if raw_payload:
                        data = json.loads(raw_payload)
                        rec = ExperienceRecord.model_validate(data)
                        # Compute similarity
                        sim = self._calculate_context_similarity(context, rec.context)
                        if sim >= 0.50:
                            generalized_matches.append((rec, sim))

        except Exception as e:
            logger.error("Failed to retrieve generalized experiences", error=str(e))

        if not generalized_matches:
            return [], 0.0

        # Sort by similarity score descending
        generalized_matches.sort(key=lambda x: x[1], reverse=True)
        top_records = [m[0] for m in generalized_matches[:top_k]]
        avg_sim = float(sum(m[1] for m in generalized_matches[:top_k]) / len(top_records))

        return top_records, avg_sim

    @staticmethod
    def _calculate_context_similarity(c1: StrategyContext, c2: StrategyContext) -> float:
        """Calculates normalized similarity score [0.0, 1.0] between two StrategyContexts."""
        score = 0.0
        weights = {
            "symbol": 0.20,
            "regime": 0.30,
            "trend_state": 0.20,
            "volatility_regime": 0.15,
            "session": 0.15,
        }

        if c1.symbol == c2.symbol:
            score += weights["symbol"]
        if c1.regime == c2.regime:
            score += weights["regime"]
        if c1.trend_state == c2.trend_state:
            score += weights["trend_state"]
        if c1.volatility_regime == c2.volatility_regime:
            score += weights["volatility_regime"]
        if c1.session in (c2.session, "ALL") or c2.session == "ALL":
            score += weights["session"]

        return float(round(score, 4))
