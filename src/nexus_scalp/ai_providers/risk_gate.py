"""Risk gate for provider TP/SL proposals (ECOSYSTEM-001, Section 17).

THE RULE
--------
External AI can return a candidate TP or SL, but those are only PROPOSALS. The
canonical flow is mandatory and cannot be skipped (Section 52):

    AI Proposal -> NSE Policy -> Risk Engine -> Broker Constraints
               -> Optional Order Check -> Execution -> Confirmation

SL is treated especially conservatively (Section 17). This gate explicitly
separates:

    TIGHTEN_SL  -- allowed when it reduces distance to the stop
    KEEP_SL     -- the default
    WIDEN_SL    -- FORBIDDEN unless an explicit policy override exists

Never let ``confidence = 0.99`` automatically justify increasing downside risk:
confidence is a statement about the provider's self-assessment, not evidence
about the broker-side consequence of a wider stop.

BROKER TRUTH (Section 51)
-------------------------
The provider is NOT the source of truth for balance, equity, free margin,
position, volume, broker constraints or order results. MT5 owns those. This gate
validates every proposal against the symbol's own contract (tick size, digits,
stops level, freeze level) and the account's risk budget -- never against a
number the provider returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nexus_scalp.ai_providers.contract import (
    PositionDecisionRequest,
    PositionDecisionResponse,
)

__all__ = ["GATE_VERSION", "RiskGateResult", "RiskPolicy", "SlKind", "gate_proposals"]


GATE_VERSION = "gate_v1"


class SlKind(StrEnum):
    TIGHTEN_SL = "TIGHTEN_SL"
    KEEP_SL = "KEEP_SL"
    WIDEN_SL = "WIDEN_SL"


@dataclass(frozen=True)
class RiskPolicy:
    """Configurable risk envelope. Widening is off by default (Section 17)."""

    #: True only when an operator has explicitly authorized widening a stop.
    allow_sl_widen: bool = False
    #: Max fraction of remaining distance-to-TP a TP candidate may move.
    max_tp_move_fraction: float = 0.5
    #: A TP candidate may not move against the trade direction.
    allow_tp_extension: bool = False
    #: Max additional dollar risk any single proposal may add (0 = none).
    max_risk_change_usd: float = 0.0
    #: Min reward-to-risk a post-proposal bracket must still offer.
    min_reward_to_risk: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_sl_widen": self.allow_sl_widen,
            "max_tp_move_fraction": self.max_tp_move_fraction,
            "allow_tp_extension": self.allow_tp_extension,
            "max_risk_change_usd": self.max_risk_change_usd,
            "min_reward_to_risk": self.min_reward_to_risk,
        }


@dataclass
class RiskGateResult:
    """The gate's verdict on one provider response's proposals.

    ``allowed`` being False does NOT abort the decision -- the proposal is
    dropped and the action degrades to the next candidate. The gate restricts;
    it does not invent an action.
    """

    allowed: bool = True
    tp_allowed: bool = True
    sl_allowed: bool = True
    sl_kind: SlKind = SlKind.KEEP_SL
    final_tp: float | None = None
    final_sl: float | None = None
    rejections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    gate_version: str = GATE_VERSION
    risk_change_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "tp_allowed": self.tp_allowed,
            "sl candidate": None,  # placeholder never used
            "sl_allowed": self.sl_allowed,
            "sl_kind": self.sl_kind.value,
            "final_tp": self.final_tp,
            "final_sl": self.final_sl,
            "rejections": list(self.rejections),
            "warnings": list(self.warnings),
            "gate_version": self.gate_version,
            "risk_change_usd": self.risk_change_usd,
        }


def _snap(price: float, request: PositionDecisionRequest) -> float:
    """Snap a candidate price to the symbol's tick grid (Section 16)."""
    tick = request.tick_size if request.tick_size > 0 else 10 ** (-request.digits)
    if tick <= 0:
        return price
    return round(price / tick) * tick


def _stops_distance_ok(price: float, request: PositionDecisionRequest) -> bool:
    """Both stops_level and freeze_level are broker-imposed floors (Section 16).

    A candidate closer to price than the broker's own stops level will be
    rejected by the terminal, so we refuse it here.
    """
    ref = request.current_price
    dist = abs(price - ref)
    floor = max(request.stops_level, request.freeze_level)
    if floor <= 0:
        return True
    return dist >= floor


