"""System One adapter (ECOSYSTEM-001, Section 4).

VERIFIED LIVE RESPONSE SHAPE (probed against the operator's endpoint):
    HTTP 200
    {"model":"typesafe/jev-1.13-20260917",
     "answers":{"is_urgent":{"type":"noul","noul":0.96}},
     "usage":{"input_tokens":290,"output_tokens":23,"cost":0.00001218},
     "id":"gen-dec-1790216301-4o4n3l629JUgad5Ss2wM","provider":"TypeSafe"}

This adapter is the ONLY place that shape is known -- nothing outside this
module depends on ``answers``/``noul``, so a change there breaks one module
instead of the ecosystem (Section 4: never couple the architecture to one
provider's exact response shape).

NO HARDCODED ANYTHING (Sections 4, 37): endpoint, model and key all come from
the registry / DPAPI. ``provider_id`` is the only constant -- an identity.

SEMANTICS: System One answers calibrated questions about a ``state`` string, so
the decision is expressed as one question per probability slot. A missing or
unusable answer is treated as NO evidence -- never as a fabricated probability.
"""

from __future__ import annotations

import json
import math
from typing import Any

from nexus_scalp.ai_providers.contract import (
    AIProviderAction,
    DecisionEvidence,
    PositionDecisionRequest,
    PositionDecisionResponse,
    SlProposal,
    TpProposal,
)
from nexus_scalp.ai_providers.errors import ProviderError, ProviderErrorCategory
from nexus_scalp.ai_providers.registry import PROVIDER_TYPE_EXTERNAL
from nexus_scalp.ai_providers.templates import TEMPLATE_VERSION
from nexus_scalp.ai_providers.transport import TransportResult
from nexus_scalp.observability.logging import get_logger

from .base import BaseAIProviderAdapter

logger = get_logger("nexus_scalp.ai_providers.adapters.system_one")

__all__ = ["SystemOneAdapter"]

_Q_HOLD = "is_hold_favourable"
_Q_CLOSE = "is_close_favourable"
_Q_REDUCE = "is_reduce_advisable"
_Q_TP = "is_tp_adjustment_warranted"
_Q_SL = "is_sl_adjustment_warranted"
_Q_REGIME = "is_regime_changing"
_Q_DOWNSIDE = "downside_risk_dominant"
_Q_UPSIDE = "upside_potential_dominant"


