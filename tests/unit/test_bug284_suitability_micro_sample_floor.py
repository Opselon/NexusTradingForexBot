"""BUG-284 regression net — suitability REJECT must not fire on micro-samples.

Wave 2026-09-14 (lane 08 §4, VERIFIED ledger): 473 of 1,373 production
decisions (34.5%) died at SUITABILITY_BELOW_THRESHOLD, driven by families with
1-4 closed samples. The evidence contract inverted:

    zero evidence  -> INSUFFICIENT_EVIDENCE -> proposal passes unchanged
    ONE loss       -> suitability ~0.15      -> proposal REJECTED (NO_TRADE)

i.e. "lost twice -> stop trading", beyond the designed risk gates. The fix
demotes a suitability REJECT to WARN while the retrieved sample count is below
the evaluator's EVALUATING floor (5 == StrategyEvaluator.min_samples_evaluating,
the same "evidence exists" basis this subsystem already uses). Never an
upgrade; Phase-08 lifecycle REJECTs (RETIRED/QUARANTINED) stay absolute.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.experience.models import (
    ExperienceAction,
    PreTradeExperienceDecision,
    StrategyLifecycle,
)
from nexus_scalp.intelligence.gate import PreTradeIntelligenceGate, SuitabilityTier


class _StubEngine:
    """Placeholder: _evaluate_with_evidence never touches the engine."""

    def evaluate_proposal(self, **_kw):  # pragma: no cover - unused
        raise AssertionError("must not be called by _evaluate_with_evidence")


def _proposal(confidence: float = 0.70):
    from nexus_scalp.domain.models import TradeProposal

    return TradeProposal(
        request_id="req_bug284",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY_MARKET,
        confidence=confidence,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        reason_code="PREDICTIVE_LIMIT",
    )


def _decision(samples: int, expectancy: float = -1.0):
    return PreTradeExperienceDecision(
        decision_id="d1",
        request_id="req_bug284",
        timestamp=datetime.now(UTC),
        action=ExperienceAction.ALLOW,
        qualifies_trade=True,
        adjusted_confidence=0.70,
        strategy_id="strat_x",
        strategy_lifecycle=StrategyLifecycle.EVALUATING,
        retrieved_sample_count=samples,
        similarity_score=0.9,
        evidence_quality=0.5,
        expectancy_r=expectancy,
        recent_expectancy_r=expectancy,
        drawdown_r=0.0,
    )


def _gate() -> PreTradeIntelligenceGate:
    return PreTradeIntelligenceGate(experience_engine=_StubEngine())  # type: ignore[arg-type]


def test_one_loss_micro_sample_no_longer_rejects():
    """RED-before: n=1 negative expectancy -> REJECT + NO_TRADE + conf 0.0."""
    gate = _gate()
    proposal = _proposal()
    out, verdict = gate._evaluate_with_evidence(proposal, _decision(samples=1))
    assert verdict.decision == SuitabilityTier.WARN
    assert verdict.qualifies is True
    assert verdict.reason.startswith("MICRO_SAMPLE_INSUFFICIENT_EVIDENCE")
    # The proposal is UNCHANGED — WARN never rewrites action or confidence.
    assert out.action == ActionType.BUY_MARKET
    assert out.confidence == 0.70


def test_four_samples_still_below_floor_but_warn_only():
    gate = _gate()
    out, verdict = gate._evaluate_with_evidence(_proposal(), _decision(samples=4))
    assert verdict.decision == SuitabilityTier.WARN
    assert out.confidence == 0.70


def test_at_floor_full_strength_reject_preserved():
    """Guard NOT weakened: at n >= min_samples_to_reject the historical
    REJECT semantics (NO_TRADE, conf 0.0, blocked_by SUITABILITY_GATE) hold."""
    gate = _gate()
    proposal = _proposal()
    out, verdict = gate._evaluate_with_evidence(proposal, _decision(samples=5))
    assert verdict.decision == SuitabilityTier.REJECT
    assert verdict.qualifies is False
    assert "SUITABILITY_BELOW_THRESHOLD" in verdict.reason
    assert out.action == ActionType.NO_TRADE
    assert out.confidence == 0.0
    assert out.blocked_by == "SUITABILITY_GATE"
    assert gate.gate_reject == 1


def test_positive_micro_sample_stays_warn_or_allow_unchanged():
    """A good micro-sample picture must behave exactly as before the fix
    (the demotion only touches the sub-floor REJECT branch)."""
    gate = _gate()
    _, verdict = gate._evaluate_with_evidence(_proposal(), _decision(samples=3, expectancy=0.2))
    assert verdict.decision in (SuitabilityTier.ALLOW, SuitabilityTier.WARN)


def test_floor_is_constructor_configurable():
    gate = PreTradeIntelligenceGate(
        experience_engine=_StubEngine(),  # type: ignore[arg-type]
        min_samples_to_reject=8,
    )
    assert gate.min_samples_to_reject == 8
    _, verdict = gate._evaluate_with_evidence(_proposal(), _decision(samples=6))
    assert verdict.decision == SuitabilityTier.WARN  # 6 < 8 -> demoted


def test_floor_never_zero_or_negative():
    gate = PreTradeIntelligenceGate(
        experience_engine=_StubEngine(),  # type: ignore[arg-type]
        min_samples_to_reject=0,
    )
    assert gate.min_samples_to_reject == 1  # clamped: always at least 1 sample


def test_default_floor_matches_evaluator_contract():
    """The 5-sample basis is SHARED with StrategyEvaluator.min_samples_evaluating
    — if one moves without the other, the funnel vocabulary drifts. Pin it."""
    import inspect

    from nexus_scalp.experience.evaluator import StrategyEvaluator

    sig = inspect.signature(StrategyEvaluator.__init__)
    assert sig.parameters["min_samples_evaluating"].default == 5
    assert PreTradeIntelligenceGate.__init__.__defaults__ is not None
    # The gate's own default:
    import re

    src = inspect.getsource(PreTradeIntelligenceGate.__init__)
    m = re.search(r"min_samples_to_reject: int = (\d+)", src)
    assert m and int(m.group(1)) == 5