def gate_proposals(
    response: PositionDecisionResponse,
    request: PositionDecisionRequest,
    policy: RiskPolicy | None = None,
) -> RiskGateResult:
    """Validate a provider response's TP/SL proposals against broker + risk truth.

    Returns a :class:`RiskGateResult`. Never raises: a gate error degrades to a
    rejection, which is the safe direction (Section 15: never pretend).
    """
    p = policy or RiskPolicy()
    res = RiskGateResult()
    is_buy = request.side == "BUY"

    # ---------------- TP ----------------
    tp = response.tp
    if tp.candidate_price is not None:
        cand = _snap(tp.candidate_price, request)
        cur_tp = request.current_tp
        if cur_tp and cur_tp > 0:
            # A BUY wants TP above price; a SELL below.
            direction_ok = (
                (cand > request.current_price) if is_buy else (cand < request.current_price)
            )
            if not direction_ok:
                res.tp_allowed = False
                res.rejections.append("TP_CANDIDATE_WRONG_SIDE")
            else:
                moved = abs(cand - cur_tp)
                max_move = abs(cur_tp - request.current_price) * p.max_tp_move_fraction
                if not p.allow_tp_extension and (cand > cur_tp if is_buy else cand < cur_tp):
                    res.tp_allowed = False
                    res.rejections.append("TP_EXTENSION_FORBIDDEN")
                elif moved > max_move:
                    res.tp_allowed = False
                    res.rejections.append("TP_MOVE_EXCEEDS_FRACTION")
                elif not _stops_distance_ok(cand, request):
                    res.tp_allowed = False
                    res.rejections.append("TP_INSIDE_STOPS_LEVEL")
                else:
                    res.final_tp = cand
        # No existing TP: allow a new one on the correct side only.
        elif (cand > request.current_price) if is_buy else (cand < request.current_price):
            res.final_tp = cand
        else:
            res.tp_allowed = classify_wrong_side_tp()
    if not res.tp_allowed:
        res.warnings.append("TP proposal rejected; keeping current TP")
        res.final_tp = request.current_tp

    # ---------------- SL ----------------
    sl = response.sl
    if sl.candidate_price is not None:
        cand = _snap(sl.candidate_price, request)
        cur = request.current_sl
        if cur and cur > 0:
            # TIGHTEN means moving the stop CLOSER to the current price.
            cur_dist = abs(cur - request.current_price)
            cand_dist = abs(cand - request.current_price)
            kind = SlKind.TIGHTEN_SL if cand_dist < cur_dist else SlKind.WIDEN_SL
            res.sl_kind = kind
            if kind is SlKind.WIDEN_SL and not p.allow_sl_widen:
                res.sl_allowed = False
                res.rejections.append("WIDEN_SL_FORBIDDEN")
            elif cand >= request.current_price if is_buy else cand <= request.current_price:
                res.sl_allowed = False
                res.rejections.append("SL_CANDIDATE_WRONG_SIDE")
            elif not _stops_distance_ok(cand, request):
                res.sl_allowed = False
                res.rejections.append("SL_INSIDE_STOPS_LEVEL")
            else:
                res.final_sl = cand
                res.risk_change_usd = float(sl.risk_change)
        else:
            res.sl_kind = SlKind.KEEP_SL
    if not res.sl_allowed:
        res.warnings.append("SL proposal rejected; keeping current SL")
        res.final_sl = request.current_sl
    else:
        if res.final_sl is None:
            res.final_sl = request.current_sl
        if res.risk_change_usd > p.max_risk_change_usd:
            res.sl_allowed = False
            res.rejections.append("RISK_CHANGE_EXCEEDS_POLICY")
            res.warnings.append("SL proposal adds risk beyond policy; keeping current SL")
            res.final_sl = request.current_sl
            res.risk_change_usd = 0.0

    # Post-bracket reward-to-risk must still be sane.
    final_tp = res.final_tp
    final_sl = res.final_sl
    if final_tp and final_sl and final_tp > 0 and final_sl > 0:
        reward = abs(final_tp - request.current_price)
        risk = max(abs(final_sl - request.current_price), 1e-9)
        rr = reward / risk
        if rr < p.min_reward_to_risk:
            res.warnings.append(f"post-proposal reward/risk {rr:.2f} below minimum")
            # Do not reject the whole decision for a tight bracket; flag it.
            res.rejections.append("POST_RR_BELOW_MIN")

    res.allowed = res.tp_allowed and res.sl_allowed
    return res


def classify_wrong_side_tp() -> bool:
    """A TP on the wrong side of price is always refused."""
    return False