def _finite(value: Any, default: float = 0.0) -> float:
    """Coerce to a finite float, else ``default`` -- never NaN, never raises."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _score(answers: dict[str, Any], question: str) -> float | None:
    """Read an answer's ``noul`` score in [0,1]; None when unusable.

    Never invents a number: an absent question, an unknown answer type, or a
    non-numeric/non-finite value all return None so the caller can treat them
    as "no evidence".
    """
    slot = answers.get(question)
    if not isinstance(slot, dict):
        return None
    if str(slot.get("type", "")).lower() != "noul":
        return None  # an answer type we do not understand = a schema change
    raw = slot.get("noul")
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return min(1.0, max(0.0, f))


def _snippet(payload: Any, limit: int = 400) -> str:
    """Short non-secret summary for diagnostics and the test centre."""
    try:
        return json.dumps(payload, default=str)[:limit]
    except Exception:
        return str(payload)[:limit]


class SystemOneAdapter(BaseAIProviderAdapter):
    """System One as a position-decision adviser."""

    provider_id = "system_one"
    provider_type = PROVIDER_TYPE_EXTERNAL
    display_name = "System One"
    capabilities = ("position_decision", "structured_questions")
    supports_usage = True

    # -- wire shape -------------------------------------------------------------
    def _build_wire_payload(
        self, request: PositionDecisionRequest, canonical_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Map the canonical payload onto ``state`` + ``questions``.

        ``state`` is DATA, not instruction: the template's system instructions
        bind the model to the response schema and forbid acting on market text
        (Section 39).
        """
        return {
            "model": self.model,
            "state": _state_string(canonical_payload),
            "questions": {
                _Q_HOLD: {"type": "noul", "instructions": "Is holding this position favourable?"},
                _Q_CLOSE: {
                    "type": "noul",
                    "instructions": "Is closing this position now favourable?",
                },
                _Q_REDUCE: {
                    "type": "noul",
                    "instructions": "Is reducing this position's size advisable?",
                },
                _Q_TP: {
                    "type": "noul",
                    "instructions": "Is the current take-profit worth adjusting?",
                },
                _Q_SL: {
                    "type": "noul",
                    "instructions": "Is the current stop-loss worth adjusting?",
                },
                _Q_REGIME: {"type": "noul", "instructions": "Is the market regime transitioning?"},
                _Q_DOWNSIDE: {"type": "noul", "instructions": "Does downside risk dominate here?"},
                _Q_UPSIDE: {"type": "noul", "instructions": "Does upside potential dominate here?"},
            },
        }

    def _provider_test_payload(self) -> dict[str, Any]:
        """A harmless fixed probe over SIMULATED TEST DATA (Sections 22, 56).

        Deliberately asks the REAL decision questions against a synthetic
        snapshot, so the test exercises the same normalization path a live
        decision takes -- "connection works" is not the same claim as "the
        provider can produce a contract-valid response", and only the second
        one is what activation requires (Section 65).
        """
        from nexus_scalp.ai_providers.sample import SIMULATED_SAMPLE_REQUEST
        from nexus_scalp.ai_providers.templates import build_position_decision_payload

        canonical = build_position_decision_payload(SIMULATED_SAMPLE_REQUEST)
        return {
            "model": self.model,
            "state": (
                "NSE TEST CENTER probe using SIMULATED TEST DATA (not a real "
                "position, no order). " + _state_string(canonical)
            ),
            "questions": {
                _Q_HOLD: {"type": "noul", "instructions": "Is holding this position favourable?"},
                _Q_CLOSE: {
                    "type": "noul",
                    "instructions": "Is closing this position now favourable?",
                },
                _Q_REDUCE: {
                    "type": "noul",
                    "instructions": "Is reducing this position's size advisable?",
                },
            },
        }

    # -- response normalization -------------------------------------------------
    def _normalize_response(
        self, request: PositionDecisionRequest, result: TransportResult
    ) -> PositionDecisionResponse:
        """Map System One's answers onto the canonical decision contract."""
        payload = result.payload
        if not isinstance(payload, dict):
            raise ProviderError(
                ProviderErrorCategory.MALFORMED_RESPONSE,
                "response is not a JSON object",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_snippet(payload),
            )
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            raise ProviderError(
                ProviderErrorCategory.SCHEMA_VIOLATION,
                "response has no 'answers' object",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_snippet(payload),
            )

        p_hold, p_close, p_reduce = (
            _score(answers, _Q_HOLD),
            _score(answers, _Q_CLOSE),
            _score(answers, _Q_REDUCE),
        )
        present = [p for p in (p_hold, p_close, p_reduce) if p is not None]
        if not present:
            raise ProviderError(
                ProviderErrorCategory.SCHEMA_VIOLATION,
                "no decision question was answered",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_snippet(payload),
            )
        total = sum(present)
        if total <= 0:
            raise ProviderError(
                ProviderErrorCategory.SCHEMA_VIOLATION,
                "decision answers sum to zero",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_snippet(payload),
            )
        # Normalize onto the simplex so the contract's sum-to-1 check holds.
        scale = 1.0 / total
        n_hold = (p_hold or 0.0) * scale
        n_close = (p_close or 0.0) * scale
        n_reduce = (p_reduce or 0.0) * scale

        probs = {
            AIProviderAction.HOLD: n_hold,
            AIProviderAction.CLOSE: n_close,
            AIProviderAction.REDUCE: n_reduce,
        }
        action = max(probs, key=probs.get)
        if max(probs.values()) < 0.20:
            action = AIProviderAction.NO_ACTION

        # Expectations, in the R units the policy formula consumes.
        exp_down = -abs(request.distance_to_sl) * (
            0.25 + 0.75 * _finite(_score(answers, _Q_DOWNSIDE), 0.5)
        )
        exp_up = abs(request.distance_to_tp) * (
            0.25 + 0.75 * _finite(_score(answers, _Q_UPSIDE), 0.5)
        )
        remaining = (n_hold * exp_up) - (n_reduce * 0.15)

        tp_score = _score(answers, _Q_TP)
        sl_score = _score(answers, _Q_SL)
        tp_price = _tp_candidate(request, tp_score)
        sl_price = _sl_candidate(request, sl_score)
        tp = TpProposal(
            recommendation="ADJUST" if tp_price is not None else "KEEP",
            candidate_price=tp_price,
            confidence=_finite(tp_score, 0.0),
        )
        sl = SlProposal(
            recommendation="TIGHTEN" if sl_price is not None else "KEEP",
            candidate_price=sl_price,
            risk_change=_sl_risk_change(request, sl_price),
            confidence=_finite(sl_score, 0.0),
        )
        # The canonical validator requires an ADJUST_* action to carry its
        # candidate, so promote a HOLD only when a candidate actually exists.
        if tp_price is not None and action in (AIProviderAction.HOLD, AIProviderAction.NO_ACTION):
            action = AIProviderAction.ADJUST_TP
        elif sl_price is not None and action in (AIProviderAction.HOLD, AIProviderAction.NO_ACTION):
            action = AIProviderAction.ADJUST_SL

        evidence = DecisionEvidence(
            action=action,
            confidence=max(probs.values()),
            p_hold=n_hold,
            p_close=n_close,
            p_reduce=n_reduce,
            expected_remaining_r=remaining,
            expected_downside_r=exp_down,
            expected_upside_r=exp_up,
            regime_change_probability=_finite(_score(answers, _Q_REGIME), 0.0),
            uncertainty=1.0 - max(probs.values()),
            rationale=(
                f"system_one answers: hold={n_hold:.3f} close={n_close:.3f} reduce={n_reduce:.3f}"
            ),
        )

        # Usage/cost (Section 36): reported when present, never invented.
        input_tokens = output_tokens = None
        cost = None
        usage = payload.get("usage")
        if isinstance(usage, dict):
            input_tokens = int(_finite(usage.get("input_tokens"), 0)) or None
            output_tokens = int(_finite(usage.get("output_tokens"), 0)) or None
            cost = _finite(usage.get("cost"), 0) or None
            self.config.cost_metadata = {
                **self.config.cost_metadata,
                "last_input_tokens": input_tokens,
                "last_output_tokens": output_tokens,
                "last_cost": cost,
                "currency": "USD",
            }

        return PositionDecisionResponse(
            schema_version=request.schema_version,
            provider=self.provider_id,
            model=str(payload.get("model") or self.model),
            decision=evidence,
            tp=tp,
            sl=sl,
            reason_codes=["SYSTEM_ONE"],
            warnings=[],
            provider_generation_id=str(payload.get("id") or ""),
            request_id=request.provider_request_id,
            latency_ms=result.latency_ms,
            template_version=TEMPLATE_VERSION,
            config_version=request.decision_context_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
            from_live_provider=True,
            raw_response_summary=_snippet(payload),
        )


