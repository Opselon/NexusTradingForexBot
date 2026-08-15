"""
Experience Intelligence Decision Boundary Engine
=================================================
Phase 08 Experience Intelligence core pre-trade decision boundary.

Evaluates trade proposals against historical strategy experience, scoring, and
data-driven lifecycles. Can REJECT, PENALIZE, down-rank, or qualify proposals
before passing them to RiskEngine.

HARD INVARIANT:
- Experience Intelligence may REJECT or qualify trade proposals.
- It MUST NOT bypass RiskEngine, OrderManager, or MT5 safety controls.
- It MUST NOT generate parallel trade signals.
- Exception Safety: Failures inside experience evaluation MUST NEVER block live execution.
"""

import uuid
from datetime import UTC, datetime

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExperienceAction,
    ExperienceRecord,
    PreTradeExperienceDecision,
    StrategyLifecycle,
)
from nexus_scalp.experience.retriever import ExperienceRetriever
from nexus_scalp.features.regime_classifier import MarketRegimeState
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.intelligence")


class ExperienceIntelligenceEngine:
    """
    Pre-trade decision boundary integrating historical strategy intelligence into the decision pipeline.
    Includes exception isolation safeguards ensuring learning failures never disrupt execution.
    """

    def __init__(
        self,
        ledger: ExperienceLedger,
        evaluator: StrategyEvaluator,
        retriever: ExperienceRetriever,
        enabled: bool = True,
        min_confidence_to_qualify: float = 0.40,
    ) -> None:
        self.ledger = ledger
        self.evaluator = evaluator
        self.retriever = retriever
        self.enabled = enabled
        self.min_confidence_to_qualify = min_confidence_to_qualify

    def evaluate_proposal(
        self,
        proposal: TradeProposal,
        feature_vector: FeatureVector,
        regime_state: MarketRegimeState | None = None,
    ) -> tuple[TradeProposal, PreTradeExperienceDecision]:
        """
        Evaluates a live trade proposal against historical strategy intelligence.

        Returns a tuple of (evaluated_proposal, pre_trade_decision).
        Guarantees complete exception safety: any exception is isolated and proposal is passed safely.
        """
        decision_id = f"exp_dec_{uuid.uuid4().hex[:12]}"
        now_utc = datetime.now(UTC)

        try:
            return self._evaluate_proposal_internal(
                proposal=proposal,
                feature_vector=feature_vector,
                regime_state=regime_state,
                decision_id=decision_id,
                now_utc=now_utc,
            )
        except Exception as e:
            logger.error(
                "EXCEPTION ISOLATED IN EXPERIENCE INTELLIGENCE: Passing trade proposal safely to RiskEngine",
                request_id=proposal.request_id,
                error=str(e),
                exc_info=True,
            )
            fallback_decision = PreTradeExperienceDecision(
                decision_id=decision_id,
                request_id=proposal.request_id,
                timestamp=now_utc,
                action=ExperienceAction.ALLOW,
                qualifies_trade=True,
                adjusted_confidence=proposal.confidence,
                strategy_id="strat_fallback_isolated",
                strategy_lifecycle=StrategyLifecycle.DISCOVERED,
                retrieved_sample_count=0,
                similarity_score=1.0,
                penalty_reason=f"EXCEPTION_ISOLATED: {e}",
            )
            return proposal, fallback_decision

    def _evaluate_proposal_internal(
        self,
        proposal: TradeProposal,
        feature_vector: FeatureVector,
        regime_state: MarketRegimeState | None,
        decision_id: str,
        now_utc: datetime,
    ) -> tuple[TradeProposal, PreTradeExperienceDecision]:
        # 1. Bypassed if proposal is NO_TRADE or engine disabled
        if not self.enabled or proposal.action in (ActionType.NO_TRADE, ActionType.WAIT):
            default_ctx = self.retriever.build_context(
                symbol=proposal.symbol,
                timeframe="M1",
                feature_vector=feature_vector,
                regime_state=regime_state,
            )
            decision = PreTradeExperienceDecision(
                decision_id=decision_id,
                request_id=proposal.request_id,
                timestamp=now_utc,
                action=ExperienceAction.ALLOW,
                qualifies_trade=True,
                adjusted_confidence=proposal.confidence,
                strategy_id=default_ctx.strategy_id,
                strategy_lifecycle=StrategyLifecycle.DISCOVERED,
                retrieved_sample_count=0,
                similarity_score=1.0,
                penalty_reason="NO_TRADE_OR_DISABLED",
            )
            return proposal, decision

        # 2. Build StrategyContext from live feature vector and regime state
        context = self.retriever.build_context(
            symbol=proposal.symbol,
            timeframe="M1",
            feature_vector=feature_vector,
            regime_state=regime_state,
        )

        # 3. Retrieve causally valid historical experiences before proposal.generated_at
        retrieved_exps, sim_score = self.retriever.retrieve_relevant_experiences(
            context=context,
            decision_timestamp=proposal.generated_at,
            top_k=50,
        )

        # 4. Evaluate strategy score and data-driven lifecycle state
        strategy_score = self.evaluator.evaluate_strategy(
            strategy_id=context.strategy_id,
            experiences=retrieved_exps,
        )
        lifecycle = strategy_score.lifecycle_state

        # 5. Pre-Trade Decision Boundary Rules
        decision_action = ExperienceAction.ALLOW
        qualifies_trade = True
        adjusted_confidence = proposal.confidence
        penalty_reason = ""

        # RULE A: RETIRED or QUARANTINED strategies MUST BE REJECTED
        if lifecycle in (StrategyLifecycle.RETIRED, StrategyLifecycle.QUARANTINED):
            decision_action = ExperienceAction.REJECT
            qualifies_trade = False
            adjusted_confidence = 0.0
            penalty_reason = f"STRATEGY_LIFECYCLE_{lifecycle.value}_REJECTED"

        # RULE B: DEGRADED strategies apply a 30% confidence penalty
        elif lifecycle == StrategyLifecycle.DEGRADED:
            decision_action = ExperienceAction.PENALIZE
            adjusted_confidence = round(proposal.confidence * 0.70, 4)
            penalty_reason = "DEGRADED_STRATEGY_CONFIDENCE_PENALTY_30PCT"
            if adjusted_confidence < self.min_confidence_to_qualify:
                decision_action = ExperienceAction.REJECT
                qualifies_trade = False
                penalty_reason = f"DEGRADED_CONFIDENCE_BELOW_THRESHOLD ({adjusted_confidence:.2f})"

        # RULE C: VALIDATED / ACTIVE strategies boost or maintain confidence
        elif lifecycle in (StrategyLifecycle.ACTIVE, StrategyLifecycle.VALIDATED):
            if strategy_score.recency_weighted_expectancy_r > 0.50:
                adjusted_confidence = min(1.0, round(proposal.confidence * 1.10, 4))
                decision_action = ExperienceAction.ALLOW_WITH_CONTEXT
                penalty_reason = "HIGH_EXPECTANCY_CONFIDENCE_BOOST_10PCT"
            else:
                decision_action = ExperienceAction.ALLOW

        # RULE D: DISCOVERED / EVALUATING strategies maintain baseline
        else:
            decision_action = ExperienceAction.ALLOW

        # 6. Construct PreTradeExperienceDecision object
        decision = PreTradeExperienceDecision(
            decision_id=decision_id,
            request_id=proposal.request_id,
            timestamp=now_utc,
            action=decision_action,
            qualifies_trade=qualifies_trade,
            adjusted_confidence=adjusted_confidence,
            strategy_id=context.strategy_id,
            strategy_lifecycle=lifecycle,
            strategy_score=strategy_score,
            retrieved_sample_count=len(retrieved_exps),
            similarity_score=sim_score,
            penalty_reason=penalty_reason,
        )

        # 7. Record Immutable Pre-Trade Experience Snapshot into Ledger
        tensor_50d = feature_vector.to_tensor_input()
        if isinstance(tensor_50d, list):
            feature_50d_list = [float(v) for v in tensor_50d]
        else:
            feature_50d_list = [float(v) for v in tensor_50d.squeeze().tolist()]
        feature_hash = self.ledger.compute_feature_hash(feature_50d_list)
        idempotency_key = f"exp_{proposal.request_id}"

        exp_record = ExperienceRecord(
            experience_id=f"exp_{proposal.request_id[:8]}",
            request_id=proposal.request_id,
            decision_id=decision_id,
            idempotency_key=idempotency_key,
            symbol=proposal.symbol,
            timeframe="M1",
            decision_timestamp=proposal.generated_at,
            strategy_id=context.strategy_id,
            context=context,
            feature_vector_50d=feature_50d_list,
            feature_hash=feature_hash,
            action=proposal.action.value,
            entry_reason=proposal.reason_code,
            model_probability=proposal.confidence,
            signal_confidence=adjusted_confidence,
            proposed_entry=proposal.proposed_entry,
            stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit,
            risk_reward_ratio=proposal.risk_reward_ratio,
        )
        self.ledger.record_experience(exp_record)

        # 8. Modify Proposal if Rejected or Penalized
        if not qualifies_trade:
            logger.info(
                "Experience Intelligence REJECTED trade proposal",
                request_id=proposal.request_id,
                strategy_id=context.strategy_id,
                lifecycle=lifecycle.value,
                reason=penalty_reason,
            )
            rejected_proposal = proposal.model_copy(
                update={
                    "action": ActionType.NO_TRADE,
                    "confidence": 0.0,
                    "rejection_reason": penalty_reason,
                    "final_action": "NO_TRADE",
                    "decision_stage": "EXPERIENCE_INTELLIGENCE_GATE",
                    "blocked_by": f"EXPERIENCE_{lifecycle.value}",
                }
            )
            return rejected_proposal, decision

        elif adjusted_confidence != proposal.confidence:
            updated_proposal = proposal.model_copy(
                update={
                    "confidence": adjusted_confidence,
                    "confidence_after_filters": adjusted_confidence,
                    "override_reason": penalty_reason,
                }
            )
            return updated_proposal, decision

        return proposal, decision

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
    ) -> bool:
        """
        Updates trade outcome in experience ledger when trade is closed/rejected.
        Includes error isolation so failures never interrupt order manager.
        """
        try:
            idempotency_key = f"exp_{request_id}"
            return self.ledger.update_experience_outcome(
                idempotency_key=idempotency_key,
                outcome_timestamp=outcome_timestamp,
                is_executed=is_executed,
                is_closed=is_closed,
                exit_reason=exit_reason,
                realized_pnl_usd=realized_pnl_usd,
                realized_r_multiple=realized_r_multiple,
                mae_points=mae_points,
                mfe_points=mfe_points,
                mae_usd=mae_usd,
                mfe_usd=mfe_usd,
                holding_duration_seconds=holding_duration_seconds,
            )
        except Exception as e:
            logger.error("Failed to record experience trade outcome (isolated)", error=str(e))
            return False
