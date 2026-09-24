"""Decide-system integration for the AI provider ecosystem (ECOSYSTEM-001).

This is the ONLY place AI provider evidence touches the hold/close decide path,
and it mirrors the existing position-adviser seam exactly (Section 6: the
internal ML remains first class; this adds external evidence alongside it, not
instead of it).

DESIGN CONTRACT — must hold whether the feature is on or off:
    * Consulted AFTER the scoring stage and the profit-giveback SAFETY OVERRIDE
      have produced the final hold score, so the only possible influence is to
      make a verdict MORE conservative.
    * The adjustment is <= 0 by construction and clamped here again to
      [0, -max_provider_penalty]. A provider can never lift a score, extend a
      position's life, or weaken a protection verdict.
    * DISABLED by default: ``apply_provider_evidence_to_hold_score`` returns the
      score unchanged and records nothing, so the decide system runs
      byte-identically to its pre-ecosystem behaviour.
    * Every failure path (provider error, malformed response, timeout, circuit
      open, schema violation) is fail-closed: the score is returned UNCHANGED
      with a structured warning. A provider failure never fabricates a verdict
      and never blocks the position manager.
    * NEVER in the tick hot path's critical section: evaluation is bounded by
      the orchestrator's own deadline, and a slow external model cannot delay a
      deterministic safety operation (Section 35).
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.ai_providers.integration")

__all__ = [
    "apply_provider_evidence_to_hold_score",
    "build_provider_request_state",
]


def build_provider_request_state(
    *, pos: Any, ticket: int, price_current: float, atr: float, spread: float
) -> dict[str, Any]:
    """Derive the values a provider request needs from what the loop already has.

    Causal only: nothing here is read from the future. The caller owns the full
    snapshot build; this is the minimal bridge the decide path can supply without
    a new hot-path query.
    """
    is_buy = bool(getattr(pos, "type", None) == 1 or str(getattr(pos, "type", "")).upper() == "BUY")
    entry = float(getattr(pos, "price_open", 0.0) or 0.0)
    cur = float(price_current)
    return {
        "ticket": int(ticket),
        "is_buy": is_buy,
        "entry_price": entry,
        "current_price": cur,
        "sl": float(getattr(pos, "sl", 0.0) or 0.0),
        "tp": float(getattr(pos, "tp", 0.0) or 0.0),
        "volume": float(getattr(pos, "volume", 0.0) or 0.0),
        "profit": float(getattr(pos, "profit", 0.0) or 0.0),
        "atr": float(atr),
        "spread": float(spread),
    }


def apply_provider_evidence_to_hold_score(
    *, ticket: int, hold_score: int, position_state: dict[str, Any], orchestrator: Any
) -> tuple[int, dict[str, Any] | None]:
    """Apply AI provider evidence to an already-final hold score.

    Returns ``(new_hold_score, decision_dict_or_None)``. When the feature is
    disabled, refused, or errors, the hold score is returned UNCHANGED -- the
    decide system then behaves exactly as it did before the ecosystem existed.

    The only channel through which provider evidence can touch execution is a
    bounded NEGATIVE adjustment, mirroring the adviser's own invariant.
    """
    if orchestrator is None:
        return hold_score, None
    try:
        if not getattr(orchestrator, "enabled_for_decide", True):
            return hold_score, None
    except Exception:
        return hold_score, None

    try:
        request = getattr(orchestrator, "build_request_from_state", None)
        if request is None:
            return hold_score, None
        outcome = orchestrator.evaluate(request(position_state))
    except Exception as exc:  # fail closed; never propagate into the hot path
        logger.warning("[AI-PROVIDER] event=INTEGRATION_EVAL_ERROR ticket=%s error=%s", ticket, exc)
        return hold_score, None

    if outcome is None:
        return hold_score, None

    adjustment = _bounded_adjustment(outcome, orchestrator)
    decision_dict = outcome.to_dict() if hasattr(outcome, "to_dict") else None

    if adjustment >= 0.0:
        # A HOLD/NO_ACTION verdict with no penalty: record, never adjust.
        return hold_score, decision_dict

    new_score = max(0, min(100, round(hold_score + adjustment)))
    if new_score == hold_score:
        return hold_score, decision_dict

    logger.info(
        "[AI-PROVIDER] event=HOLD_SCORE_ADJUSTED ticket=%s action=%s "
        "hold=%d -> %d (adj=%.2f mode=%s providers=%s fallback=%s)",
        ticket,
        outcome.final_action,
        hold_score,
        new_score,
        adjustment,
        getattr(outcome, "decision_mode", "?"),
        getattr(outcome, "providers_used", []),
        getattr(outcome, "fallback_used", False),
    )
    return new_score, decision_dict


def _bounded_adjustment(outcome: Any, orchestrator: Any) -> float:
    """Compute the hold-score penalty. ALWAYS <= 0 (Sections 1, 11).

    The penalty is a function of the policy's own CLOSE/REDUCE evidence, not of
    a provider's confidence alone: a high-confidence CLOSE with a real expected
    downside is what moves the score, because confidence by itself is not
    evidence about consequences (Section 17).
    """
    max_pen = float(getattr(orchestrator, "max_provider_penalty", 25.0))
    scores = getattr(getattr(outcome, "policy", None), "scores", {}) or {}
    close_v = float(scores.get("CLOSE", 0.0))
    reduce_v = float(scores.get("REDUCE", 0.0))
    hold_v = float(scores.get("HOLD", 0.0))
    action = getattr(outcome, "final_action", "NO_ACTION")

    # Only an evidence-backed negative verdict can lower the score.
    if action in ("HOLD", "NO_ACTION", "ADJUST_TP"):
        return 0.0
    if action == "CLOSE":
        raw = -abs(close_v) * 100.0
    elif action == "REDUCE":
        raw = -abs(reduce_v) * 100.0
    else:
        return 0.0
    # Never let a positive-looking margin invert into a lift.
    if hold_v > close_v and action == "CLOSE":
        return 0.0
    return max(-max_pen, min(0.0, raw))
