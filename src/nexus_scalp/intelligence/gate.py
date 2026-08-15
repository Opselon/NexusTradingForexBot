"""
Pre-Trade Intelligence Gate
===========================
PHASE 09 suitability / quality decision layered on top of the Phase 08 gate.

Phase 08 already provides `ExperienceIntelligenceEngine.evaluate_proposal`
(ALLOW / PENALIZE / REJECT / INSUFFICIENT_EVIDENCE). This module adds the
explicit WARN tier and a bounded "suitability" score that combines historical
expectancy with context evidence, producing a richer decision explanation for
live explainability.

SAFETY (non-negotiable):
  * The gate can only down-rank the confidence of, WARN on, PENALIZE or REJECT
    an EXISTING proposal. It never creates a proposal, never places/modifies a
    position or SL/TP, never bypasses RiskEngine or OrderManager.
  * Rejection is expressed as ActionType.NO_TRADE, which the existing pipeline
    already treats as "do nothing" BEFORE order placement.
  * Absence of evidence is never approval: INSUFFICIENT_EVIDENCE passes the
    proposal through bit-identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.models import (
    ExperienceAction,
    PreTradeExperienceDecision,
    StrategyLifecycle,
)
from nexus_scalp.features.regime_classifier import MarketRegimeState
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.intelligence.gate")


class SuitabilityTier(StrEnum):
    """
    Bounded pre-trade decision tiers emitted by the Phase 09 gate.

    Deliberately a standalone enum: `ExperienceAction` (Phase 08) cannot be
    extended at runtime. WARN is the new Phase 09 tier layered on top.
    """

    ALLOW = "ALLOW"
    WARN = "WARN"
    PENALIZE = "PENALIZE"
    REJECT = "REJECT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass
class SuitabilityVerdict:
    """A bounded suitability decision over one live proposal."""

    decision: SuitabilityTier
    suitability_score: float
    qualifies: bool
    adjusted_confidence: float
    reason: str
    evidence: dict[str, Any]
    strategy_id: str
    strategy_lifecycle: StrategyLifecycle
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "suitability_score": round(self.suitability_score, 4),
            "qualifies": self.qualifies,
            "adjusted_confidence": round(self.adjusted_confidence, 4),
            "reason": self.reason,
            "evidence": self.evidence,
            "strategy_id": self.strategy_id,
            "strategy_lifecycle": self.strategy_lifecycle.value,
            "timestamp": self.timestamp.isoformat(),
        }


class PreTradeIntelligenceGate:
    """
    Phase 09 gate enhancing the Phase 08 gate with a WARN tier and a bounded
    suitability score. Owns no execution capability by construction.
    """

    def __init__(
        self,
        experience_engine: ExperienceIntelligenceEngine,
        warn_expectancy_threshold_r: float = 0.0,
        allow_expectancy_threshold_r: float = 0.05,
        severe_drawdown_r: float = 2.5,
        min_suitability_to_qualify: float = 0.40,
        warn_suitability_floor: float = 0.25,
    ) -> None:
        self.experience_engine = experience_engine
        self.warn_expectancy_threshold_r = warn_expectancy_threshold_r
        self.allow_expectancy_threshold_r = allow_expectancy_threshold_r
        self.severe_drawdown_r = severe_drawdown_r
        self.min_suitability_to_qualify = min_suitability_to_qualify
        self.warn_suitability_floor = warn_suitability_floor

        #: Observability counters
        self.gate_allow = 0
        self.gate_warn = 0
        self.gate_penalize = 0
        self.gate_reject = 0

    def evaluate(
        self, proposal: TradeProposal, fv: FeatureVector, regime: MarketRegimeState | None
    ):
        """
        Evaluates one proposal and returns (proposal, phase08_decision, suitability).

        The Phase 09 gate runs AFTER the Phase 08 gate in the live pipeline, so
        a rejection here is strictly before risk sizing / dispatch. It can only
        downgrade; it never upgrades a proposal.
        """
        proposal_out, exp_decision = self.experience_engine.evaluate_proposal(
            proposal=proposal, feature_vector=fv, regime_state=regime
        )
        base_action = exp_decision.action

        # Phase 08 already rejected hard (RETIRED / QUARANTINED / degraded-below-threshold).
        if base_action == ExperienceAction.REJECT:
            self.gate_reject += 1
            return (
                proposal_out,
                exp_decision,
                self._verdict_from_phase08(SuitabilityTier.REJECT, exp_decision),
            )

        # Phase 08 already penalized (degraded strategy).
        if base_action == ExperienceAction.PENALIZE:
            self.gate_penalize += 1
            return (
                proposal_out,
                exp_decision,
                self._verdict_from_phase08(SuitabilityTier.PENALIZE, exp_decision),
            )

        # No evidence -> INSUFFICIENT_EVIDENCE passes through unchanged.
        if base_action == ExperienceAction.INSUFFICIENT_EVIDENCE:
            return (
                proposal_out,
                exp_decision,
                SuitabilityVerdict(
                    decision=SuitabilityTier.INSUFFICIENT_EVIDENCE,
                    suitability_score=0.0,
                    qualifies=True,
                    adjusted_confidence=proposal.confidence,
                    reason="NO_RELEVANT_EXPERIENCE",
                    evidence={},
                    strategy_id=exp_decision.strategy_id,
                    strategy_lifecycle=exp_decision.strategy_lifecycle,
                    timestamp=exp_decision.timestamp,
                ),
            )

        # Evidence exists. Compute a bounded suitability verdict.
        proposal_out, verdict = self._evaluate_with_evidence(proposal_out, exp_decision)
        return proposal_out, exp_decision, verdict

    def _evaluate_with_evidence(
        self,
        proposal: TradeProposal,
        exp_decision: PreTradeExperienceDecision,
    ) -> tuple[TradeProposal, SuitabilityVerdict]:
        """
        Builds the Phase 09 suitability verdict for a proposal that HAS evidence.

        Separated from `evaluate()` so callers and tests can apply the
        suitability logic deterministically against a known decision.
        """
        score = self._suitability_score(exp_decision)
        lifecycle = exp_decision.strategy_lifecycle
        reason = ""
        decision = SuitabilityTier.ALLOW
        qualifies = True
        adjusted_confidence = exp_decision.adjusted_confidence

        # WARN tier: evidence is mixed (positive expectancy but elevated drawdown
        # or a degrading recency picture) - the position is acceptable but the
        # operator should be aware.
        if score <= self.warn_suitability_floor or (
            exp_decision.drawdown_r >= self.severe_drawdown_r
        ):
            decision = SuitabilityTier.WARN
            reason = (
                "ELEVATED_DRAWDOWN_CONTEXT"
                if exp_decision.drawdown_r >= self.severe_drawdown_r
                else "LOW_SUITABILITY_SCORE"
            )
        elif exp_decision.expectancy_r <= self.warn_expectancy_threshold_r:
            decision = SuitabilityTier.WARN
            reason = "NEUTRAL_OR_NEGATIVE_EXPECTANCY"

        # Suitability below the qualify floor -> REJECT (a soft rejection).
        if score < self.min_suitability_to_qualify:
            decision = SuitabilityTier.REJECT
            qualifies = False
            reason = (
                f"SUITABILITY_BELOW_THRESHOLD ({score:.2f} < {self.min_suitability_to_qualify:.2f})"
            )
            adjusted_confidence = 0.0
            proposal = proposal.model_copy(
                update={
                    "action": ActionType.NO_TRADE,
                    "confidence": 0.0,
                    "rejection_reason": reason,
                    "final_action": "NO_TRADE",
                    "decision_stage": "TRADE_INTELLIGENCE_GATE",
                    "blocked_by": "SUITABILITY_GATE",
                }
            )

        # Tally observability counters.
        if decision == SuitabilityTier.ALLOW:
            self.gate_allow += 1
        elif decision == SuitabilityTier.WARN:
            self.gate_warn += 1
        elif decision == SuitabilityTier.REJECT:
            self.gate_reject += 1
        elif decision == SuitabilityTier.PENALIZE:
            self.gate_penalize += 1

        verdict = SuitabilityVerdict(
            decision=decision,
            suitability_score=score,
            qualifies=qualifies,
            adjusted_confidence=adjusted_confidence,
            reason=reason or "EVIDENCE_SUPPORTED",
            evidence=self._evidence_dict(exp_decision, score),
            strategy_id=exp_decision.strategy_id,
            strategy_lifecycle=lifecycle,
            timestamp=datetime.now(UTC),
        )
        return proposal, verdict

    # ------------------------------------------------------------------
    # Suitability scoring
    # ------------------------------------------------------------------

    def _suitability_score(self, d: PreTradeExperienceDecision) -> float:
        """
        Bounded [0, 1] suitability combining expectancy, drawdown, evidence and
        lifecycle. Positive expectancy and healthy evidence raise it; drawdown,
        recency decay and DEGRADED lifecycle lower it.
        """
        score = 0.5
        expectancy = d.expectancy_r or 0.0
        recent = d.recent_expectancy_r or 0.0
        dd = d.drawdown_r or 0.0

        score += max(-0.30, min(0.30, expectancy * 0.6))
        score += max(-0.15, min(0.15, recent * 0.4))
        score -= min(0.30, dd * 0.10)  # elevated drawdown penalizes suitability
        score += min(0.20, d.evidence_quality * 0.2)

        if d.strategy_lifecycle == StrategyLifecycle.DEGRADED:
            score -= 0.20
        elif d.strategy_lifecycle in (StrategyLifecycle.ACTIVE, StrategyLifecycle.VALIDATED):
            score += 0.10

        size = d.retrieved_sample_count or 0
        if size == 0:
            score -= 0.15  # almost no evidence
        elif size < 5:
            score -= 0.10

        return float(max(0.0, min(1.0, score)))

    @staticmethod
    def _evidence_dict(d: PreTradeExperienceDecision, score: float) -> dict[str, Any]:
        return {
            "samples": d.retrieved_sample_count,
            "similarity": d.similarity_score,
            "expectancy_r": d.expectancy_r,
            "recent_expectancy_r": d.recent_expectancy_r,
            "drawdown_r": d.drawdown_r,
            "evidence_quality": d.evidence_quality,
            "strategy_lifecycle": d.strategy_lifecycle.value,
            "suitability_score": round(score, 4),
        }

    @staticmethod
    def _verdict_from_phase08(
        action: SuitabilityTier, d: PreTradeExperienceDecision
    ) -> SuitabilityVerdict:
        return SuitabilityVerdict(
            decision=action,
            suitability_score=0.0,
            qualifies=(action != SuitabilityTier.REJECT),
            adjusted_confidence=d.adjusted_confidence,
            reason=d.penalty_reason or action.value,
            evidence={
                "strategy_lifecycle": d.strategy_lifecycle.value,
                "expectancy_r": d.expectancy_r,
                "recent_expectancy_r": d.recent_expectancy_r,
            },
            strategy_id=d.strategy_id,
            strategy_lifecycle=d.strategy_lifecycle,
            timestamp=d.timestamp,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "gate_allow": self.gate_allow,
            "gate_warn": self.gate_warn,
            "gate_penalize": self.gate_penalize,
            "gate_reject": self.gate_reject,
        }
