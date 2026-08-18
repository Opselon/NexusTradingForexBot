"""
Experience Intelligence Pre-Trade Decision Boundary
===================================================
Phase 08 decision gate: turns accumulated experience into a bounded,
explainable verdict on a live trade proposal.

HARD SAFETY INVARIANTS
----------------------
1. The gate may only DOWN-RANK or REJECT an existing proposal. It never
   generates a proposal, never places or modifies an order, never touches the
   MT5 adapter, and never bypasses RiskEngine or OrderManager. Rejection is
   expressed by rewriting the proposal to `ActionType.NO_TRADE`, which the
   existing pipeline already treats as "do nothing", so rejection happens
   strictly BEFORE order placement.
2. ONLY ENTRY actions are gated. Position-management actions
   (CLOSE_POSITION / PARTIAL_CLOSE / MODIFY_SL_TP / CANCEL_ORDER) pass through
   untouched - blocking an exit would be a capital-safety failure, not a
   learning improvement. (The first Phase 08 revision gated every non-NO_TRADE
   action, so a retired strategy could suppress a protective close - see
   agents/bugs.md BUG-010.)
3. Absence of evidence is never approval. With no relevant history the verdict
   is INSUFFICIENT_EVIDENCE and the proposal passes through BIT-IDENTICAL:
   no confidence boost is ever fabricated.
4. Learning failure is non-critical. Any internal exception is isolated,
   logged, and the ORIGINAL proposal is passed through unchanged with
   `action=INSUFFICIENT_EVIDENCE` and an explicit failure reason - the gate
   never invents an endorsement and never blocks the live path.
5. Hot-path discipline: score refreshes are TTL-cached and rate-limited. A
   cache hit performs zero database work. When the inline refresh budget is
   exhausted the gate degrades to INSUFFICIENT_EVIDENCE instead of blocking the
   event loop.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    INELIGIBLE_LIFECYCLES,
    ExperienceAction,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    ModelProvenance,
    OutcomeCorrelationSource,
    PreTradeExperienceDecision,
    StrategyContext,
    StrategyLifecycle,
    StrategyScore,
)
from nexus_scalp.experience.outcome_recovery import resolve_outcome_correlation
from nexus_scalp.experience.quality import OutcomeAnalyzer, compute_behavior_metrics
from nexus_scalp.experience.retriever import ExperienceRetriever
from nexus_scalp.features.regime_classifier import MarketRegimeState
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.intelligence")

#: Entry actions subject to the experience gate. Deliberately excludes every
#: position-management action so an exit can never be blocked by learning.
GATED_ENTRY_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.BUY,
        ActionType.SELL,
        ActionType.BUY_MARKET,
        ActionType.SELL_MARKET,
        ActionType.BUY_LIMIT,
        ActionType.SELL_LIMIT,
        ActionType.BUY_STOP,
        ActionType.SELL_STOP,
    }
)


@dataclass
class _CachedScore:
    """TTL-cached derived score for one strategy family."""

    score: StrategyScore
    similarity: float
    sample_count: int
    cached_at: float


class ExperienceIntelligenceEngine:
    """
    Bounded pre-trade gate plus post-trade outcome recorder.

    The engine owns no execution capability by construction: it holds a ledger,
    an evaluator, a retriever and an analyzer - no adapter, no order manager, no
    risk engine.
    """

    def __init__(
        self,
        ledger: ExperienceLedger,
        evaluator: StrategyEvaluator,
        retriever: ExperienceRetriever,
        enabled: bool = True,
        min_confidence_to_qualify: float = 0.40,
        degraded_confidence_multiplier: float = 0.70,
        high_expectancy_boost_threshold: float = 0.50,
        confidence_boost_multiplier: float = 1.10,
        score_cache_ttl_sec: float = 30.0,
        max_inline_refresh_per_sec: float = 4.0,
        retrieval_top_k: int = 100,
        analyzer: OutcomeAnalyzer | None = None,
        provenance: ModelProvenance | None = None,
    ) -> None:
        self.ledger = ledger
        self.evaluator = evaluator
        self.retriever = retriever
        self.enabled = enabled
        self.min_confidence_to_qualify = min_confidence_to_qualify
        self.degraded_confidence_multiplier = degraded_confidence_multiplier
        self.high_expectancy_boost_threshold = high_expectancy_boost_threshold
        self.confidence_boost_multiplier = confidence_boost_multiplier
        self.score_cache_ttl_sec = max(1.0, score_cache_ttl_sec)
        self.max_inline_refresh_per_sec = max(0.0, max_inline_refresh_per_sec)
        self.retrieval_top_k = max(1, retrieval_top_k)
        self.analyzer = analyzer or OutcomeAnalyzer()
        self.provenance = provenance or ModelProvenance()

        self._score_cache: dict[str, _CachedScore] = {}
        self._last_inline_refresh: float = 0.0
        #: Observability counters (also asserted by the test-suite).
        self.gate_allow_count: int = 0
        self.gate_penalize_count: int = 0
        self.gate_reject_count: int = 0
        self.gate_insufficient_count: int = 0
        self.gate_failure_count: int = 0

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def set_provenance(self, provenance: ModelProvenance) -> None:
        """
        Updates the provenance stamped onto NEW experiences after a model
        registration or hot-swap.

        Historical experiences are untouched: they keep the provenance of the
        model that actually produced them.
        """
        self.provenance = provenance
        logger.info(
            "[MODEL] SCHEMA_VERSION",
            model_id=provenance.model_id,
            model_version=provenance.model_version,
            feature_schema=provenance.feature_schema_id,
            feature_dimension=provenance.feature_dimension,
        )

    # ------------------------------------------------------------------
    # Pre-trade gate
    # ------------------------------------------------------------------

    def evaluate_proposal(
        self,
        proposal: TradeProposal,
        feature_vector: FeatureVector,
        regime_state: MarketRegimeState | None = None,
    ) -> tuple[TradeProposal, PreTradeExperienceDecision]:
        """
        Evaluates a proposal against historical experience.

        Returns `(proposal_out, decision)`. `proposal_out` is either the
        original object, a confidence-adjusted copy, or a NO_TRADE rejection.
        Never raises.
        """
        decision_id = f"exp_dec_{uuid.uuid4().hex[:12]}"
        now_utc = datetime.now(UTC)
        try:
            return self._evaluate_internal(
                proposal=proposal,
                feature_vector=feature_vector,
                regime_state=regime_state,
                decision_id=decision_id,
                now_utc=now_utc,
            )
        except Exception as e:
            # FAIL-SAFE: isolate, pass the ORIGINAL proposal through untouched,
            # and label the verdict as evidence-free rather than as an ALLOW.
            self.gate_failure_count += 1
            logger.error(
                "[PRE_TRADE] EVALUATION_FAILED (isolated, proposal passed through unchanged)",
                request_id=proposal.request_id,
                error=str(e),
                exc_info=True,
            )
            return proposal, PreTradeExperienceDecision(
                decision_id=decision_id,
                request_id=proposal.request_id,
                timestamp=now_utc,
                action=ExperienceAction.INSUFFICIENT_EVIDENCE,
                qualifies_trade=True,
                adjusted_confidence=proposal.confidence,
                strategy_id="strat_unavailable",
                strategy_lifecycle=StrategyLifecycle.DISCOVERED,
                retrieved_sample_count=0,
                similarity_score=0.0,
                evidence_quality=0.0,
                penalty_reason=f"EXPERIENCE_EVALUATION_FAILED: {e}",
                provenance=self.provenance,
            )

    def _evaluate_internal(
        self,
        proposal: TradeProposal,
        feature_vector: FeatureVector,
        regime_state: MarketRegimeState | None,
        decision_id: str,
        now_utc: datetime,
    ) -> tuple[TradeProposal, PreTradeExperienceDecision]:
        # --- 1. Scope: only entry proposals are gated -------------------
        if not self.enabled or proposal.action not in GATED_ENTRY_ACTIONS:
            reason = "GATE_DISABLED" if not self.enabled else "NON_ENTRY_ACTION_NOT_GATED"
            return proposal, self._passthrough_decision(
                proposal, decision_id, now_utc, reason=reason
            )

        # --- 2. Bounded context ----------------------------------------
        context = self.build_proposal_context(
            proposal=proposal, feature_vector=feature_vector, regime_state=regime_state
        )

        # --- 3. TTL-cached, causally valid evidence --------------------
        cached = self._get_score(context=context, decision_timestamp=proposal.generated_at)

        # --- 4. Immutable decision snapshot (always recorded) ----------
        self._record_decision_experience(
            proposal=proposal,
            context=context,
            feature_vector=feature_vector,
            decision_id=decision_id,
        )

        if cached is None:
            self.gate_insufficient_count += 1
            logger.debug(
                "[PRE_TRADE] INSUFFICIENT_EVIDENCE",
                request_id=proposal.request_id,
                strategy_id=context.strategy_id,
            )
            return proposal, PreTradeExperienceDecision(
                decision_id=decision_id,
                request_id=proposal.request_id,
                timestamp=now_utc,
                action=ExperienceAction.INSUFFICIENT_EVIDENCE,
                qualifies_trade=True,
                adjusted_confidence=proposal.confidence,
                strategy_id=context.strategy_id,
                strategy_lifecycle=StrategyLifecycle.DISCOVERED,
                retrieved_sample_count=0,
                similarity_score=0.0,
                evidence_quality=0.0,
                penalty_reason="NO_RELEVANT_EXPERIENCE",
                provenance=self.provenance,
            )

        score = cached.score
        lifecycle = score.lifecycle_state

        # --- 5. Verdict ------------------------------------------------
        action = ExperienceAction.ALLOW
        qualifies = True
        adjusted_confidence = proposal.confidence
        reason = ""

        if lifecycle in INELIGIBLE_LIFECYCLES:
            # HARD SAFETY: statistically proven harmful family.
            action = ExperienceAction.REJECT
            qualifies = False
            adjusted_confidence = 0.0
            reason = (
                "PERSISTENT_NEGATIVE_EXPECTANCY"
                if lifecycle == StrategyLifecycle.RETIRED
                else "QUARANTINED_ANOMALOUS_EVIDENCE"
            )
        elif lifecycle == StrategyLifecycle.DEGRADED:
            action = ExperienceAction.PENALIZE
            adjusted_confidence = round(
                proposal.confidence * self.degraded_confidence_multiplier, 4
            )
            reason = "DEGRADED_STRATEGY_CONFIDENCE_PENALTY"
            if adjusted_confidence < self.min_confidence_to_qualify:
                action = ExperienceAction.REJECT
                qualifies = False
                reason = f"DEGRADED_CONFIDENCE_BELOW_THRESHOLD ({adjusted_confidence:.2f})"
        elif lifecycle in (StrategyLifecycle.ACTIVE, StrategyLifecycle.VALIDATED):
            # A boost requires BOTH a strong recency-weighted edge AND
            # out-of-sample confirmation - never sample count alone.
            if (
                score.recency_weighted_expectancy_r > self.high_expectancy_boost_threshold
                and score.replay_validated
            ):
                adjusted_confidence = min(
                    1.0, round(proposal.confidence * self.confidence_boost_multiplier, 4)
                )
                action = ExperienceAction.ALLOW_WITH_CONTEXT
                reason = "VALIDATED_HIGH_EXPECTANCY_BOOST"
        else:
            # DISCOVERED / EVALUATING: evidence exists but is not conclusive.
            action = ExperienceAction.ALLOW
            reason = "EVIDENCE_ACCUMULATING"

        decision = PreTradeExperienceDecision(
            decision_id=decision_id,
            request_id=proposal.request_id,
            timestamp=now_utc,
            action=action,
            qualifies_trade=qualifies,
            adjusted_confidence=adjusted_confidence,
            strategy_id=context.strategy_id,
            strategy_lifecycle=lifecycle,
            strategy_score=score,
            retrieved_sample_count=cached.sample_count,
            similarity_score=cached.similarity,
            evidence_quality=score.evidence_quality,
            expectancy_r=score.expectancy_r,
            recent_expectancy_r=score.recent_window_expectancy_r,
            drawdown_r=score.normalized_drawdown_r,
            penalty_reason=reason,
            provenance=self.provenance,
        )

        # --- 6. Apply verdict to the proposal --------------------------
        if not qualifies:
            self.gate_reject_count += 1
            logger.info(
                "[PRE_TRADE] REJECT",
                request_id=proposal.request_id,
                strategy_id=context.strategy_id,
                context=f"{context.regime}/{context.volatility_regime}/{context.trend_state}",
                samples=score.sample_count,
                expectancy_r=score.expectancy_r,
                recent_expectancy_r=score.recent_window_expectancy_r,
                drawdown_r=score.normalized_drawdown_r,
                confidence=score.confidence_score,
                lifecycle=lifecycle.value,
                reason=reason,
            )
            rejected = proposal.model_copy(
                update={
                    "action": ActionType.NO_TRADE,
                    "confidence": 0.0,
                    "rejection_reason": reason,
                    "final_action": "NO_TRADE",
                    "decision_stage": "EXPERIENCE_INTELLIGENCE_GATE",
                    "blocked_by": f"EXPERIENCE_{lifecycle.value}",
                }
            )
            return rejected, decision

        if adjusted_confidence != proposal.confidence:
            self.gate_penalize_count += 1
            logger.info(
                "[PRE_TRADE] PENALIZE"
                if action == ExperienceAction.PENALIZE
                else "[PRE_TRADE] ALLOW",
                request_id=proposal.request_id,
                strategy_id=context.strategy_id,
                lifecycle=lifecycle.value,
                confidence_before=proposal.confidence,
                confidence_after=adjusted_confidence,
                reason=reason,
            )
            adjusted = proposal.model_copy(
                update={
                    "confidence": adjusted_confidence,
                    "confidence_before_filters": proposal.confidence,
                    "confidence_after_filters": adjusted_confidence,
                    "override_reason": reason,
                    "decision_stage": "EXPERIENCE_INTELLIGENCE_GATE",
                }
            )
            return adjusted, decision

        self.gate_allow_count += 1
        return proposal, decision

    def build_proposal_context(
        self,
        proposal: TradeProposal,
        feature_vector: FeatureVector,
        regime_state: MarketRegimeState | None = None,
    ) -> StrategyContext:
        """
        Builds the exact `StrategyContext` the gate will use for a proposal.

        Exposed publicly so callers and tests can resolve the strategy family a
        proposal maps to WITHOUT duplicating the setup/session/confluence
        derivation (which would silently drift from the gate).
        """
        return self.retriever.build_context(
            symbol=proposal.symbol,
            timeframe="M1",
            feature_vector=feature_vector,
            regime_state=regime_state,
            entry_reason=proposal.reason_code,
            execution_mode=str(proposal.execution_mode or ""),
        )

    def _passthrough_decision(
        self,
        proposal: TradeProposal,
        decision_id: str,
        now_utc: datetime,
        reason: str,
    ) -> PreTradeExperienceDecision:
        """Neutral verdict for out-of-scope proposals. No confidence change."""
        return PreTradeExperienceDecision(
            decision_id=decision_id,
            request_id=proposal.request_id,
            timestamp=now_utc,
            action=ExperienceAction.INSUFFICIENT_EVIDENCE,
            qualifies_trade=True,
            adjusted_confidence=proposal.confidence,
            strategy_id="strat_not_evaluated",
            strategy_lifecycle=StrategyLifecycle.DISCOVERED,
            retrieved_sample_count=0,
            similarity_score=0.0,
            evidence_quality=0.0,
            penalty_reason=reason,
            provenance=self.provenance,
        )

    # ------------------------------------------------------------------
    # Score caching (hot-path discipline)
    # ------------------------------------------------------------------

    def _get_score(
        self, context: StrategyContext, decision_timestamp: datetime
    ) -> _CachedScore | None:
        """
        Returns bounded evidence for a strategy family.

        Three tiers, cheapest first:
          1. TTL cache hit  -> zero database work.
          2. Inline refresh -> bounded causal retrieval + evaluation, allowed
             only while the per-second refresh budget permits it.
          3. Registry read  -> a single indexed primary-key SELECT of the
             already-derived score.

        Tier 3 exists so that exhausting the refresh budget can NEVER let a
        RETIRED strategy slip through the gate: the lifecycle verdict stays
        available even when the expensive path is skipped.
        """
        now_mono = time.monotonic()
        cached = self._score_cache.get(context.strategy_id)
        if cached is not None and (now_mono - cached.cached_at) < self.score_cache_ttl_sec:
            return cached

        budget_ok = True
        if self.max_inline_refresh_per_sec > 0.0:
            min_interval = 1.0 / self.max_inline_refresh_per_sec
            budget_ok = (now_mono - self._last_inline_refresh) >= min_interval

        if budget_ok:
            self._last_inline_refresh = now_mono
            return self.refresh_strategy_score(
                context=context, decision_timestamp=decision_timestamp
            )

        # Budget exhausted: fall back to the cheap derived-registry lookup so the
        # lifecycle gate still applies, then to stale cache, then to no evidence.
        registry_score = self.evaluator.get_registered_strategy_score(context.strategy_id)
        if registry_score is not None and registry_score.sample_count > 0:
            entry = _CachedScore(
                score=registry_score,
                similarity=1.0,
                sample_count=registry_score.sample_count,
                cached_at=now_mono,
            )
            self._score_cache[context.strategy_id] = entry
            return entry
        return cached

    def refresh_strategy_score(
        self, context: StrategyContext, decision_timestamp: datetime
    ) -> _CachedScore | None:
        """
        Performs a bounded causal retrieval + evaluation and refreshes the cache.

        Safe to call from a background task; the pre-trade path then only ever
        reads the cache.
        """
        experiences, similarity = self.retriever.retrieve_relevant_experiences(
            context=context,
            decision_timestamp=decision_timestamp,
            top_k=self.retrieval_top_k,
        )
        closed = [e for e in experiences if e.is_executed and e.is_closed]
        if not closed:
            return None

        score = self.evaluator.evaluate_strategy(
            strategy_id=context.strategy_id, experiences=experiences
        )
        entry = _CachedScore(
            score=score,
            similarity=similarity,
            sample_count=len(closed),
            cached_at=time.monotonic(),
        )
        self._score_cache[context.strategy_id] = entry
        return entry

    def invalidate_score_cache(self, strategy_id: str | None = None) -> None:
        """Drops cached evidence so the next evaluation re-reads the ledger."""
        if strategy_id is None:
            self._score_cache.clear()
        else:
            self._score_cache.pop(strategy_id, None)

    # ------------------------------------------------------------------
    # Experience recording
    # ------------------------------------------------------------------

    def _record_decision_experience(
        self,
        proposal: TradeProposal,
        context: StrategyContext,
        feature_vector: FeatureVector,
        decision_id: str,
    ) -> None:
        """
        Writes the immutable decision snapshot.

        The feature snapshot is stored under the ACTIVE schema identity and the
        current model provenance so the row stays interpretable after the model
        or the feature contract changes.
        """
        values = self._extract_feature_values(feature_vector)
        snapshot = FeatureSnapshot(
            feature_schema_id=self.provenance.feature_schema_id,
            feature_dimension=len(values) or self.provenance.feature_dimension,
            values=values,
            feature_hash=self.ledger.compute_feature_hash(
                values, self.provenance.feature_schema_id
            ),
        )
        record = ExperienceRecord(
            experience_id=f"exp_{proposal.request_id[:12]}",
            request_id=proposal.request_id,
            decision_id=decision_id,
            idempotency_key=self.build_idempotency_key(proposal.request_id),
            symbol=proposal.symbol,
            timeframe=context.timeframe,
            decision_timestamp=proposal.generated_at,
            strategy_id=context.strategy_id,
            strategy_version=context.strategy_version,
            context=context,
            feature_snapshot=snapshot,
            provenance=self.provenance,
            action=proposal.action.value,
            entry_reason=proposal.reason_code,
            model_probability=proposal.confidence,
            signal_confidence=proposal.confidence,
            proposed_entry=proposal.proposed_entry,
            stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit,
            risk_reward_ratio=proposal.risk_reward_ratio,
        )
        self.ledger.record_experience(record)

    @staticmethod
    def _extract_feature_values(feature_vector: FeatureVector) -> list[float]:
        """
        Extracts the canonical feature tensor as plain floats.

        Deliberately dimension-agnostic: whatever the live contract produces is
        stored with its declared dimension, so a future 60D/350D schema needs no
        change here.
        """
        tensor = feature_vector.to_tensor_input()
        if isinstance(tensor, list):
            return [float(v) for v in tensor]
        squeezed = tensor.squeeze()
        listed = squeezed.tolist()
        if isinstance(listed, float):
            return [float(listed)]
        return [float(v) for v in listed]

    @staticmethod
    def build_idempotency_key(request_id: str) -> str:
        """
        Deterministic dedup key for an experience.

        Derived solely from the proposal request id so the pre-trade write and
        the post-trade outcome always agree, and so replayed broker callbacks
        collapse onto the same row.
        """
        return f"exp_{request_id}"

    # ------------------------------------------------------------------
    # Post-trade outcome
    # ------------------------------------------------------------------

    def record_trade_outcome(
        self,
        request_id: str,
        execution_id: str,
        outcome_timestamp: datetime,
        is_executed: bool,
        is_closed: bool,
        exit_reason: str,
        realized_pnl_usd: float,
        realized_r_multiple: float,
        mae_points: float = 0.0,
        mfe_points: float = 0.0,
        mae_usd: float = 0.0,
        mfe_usd: float = 0.0,
        holding_duration_seconds: float = 0.0,
        approved_volume: float = 0.0,
        actual_entry: float = 0.0,
        slippage_points: float = 0.0,
        execution_latency_ms: float = 0.0,
        spread_at_execution: float = 0.0,
        initial_sl_distance: float = 0.0,
        sl_moved: bool = False,
        tp_moved: bool = False,
        partial_closed: bool = False,
        atr_at_entry: float = 0.0,
        expected_duration_sec: float = 0.0,
        time_to_mae_sec: float = 0.0,
        time_to_mfe_sec: float = 0.0,
        broker_retcode: int = 0,
        rejection_reason: str = "",
        correlation_source: str = "",
        correlation_detail: str = "",
        broker_outcome: dict[str, object] | None = None,
    ) -> bool:
        """
        Records the append-only outcome for a decision, including the full
        quality decomposition and behavioral flags.

        Idempotent: a duplicate close callback is discarded by the outcome
        table's UNIQUE constraint. Fully exception-isolated so OrderManager can
        never be disrupted by learning.

        Phase 14: BREAK_EVEN is a first-class outcome. The outcome is recorded
        for WIN / LOSS / BREAK_EVEN whenever a correlatable decision exists;
        a zero or near-zero net PnL is NEVER a reason to skip learning.
        Correlation provenance (correlation_source / correlation_detail)
        survives into the persisted outcome payload.
        """
        try:
            key = self.build_idempotency_key(request_id)
            if not request_id:
                # Phase 14: attempt deterministic correlation recovery instead
                # of silently discarding the outcome (BUG-045).
                recovered = resolve_outcome_correlation(
                    request_id=request_id,
                    ticket=str(execution_id),
                    ledger=self.ledger,
                    build_idempotency_key_fn=self.build_idempotency_key,
                )
                if recovered is None:
                    logger.warning(
                        "[EXPERIENCE_OUTCOME] event=CORRELATION_FAILED",
                        ticket=execution_id,
                        reason="NO_CORRELATABLE_EXPERIENCE",
                        identifiers_available={
                            "ticket": execution_id,
                            "correlation_source": correlation_source or "",
                        },
                    )
                    return False
                key, recovered_source, recovered_detail = recovered
                correlation_source = correlation_source or recovered_source
                correlation_detail = correlation_detail or recovered_detail
                logger.info(
                    "[EXPERIENCE_OUTCOME] event=CORRELATION_RECOVERY",
                    ticket=execution_id,
                    fallback=recovered_source,
                    idempotency_key=key,
                    status="RECOVERED",
                )
            else:
                # Normal path: the originating request_id IS the correlation
                # identity. Record explicit ORIGINAL_REQUEST provenance.
                correlation_source = (
                    correlation_source or OutcomeCorrelationSource.ORIGINAL_REQUEST.value
                )
                if not correlation_detail:
                    correlation_detail = f"request_id={request_id}"

            record = self.ledger.get_experience_by_key(key)
            if record is None:
                # No decision snapshot: recording an orphan outcome would
                # fabricate evidence with no context, so it is refused - but
                # with full diagnostics.
                logger.warning(
                    "[EXPERIENCE_OUTCOME] event=RECORD_FAILED",
                    ticket=execution_id,
                    reason="NO_DECISION_SNAPSHOT",
                    idempotency_key=key,
                    correlation_source=correlation_source or "",
                )
                return False

            if outcome_timestamp < record.decision_timestamp:
                logger.error(
                    "[EXPERIENCE] CAUSALITY_REJECTED outcome precedes decision",
                    idempotency_key=key,
                )
                return False

            if self.ledger.has_outcome(key):
                self.ledger.duplicate_count += 1
                logger.info("[EXPERIENCE] DUPLICATE outcome ignored", idempotency_key=key)
                return False

            # ANOMALY-VERIFY-01: one broker ticket == one ECONOMIC trade.
            # A second closed outcome carrying the SAME execution_id (broker
            # ticket) but a DIFFERENT idempotency_key is a duplicate of the
            # same economic position (split-fill / sibling-ticket context
            # leak, BUG-081 pattern) — it must NOT create a second outcome.
            # The broker ticket is the economic identity; the first outcome
            # row for it is the canonical one.
            if execution_id and str(execution_id).strip():
                existing_ticket_owner = self.ledger.owner_of_execution(
                    str(execution_id), exclude_key=key
                )
                if existing_ticket_owner:
                    self.ledger.duplicate_count += 1
                    logger.warning(
                        "[EXPERIENCE_OUTCOME] event=ECONOMIC_DUPLICATE_REJECTED",
                        ticket=execution_id,
                        idempotency_key=key,
                        existing_key=existing_ticket_owner,
                        reason="one economic trade must have exactly one outcome",
                    )
                    return False

            planned_risk = record.planned_risk_distance
            behavior = compute_behavior_metrics(
                mae_points=mae_points,
                mfe_points=mfe_points,
                mae_usd=mae_usd,
                mfe_usd=mfe_usd,
                planned_risk_distance=planned_risk,
                duration_sec=holding_duration_seconds,
                time_to_mae_sec=time_to_mae_sec,
                time_to_mfe_sec=time_to_mfe_sec,
                expected_duration_sec=expected_duration_sec,
                initial_sl_distance=initial_sl_distance or planned_risk,
                sl_moved=sl_moved,
                tp_moved=tp_moved,
                partial_closed=partial_closed,
                atr_at_entry=atr_at_entry,
            )
            execution = self._build_execution_context(
                record=record,
                actual_entry=actual_entry,
                slippage_points=slippage_points,
                latency_ms=execution_latency_ms,
                spread_at_execution=spread_at_execution,
                approved_volume=approved_volume,
                broker_retcode=broker_retcode,
                rejection_reason=rejection_reason,
            )

            recent_entries = self.ledger.count_recent_entries_for_strategy(
                strategy_id=record.strategy_id,
                before_timestamp=record.decision_timestamp,
                window_seconds=self.analyzer.t.reentry_window_sec,
            )
            decomposition, flags = self.analyzer.analyze(
                record=record,
                behavior=behavior,
                execution=execution,
                realized_r=realized_r_multiple,
                exit_reason=exit_reason,
                recent_context_entries=recent_entries,
            )

            broker = None
            if broker_outcome is not None:
                try:
                    from nexus_scalp.experience.models import BrokerOutcome

                    broker = BrokerOutcome.model_validate(broker_outcome)
                except Exception:
                    broker = None

            outcome = ExperienceOutcome(
                idempotency_key=key,
                execution_id=execution_id,
                outcome_timestamp=outcome_timestamp,
                is_executed=is_executed,
                is_closed=is_closed,
                exit_reason=exit_reason,
                realized_pnl_usd=realized_pnl_usd,
                realized_r_multiple=realized_r_multiple,
                approved_volume=approved_volume,
                behavior=behavior,
                execution=execution,
                decomposition=decomposition,
                behavioral_flags=flags,
                correlation_source=correlation_source or "",
                correlation_detail=correlation_detail or "",
                broker_outcome=broker,
            )
            ok = self.ledger.record_outcome(outcome)

            logger.info(
                "[POSITION] STRATEGY_ATTRIBUTION",
                strategy_id=record.strategy_id,
                execution_id=execution_id,
                realized_r=round(realized_r_multiple, 3),
                entry_quality=round(decomposition.entry_quality, 3),
                hold_quality=round(decomposition.position_management_quality, 3),
                exit_quality=round(decomposition.exit_quality, 3),
                execution_quality=round(decomposition.execution_quality, 3),
                strategy_quality=round(decomposition.strategy_quality, 3),
                strategy_verdict=decomposition.strategy_verdict.value,
                profitable_for_wrong_reason=decomposition.profitable_for_wrong_reason,
                acceptable_loss=decomposition.acceptable_loss,
                flags=[f.value for f in flags],
            )

            # Fresh evidence: force the next gate evaluation to re-read history.
            self.invalidate_score_cache(record.strategy_id)
            return ok

        except Exception as e:
            logger.error(
                "[EXPERIENCE] outcome recording failed (isolated)",
                request_id=request_id,
                error=str(e),
                exc_info=True,
            )
            return False

    @staticmethod
    def _build_execution_context(
        record: ExperienceRecord,
        actual_entry: float,
        slippage_points: float,
        latency_ms: float,
        spread_at_execution: float,
        approved_volume: float,
        broker_retcode: int,
        rejection_reason: str,
    ):
        """
        Builds the execution context, deriving DIRECTIONAL slippage when the
        caller only supplies the actual fill.

        Slippage is signed against the trade direction so "adverse" always means
        the same thing for BUY and SELL.
        """
        from nexus_scalp.experience.models import ExecutionContext

        expected = record.proposed_entry
        derived_slippage = slippage_points
        if derived_slippage == 0.0 and actual_entry > 0.0 and expected > 0.0:
            raw = actual_entry - expected
            is_buy = "BUY" in record.action.upper()
            derived_slippage = raw if is_buy else -raw

        return ExecutionContext(
            expected_entry=max(0.0, expected),
            actual_entry=max(0.0, actual_entry),
            slippage_points=float(derived_slippage),
            latency_ms=max(0.0, latency_ms),
            spread_at_execution=max(0.0, spread_at_execution),
            broker_retcode=int(broker_retcode),
            rejection_reason=rejection_reason,
            executed_volume=max(0.0, approved_volume),
        )

    # ------------------------------------------------------------------
    # Self-healing / startup
    # ------------------------------------------------------------------

    def self_heal(self) -> dict[str, StrategyScore]:
        """
        Rebuilds derived intelligence from the immutable ledger and clears the
        in-memory cache.

        Never mutates historical outcomes; safe after model rebuild, registry
        corruption or an interrupted derived calculation.
        """
        rebuilt = self.evaluator.rebuild_derived_intelligence(self.ledger)
        self.invalidate_score_cache()
        return rebuilt

    def summary(self) -> dict[str, object]:
        """Observability snapshot for the REST API and diagnostics."""
        return {
            "enabled": self.enabled,
            "recorded_experiences": self.ledger.count_experiences(),
            "queued_records": self.ledger.recorded_count,
            "queued_outcomes": self.ledger.outcome_count,
            "duplicate_outcomes": self.ledger.duplicate_count,
            "cached_strategies": len(self._score_cache),
            "gate_allow": self.gate_allow_count,
            "gate_penalize": self.gate_penalize_count,
            "gate_reject": self.gate_reject_count,
            "gate_insufficient_evidence": self.gate_insufficient_count,
            "gate_failures": self.gate_failure_count,
            "feature_schema_id": self.provenance.feature_schema_id,
            "feature_dimension": self.provenance.feature_dimension,
            "model_id": self.provenance.model_id,
            "model_version": self.provenance.model_version,
            "schema_distribution": self.ledger.get_schema_distribution(),
        }