def _state_string(c: dict[str, Any]) -> str:
    """Render the canonical payload as System One's ``state`` string.

    Data only: no credentials, no paths, no instruction-shaped text. The model
    is asked to *assess* this, never to act on it (Section 39).
    """
    pos, mkt, acc, risk = (
        c.get("position", {}),
        c.get("market", {}),
        c.get("account", {}),
        c.get("risk", {}),
    )
    return (
        f"POSITION_SNAPSHOT symbol={pos.get('symbol')} side={pos.get('side')} "
        f"entry={pos.get('entry_price')} current={pos.get('current_price')} "
        f"volume={pos.get('volume')} age_sec={pos.get('position_age_sec')} "
        f"unrealized_r={pos.get('unrealized_r')} tp={pos.get('current_tp')} "
        f"sl={pos.get('current_sl')} atr={mkt.get('atr')} spread={mkt.get('spread')} "
        f"trend={mkt.get('trend')} regime={mkt.get('regime')} "
        f"regime_conf={mkt.get('regime_confidence')} session={mkt.get('session')} "
        f"liquidity={mkt.get('liquidity_state')} equity={acc.get('equity')} "
        f"free_margin={acc.get('free_margin')} margin_level={acc.get('margin_level')} "
        f"risk={risk.get('current_risk')}/{risk.get('max_allowed_risk')} "
        f"dist_sl={risk.get('distance_to_sl')} dist_tp={risk.get('distance_to_tp')} "
        f"rr={risk.get('reward_to_risk')}"
    )


def _tp_candidate(request: PositionDecisionRequest, score: float | None) -> float | None:
    """A TP candidate in the trade direction, snapped to the tick grid."""
    if score is None or score < 0.5:
        return None
    is_buy = request.side == "BUY"
    tick = request.tick_size if request.tick_size > 0 else 0.01
    if request.current_tp and request.current_tp > 0:
        base = request.current_tp
    else:
        base = request.current_price + (request.atr * 2.0 if is_buy else -request.atr * 2.0)
    step = tick * max(1, int(request.atr / max(tick, 1e-9)))
    candidate = base - (step if is_buy else -step)
    return round(candidate / tick) * tick


def _sl_candidate(request: PositionDecisionRequest, score: float | None) -> float | None:
    """An SL candidate that can only TIGHTEN the stop (Section 17).

    Produced ONLY when it reduces the distance to the stop, so a candidate can
    never widen risk by construction; the risk engine re-checks it anyway.
    """
    if score is None or score < 0.5:
        return None
    if request.current_sl is None or request.current_sl <= 0:
        return None
    is_buy = request.side == "BUY"
    tick = request.tick_size if request.tick_size > 0 else 0.01
    step = tick * max(1, int(request.atr / max(tick, 1e-9)))
    candidate = request.current_sl + (step if is_buy else -step)
    if is_buy and candidate >= request.current_price:
        return None
    if not is_buy and candidate <= request.current_price:
        return None
    return round(candidate / tick) * tick


def _sl_risk_change(request: PositionDecisionRequest, candidate: float | None) -> float:
    """Signed dollar-risk delta of the SL candidate. <= 0 by construction."""
    if candidate is None or request.current_sl is None or request.current_sl <= 0:
        return 0.0
    is_buy = request.side == "BUY"
    moved = candidate - request.current_sl if is_buy else request.current_sl - candidate
    if moved <= 0:
        return 0.0  # stop widened or unchanged -- never report a risk saving
    return -abs(moved) * request.volume * 100.0
