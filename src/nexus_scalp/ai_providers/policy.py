"""Deterministic decision policy (ECOSYSTEM-001, Section 11).

THE RULE
--------
Do NOT treat "AI says HOLD -> HOLD". The provider and the internal ML produce
EVIDENCE; this module scores that evidence with an explicit, versioned,
deterministic formula. External AI output is an *input* to the formula, never
the formula itself.

THE FORMULA (conceptual, Section 11):
    ExpectedHoldValue =
        P(hold beneficial) * expected_future_benefit
        - expected_adverse_cost
        - transaction_cost
        - uncertainty_penalty
        - regime_change_penalty

    ExpectedCloseValue is computed SEPARATELY, not as 1 - ExpectedHoldValue,
    because "hold because continuation is favourable" and "hold because there
    is not enough evidence to close" are NOT the same statement (Section 18).

Each candidate action -- HOLD, CLOSE, REDUCE, ADJUST_TP, ADJUST_SL -- gets a
normalized policy score from measurable quantities. There is no magical "AI
score": every term is named, bounded, unit-tested and configurable.

PROPERTIES
    * DETERMINISTIC: same inputs -> same output, byte for byte. Replay of the
      same snapshot must reproduce the same decision (Section 45).
    * AUDITABLE: every term is returned in the trace, so a decision can be
      explained as arithmetic rather than asserted as authority.
    * CONFIGURABLE: weights live in :class:`PolicyWeights`, not inline.
    * VERSIONED: ``POLICY_VERSION`` is stamped onto every decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.ai_providers.contract import AIProviderAction, PositionDecisionRequest

__all__ = ["POLICY_VERSION", "PolicyScores", "PolicyWeights", "score_action"]

#: Bump when the formula changes. Persisted with every decision so a historical
#: decision can be re-derived under the formula that produced it.
POLICY_VERSION = "policy_v1"


@dataclass(frozen=True)
class PolicyWeights:
    """Every tunable in the formula. Change here, never inline in code.

    Frozen so a policy in flight cannot be mutated mid-decision by another
    thread -- a decision must be scored against ONE weight set.
    """

    #: Dollar cost of one round-trip trade, in R units (spread + slippage).
    transaction_cost_r: float = 0.05
    #: Penalty per unit of provider uncertainty (calibration quality).
    uncertainty_weight: float = 1.0
    #: Penalty for the probability the regime flips against the position.
    regime_change_weight: float = 1.0
    #: Penalty per second of position age (dead money decays).
    time_decay_weight: float = 0.00001
    #: How much a provider's evidence counts vs. the deterministic read.
    provider_evidence_weight: float = 1.0
    #: Confidence below this treats the provider's action as no evidence at all.
    min_confidence: float = 0.35
    #: Floor on a CLOSE score: closing costs the transaction cost and forfeits
    #: remaining expectancy, so a weak close signal must clear a real bar.
    close_margin: float = 0.10
    #: A REDUCE score must beat HOLD by this margin to be worth acting on.
    reduce_margin: float = 0.15

    def to_dict(self) -> dict[str, float]:
        return {
            "transaction_cost_r": self.transaction_cost_r,
            "uncertainty_weight": self.uncertainty_weight,
            "regime_change_weight": self.regime_change_weight,
            "time_decay_weight": self.time_decay_weight,
            "provider_evidence_weight": self.provider_evidence_weight,
            "min_confidence": self.min_confidence,
            "close_margin": self.close_margin,
            "reduce_margin": self.reduce_margin,
        }


@dataclass
class PolicyScores:
    """The full result of scoring one decision.

    ``terms`` carries every individual component so the decision trace can show
    the arithmetic (Section 41). ``winner`` is the argmax, but the ORCHESTRATOR
    still applies gates -- a winning score is a recommendation, not authority.
    """

    scores: dict[str, float] = field(default_factory=dict)
    winner: str = AIProviderAction.NO_ACTION
    expected_hold_value: float = 0.0
    expected_close_value: float = 0.0
    terms: dict[str, Any] = field(default_factory=dict)
    policy_version: str = POLICY_VERSION
    reason_codes: list[str] = field(default_factory=list)

    @property
    def margin(self) -> float:
        """Gap between best and runner-up: 0.0 means it was a coin flip."""
        ordered = sorted(self.scores.values(), reverse=True)
        if len(ordered) < 2:
            return 0.0
        return ordered[0] - ordered[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scores": {k: round(v, 6) for k, v in self.scores.items()},
            "winner": self.winner,
            "margin": round(self.margin, 6),
            "expected_hold_value": round(self.expected_hold_value, 6),
            "expected_close_value": round(self.expected_close_value, 6),
            "terms": self.terms,
            "policy_version": self.policy_version,
            "reason_codes": list(self.reason_codes),
        }


def score_action(
    request: PositionDecisionRequest,
    *,
    p_hold: float,
    p_close: float,
    p_reduce: float,
    expected_remaining_r: float,
    expected_upside_r: float,
    expected_downside_r: float,
    regime_change_probability: float,
    uncertainty: float,
    confidence: float,
    action_hint: AIProviderAction = AIProviderAction.NO_ACTION,
    weights: PolicyWeights | None = None,
) -> PolicyScores:
    """Score every candidate action from measurable quantities only.

    DETERMINISTIC: no randomness, no clock read, no I/O. Replaying the same
    inputs reproduces the same numbers, which is what makes replay comparison
    (Section 45) meaningful rather than decorative.

    ``action_hint`` is the provider's *nominal* action; it biases evidence
    weighting but never decides. A provider with low confidence contributes
    almost nothing, which is how "confidence = 0.99 cannot justify increasing
    downside risk" (Section 17) reaches the formula rather than being a slogan.
    """
    w = weights or PolicyWeights()
    reasons: list[str] = []

    # -- sanitize inputs (the formula must never propagate NaN) ---------------
    def _f(x: float) -> float:
        return float(x) if isinstance(x, (int, float)) and math.isfinite(float(x)) else 0.0

    p_hold, p_close, p_reduce = _f(p_hold), _f(p_close), _f(p_reduce)
    exp_rem = _f(expected_remaining_r)
    exp_up, exp_down = _f(expected_upside_r), _f(expected_downside_r)
    regime_p = min(1.0, max(0.0, _f(regime_change_probability)))
    unc = min(1.0, max(0.0, _f(uncertainty)))
    conf = min(1.0, max(0.0, _f(confidence)))

    # Below the floor, the provider contributes no evidence at all: an
    # uncalibrated guess must not steer a decision.
    if conf < w.min_confidence:
        evidence_scale = 0.0
        reasons.append("EVIDENCE_BELOW_CONFIDENCE_FLOOR")
    else:
        evidence_scale = (conf - w.min_confidence) / max(1e-9, 1.0 - w.min_confidence)
        evidence_scale = min(1.0, max(0.0, evidence_scale)) * w.provider_evidence_weight

    # Deterministic read of the position itself, independent of any provider.
    rr = _f(request.reward_to_risk)
    dist_sl = max(_f(request.distance_to_sl), 1e-9)
    dist_tp = max(_f(request.distance_to_tp), 1e-9)
    age_sec = max(0.0, _f(request.position_age_sec))
    # Position's own R-momentum: is it currently in the money?
    in_money = 1.0 if _f(request.unrealized_r) > 0 else -1.0

    # -- shared penalties ------------------------------------------------------
    transaction_cost = w.transaction_cost_r
    uncertainty_penalty = w.uncertainty_weight * unc
    regime_penalty = w.regime_change_weight * regime_p
    time_decay = w.time_decay_weight * age_sec
    reasons.append(f"transaction_cost_r={transaction_cost:.4f}")
    reasons.append(f"uncertainty_penalty={uncertainty_penalty:.4f}")
    reasons.append(f"regime_change_penalty={regime_penalty:.4f}")

    # -- ExpectedHoldValue (Section 11) ----------------------------------------
    # Benefit scales with remaining expectancy and the probability of holding.
    hold_benefit = p_hold * (exp_up + exp_rem) * evidence_scale
    # Adverse cost: what we stand to give back if we hold and it reverses.
    hold_adverse = p_close * abs(exp_down) * evidence_scale
    expected_hold = (
        hold_benefit
        - hold_adverse
        - transaction_cost
        - uncertainty_penalty
        - regime_penalty
        - time_decay
    )

    # -- ExpectedCloseValue (computed SEPARATELY, Section 18) -----------------
    # Closing realizes the current R, pays the transaction cost, and FORFEITS
    # remaining expectancy. It is not the complement of holding.
    close_realizes = _f(request.unrealized_r)
    close_forfeits = exp_rem * evidence_scale  # giving this up by closing
    expected_close = (
        p_close * (close_realizes - transaction_cost + regime_penalty * 0.5)
        - p_close * close_forfeits
        - hold_benefit * 0.5  # opportunity cost of not continuing
        - uncertainty_penalty * 0.5
    )

    # -- candidate scores -------------------------------------------------------
    hold_score = expected_hold
    close_score = expected_close

    # REDUCE: keeps part of the upside while cutting exposure to the adverse
    # term -- but only beats HOLD when the evidence actually says so.
    reduce_score = expected_hold * 0.5 + (p_reduce * exp_rem * evidence_scale) - (
        transaction_cost * 0.5 + uncertainty_penalty * 0.5
    )
    if rr <= 1.0:
        reduce_score -= 0.05  # poor reward-to-risk already argues for less size

    # ADJUST_TP / ADJUST_SL: only score positively when there is a real
    # candidate AND the risk math supports it. TP extension needs headroom;
    # SL tightening is always worth more than widening, by construction.
    tp_headroom = dist_tp > dist_sl
    adjust_tp_score = 0.0
    if tp_headroom and evidence_scale > 0:
        adjust_tp_score = 0.5 * evidence_scale + 0.1 * min(1.0, rr / 3.0) - transaction_cost
    adjust_sl_score = 0.0
    if evidence_scale > 0 and regime_p > 0.5:
        adjust_sl_score = 0.4 * evidence_scale + 0.2 * regime_p

    # NO_ACTION is the honest default: it wins only if every other candidate is
    # negative, which is the correct reading of "no evidence to act" (Section 18).
    no_action_score = 0.0
    if max(hold_score, close_score, reduce_score, adjust_tp_score, adjust_sl_score) < 0:
        no_action_score = 0.001
        reasons.append("ALL_CANDIDATES_NEGATIVE")

    scores = {
        AIProviderAction.HOLD.value: hold_score,
        AIProviderAction.CLOSE.value: close_score,
        AIProviderAction.REDUCE.value: reduce_score,
        AIProviderAction.ADJUST_TP.value: adjust_tp_score,
        AIProviderAction.ADJUST_SL.value: adjust_sl_score,
        AIProviderAction.NO_ACTION.value: no_action_score,
    }
    winner = max(scores, key=scores.get)

    # Margins (Sections 11/17): a CLOSE must clear a real bar, and so must a
    # REDUCE -- otherwise the noise floor would generate trades.
    if winner == AIProviderAction.CLOSE and scores[AIProviderAction.CLOSE.value] < (
        scores[AIProviderAction.HOLD.value] + w.close_margin
    ):
        winner = AIProviderAction.HOLD if scores[AIProviderAction.HOLD.value] > 0 else AIProviderAction.NO_ACTION
        reasons.append("CLOSE_BELOW_MARGIN")
    if winner == AIProviderAction.REDUCE and scores[AIProviderAction.REDUCE.value] < (
        scores[AIProviderAction.HOLD.value] + w.reduce_margin
    ):
        winner = AIProviderAction.HOLD if scores[AIProviderAction.HOLD.value] > 0 else AIProviderAction.NO_ACTION
        reasons.append("REDUCE_BELOW_MARGIN")

    # The provider's own hint is recorded as evidence, never as the decision.
    if action_hint is not AIProviderAction.NO_ACTION and evidence_scale > 0:
        if str(action_hint) != winner:
            reasons.append(f"PROVIDER_HINT_OVERRIDDEN({action_hint})")

    return PolicyScores(
        scores=scores,
        winner=str(winner),
        expected_hold_value=expected_hold,
        expected_close_value=expected_close,
        terms={
            "hold_benefit": hold_benefit,
            "hold_adverse": hold_adverse,
            "close_realizes": close_realizes,
            "close_forfeits": close_forfeits,
            "transaction_cost_r": transaction_cost,
            "uncertainty_penalty": uncertainty_penalty,
            "regime_penalty": regime_penalty,
            "time_decay": time_decay,
            "evidence_scale": evidence_scale,
            "in_money": in_money,
            "tp_headroom": tp_headroom,
            "reward_to_risk": rr,
            "confidence": conf,
            "weights": w.to_dict(),
        },
        policy_version=POLICY_VERSION,
        reason_codes=reasons,
    )
