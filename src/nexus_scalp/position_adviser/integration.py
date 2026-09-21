"""Decide-system integration for the Position Decision Adviser (TASK-POSA-001).

This is the ONLY place the adviser touches the hold/close decide path.

Design contract (must hold whether the adviser is enabled or not):
    * The adviser is consulted AFTER the existing scoring stage and AFTER the
      profit-giveback SAFETY OVERRIDE have produced the final hold score, so it
      can only ever make a verdict MORE conservative — never lift a score,
      never extend a position, never weaken a protection verdict.
    * The adjustment is <= 0 by construction (service._bounded_adjustment) and
      is clamped here again to [0, -max_hold_score_penalty].
    * When the adviser is DISABLED (the default), ``apply_advisory_to_hold_score``
      returns the score unchanged and records nothing. The decide system is
      byte-identical to its pre-adviser behaviour.
    * Every failure path (missing keys, non-finite features, model error) is
      fail-closed: the score is returned unchanged and a structured warning is
      logged. No fabricated confidence, no fabricated verdict.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.position_adviser.models import AdviserActivation

logger = get_logger("nexus_scalp.position_adviser.integration")

#: The keys the adviser needs from the manage-active-positions loop. All of
#: these are already computed per ticket by the existing stage; we re-derive
#: the two R-multiples here rather than asking the loop for anything new.
_REQUIRED_LIVE_KEYS: tuple[str, ...] = (
    "unrealized_pnl_r",
    "current_r_net",
    "current_return",
    "distance_to_stop_r",
    "distance_to_target_r",
    "position_age_bars",
    "atr",
    "spread",
    "estimated_slippage",
    "model_probability",
    "model_confidence",
    "signal_age",
)


def build_position_state_for_adviser(
    *,
    pos: Any,
    ticket: int,
    price_current: float,
    atr: float,
    spread: float,
    initial_risk_usd: float,
    holding_duration_sec: float,
    signal_age: float,
    model_probability: float,
    model_confidence: float,
    estimated_slippage: float = 0.05,
    contract_size: float = 100.0,
) -> dict[str, Any]:
    """Derive the adviser's causal state from values the loop already has.

    Every value here is causal (known at decision time). Nothing is read from
    the future. ``initial_risk_usd`` is the planned dollar risk the engine
    computed at entry; all R-multiples are expressed against it, matching the
    position-dataset generator's convention.
    """
    is_buy = bool(getattr(pos, "type", None) == 1 or str(getattr(pos, "type", "")).upper() == "BUY")
    entry = float(getattr(pos, "price_open", 0.0) or 0.0)
    cur = float(price_current)
    if is_buy:
        price_delta = cur - entry
    else:
        price_delta = entry - cur

    risk = max(float(initial_risk_usd), 1e-9)
    unreal_r = price_delta / risk  # R-multiple of the current unrealized PnL

    stop = float(getattr(pos, "sl", 0.0) or 0.0)
    tp = float(getattr(pos, "tp", 0.0) or 0.0)
    if stop > 0.0:
        dist_stop_r = abs(cur - stop) / risk
    else:
        dist_stop_r = 0.0
    if tp > 0.0:
        dist_target_r = abs(tp - cur) / risk
    else:
        dist_target_r = 0.0

    ret = price_delta / max(entry, 1e-9)

    return {
        "unrealized_pnl_r": unreal_r,
        "current_r_net": unreal_r,
        "current_return": ret,
        "distance_to_stop_r": dist_stop_r,
        "distance_to_target_r": dist_target_r,
        "position_age_bars": max(0, int(holding_duration_sec / 60.0)),
        "atr": float(atr),
        "spread": float(spread),
        "estimated_slippage": float(estimated_slippage),
        "model_probability": float(model_probability),
        "model_confidence": float(model_confidence),
        "signal_age": max(0.0, float(signal_age)),
    }


def apply_advisory_to_hold_score(
    *,
    ticket: int,
    hold_score: int,
    position_state: dict[str, Any],
    service: Any,
) -> tuple[int, dict[str, Any] | None]:
    """Apply an adviser verdict to an already-final hold score.

    Returns ``(new_hold_score, advisory_dict_or_None)``. When the adviser is
    disabled, refused, or errors, the hold score is returned UNCHANGED — the
    decide system then behaves exactly as it did before the adviser existed.
    """
    if service is None or not getattr(service, "enabled", False):
        return hold_score, None

    try:
        advisory = service.evaluate(ticket, position_state)
    except Exception as exc:  # fail closed; never propagate into the hot path
        logger.warning("[ADVISER] event=INTEGRATION_EVAL_ERROR ticket=%s error=%s", ticket, exc)
        return hold_score, None

    if advisory is None:
        return hold_score, None

    adj = float(getattr(advisory, "hold_score_adjustment", 0.0))
    if adj >= 0.0:
        # KEEP verdict or below-threshold: no influence at all.
        return hold_score, advisory.to_dict()

    max_pen = float(getattr(getattr(service, "config", None), "max_hold_score_penalty", 25.0))
    clamped = max(-max_pen, min(0.0, adj))
    new_score = max(0, min(100, int(round(hold_score + clamped))))

    if new_score == hold_score:
        return hold_score, advisory.to_dict()

    logger.info(
        "[ADVISER] event=HOLD_SCORE_ADJUSTED ticket=%s action=%s confidence=%.4f "
        "hold=%d -> %d (adj=%.2f activation=%s)",
        ticket,
        getattr(advisory, "action", "?"),
        float(getattr(advisory, "confidence", 0.0)),
        hold_score,
        new_score,
        clamped,
        getattr(advisory, "activation", AdviserActivation.DISABLED),
    )
    return new_score, advisory.to_dict()


__all__ = [
    "apply_advisory_to_hold_score",
    "build_position_state_for_adviser",
]
