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

TRAIN/SERVE PARITY (ML-POSITION-FORENSICS, the F-series audit): every field
built here must be numerically IDENTICAL in semantics to the same field the
position-dataset generator (``model_generation/position_replay.py``) writes,
because the trained head saw only generator values. The pinned equivalences:

    generator                                    here
    -------------------------------------------  -------------------------------
    r_distance = max(|entry-sl|, 0.20)           initial_risk_price (price units,
                                                  floored here the same way)
    unrealized_pnl_r = current_r_net =
      (price_delta - friction) / r_distance      net R against PRICE distance
      where friction =
        spread_usd + 2 * slippage_usd            canonical execution assumptions
    current_return = price_delta / entry         unchanged (gross)
    distance_to_stop_r = |price-sl| / r_distance price distance / r_distance
    position_age_bars = bars since entry         holding_duration_sec / 60
    signal_age = pos_age (bars)                  signal_age_bars (entry == signal)
    spread = config spread                       LIVE observed spread (see note)
    estimated_slippage = config slippage         canonical execution assumptions

NOTE on ``spread``: the generator writes the fixed config value (constant
column), while here we pass the LIVE observed spread — it is real decision-time
information and passing a constant would be fabrication. The constant training
std makes raw live values scale to huge z-scores, which is why
``AdviserScaler.transform`` clips to +/-5 identically at train and serve.

The snapshot contract (F1): the returned dict also carries
``snapshot_observed_at`` (monotonic seconds) and ``snapshot_id`` (content hash
of the decision-relevant position fields). ``service.evaluate`` REFUSES any
snapshot that is missing, older than ``max_snapshot_age_sec``, or identical to
the last evaluated snapshot for that ticket — stale position state can never
produce a prediction, and one state can never produce two decisions.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.position_adviser.features import ADVISER_FEATURE_ORDER
from nexus_scalp.position_adviser.models import AdviserActivation

logger = get_logger("nexus_scalp.position_adviser.integration")

#: Fallback execution assumptions — mirrors ReplayExecutionConfig defaults
#: (spread 0.147, slippage 0.05), used only if the canonical config cannot be
#: read, so cost semantics degrade to the generator's own fallback values.
_FALLBACK_SPREAD_USD = 0.147
_FALLBACK_SLIPPAGE_USD = 0.05

#: Generator floor for the R denominator (position_replay: r_distance floor).
_MIN_R_DISTANCE = 0.20

#: The keys the adviser needs from the manage-active-positions loop. All of
#: these are already computed per ticket by the existing stage; we re-derive
#: the two R-multiples here rather than asking the loop for anything new.
_REQUIRED_LIVE_KEYS: tuple[str, ...] = ADVISER_FEATURE_ORDER


def _execution_cost_assumptions() -> tuple[float, float]:
    """(spread_usd, slippage_usd) from the SAME canonical config the position
    dataset generator reads — one source of truth for cost semantics, so live
    net-R can never drift from trained net-R."""
    try:
        from nexus_scalp.configuration.execution_costs import get_execution_assumptions

        assumptions = get_execution_assumptions()
        return float(assumptions.spread.mean), float(assumptions.slippage.paper_measured_p95)
    except Exception:  # config unreadable -> generator's own fallback values
        logger.warning(
            "[ADVISER] event=COST_ASSUMPTIONS_FALLBACK using generator-default spread/slippage"
        )
        return _FALLBACK_SPREAD_USD, _FALLBACK_SLIPPAGE_USD


def build_position_state_for_adviser(
    *,
    pos: Any,
    ticket: int,
    price_current: float,
    atr: float,
    spread: float,
    initial_risk_price: float,
    holding_duration_sec: float,
    signal_age_bars: float,
    model_probability: float,
    model_confidence: float,
    contract_size: float = 100.0,
) -> dict[str, Any]:
    """Derive the adviser's causal state from values the loop already has.

    Every value here is causal (known at decision time). Nothing is read from
    the future. Parity with the generator is explicit in the module docstring;
    the two parameters that were previously mis-unit'd:

        initial_risk_price  the INITIAL STOP DISTANCE IN PRICE UNITS
                            (generator ``r_distance``), NOT dollars. The loop
                            previously passed ``volume * contract_size *
                            stop_distance`` (dollars), which scaled every
                            R feature by 1/(volume*contract_size) — e.g. 10x
                            too small for a 0.1-lot XAUUSD position.
        signal_age_bars     bars since the entry signal. The generator writes
                            ``signal_age = position_age`` (entry == signal);
                            the loop previously passed
                            ``entry_signal_age_sec`` (SECONDS, and no writer
                            ever set it, so it was structurally 0.0).

    The returned dict carries the two snapshot-contract keys consumed (and
    required) by ``service.evaluate``.
    """
    is_buy = bool(getattr(pos, "type", None) == 1 or str(getattr(pos, "type", "")).upper() == "BUY")
    entry = float(getattr(pos, "price_open", 0.0) or 0.0)
    cur = float(price_current)
    if is_buy:
        price_delta = cur - entry
    else:
        price_delta = entry - cur

    # R denominator: generator convention (initial stop distance in price
    # units, floored at 0.20) — NEVER dollars.
    risk = max(float(initial_risk_price), _MIN_R_DISTANCE)

    # Round-trip friction in price units: spread + 2 * slippage, exactly the
    # generator's `friction_points` definition, from the same config source.
    spread_cfg, slippage_cfg = _execution_cost_assumptions()
    friction = spread_cfg + 2.0 * slippage_cfg

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

    # Net R (friction-adjusted), matching generator `unrealized_pnl_r` ==
    # `current_r_net` == cur_r_net.
    net_r = (price_delta - friction) / risk

    position_age_bars = max(0, int(holding_duration_sec / 60.0))

    state: dict[str, Any] = {
        "unrealized_pnl_r": net_r,
        "current_r_net": net_r,
        "current_return": ret,
        "distance_to_stop_r": dist_stop_r,
        "distance_to_target_r": dist_target_r,
        "position_age_bars": position_age_bars,
        "atr": float(atr),
        "spread": float(spread),
        "estimated_slippage": slippage_cfg,
        "model_probability": float(model_probability),
        "model_confidence": float(model_confidence),
        "signal_age": max(0.0, float(signal_age_bars)),
        # Snapshot contract (F1): identity of THIS observation + when it was
        # observed. service.evaluate refuses stale/missing/duplicate values.
        "snapshot_observed_at": time.monotonic(),
        "snapshot_id": _snapshot_id(
            ticket=ticket,
            entry=entry,
            cur=cur,
            stop=stop,
            tp=tp,
            volume=float(getattr(pos, "volume", 0.0) or 0.0),
            position_age_bars=position_age_bars,
        ),
    }
    return state


def _snapshot_id(
    *,
    ticket: int,
    entry: float,
    cur: float,
    stop: float,
    tp: float,
    volume: float,
    position_age_bars: int,
) -> str:
    """Content hash of the decision-relevant position fields.

    Two evaluations see "the same snapshot" iff none of the fields that could
    change the decision changed: identity, prices, protection levels, size, or
    age. A partial close, an external SL/TP edit, or a price move therefore
    always yields a NEW id (eligible for evaluation), while a re-delivery of
    unchanged state is recognised as a duplicate and refused.
    """
    raw = f"{ticket}|{entry:.6f}|{cur:.6f}|{stop:.6f}|{tp:.6f}|{volume:.6f}|{position_age_bars}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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
    new_score = max(0, min(100, round(hold_score + clamped)))

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
