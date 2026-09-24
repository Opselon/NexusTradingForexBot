"""Internal NSE ML adapter (ECOSYSTEM-001, Section 6).

The existing Layer-2 position decision adviser -- ``PositionAdviserService``,
the trained KEEP/CLOSE/REDUCE head -- exposed as ONE MORE PROVIDER behind the
same canonical interface. Nothing is reimplemented, retrained or duplicated
(Section 44: reuse the snapshot, the feature contract, the policy, the risk
gate; only the *evidence provider* varies).

WHY A FIRST-CLASS ADAPTER
------------------------
The ecosystem's modes (INTERNAL_ONLY / HYBRID / SHADOW / COMPARISON) all need
internal evidence to sit in the same normalized shape as external evidence so
fusion is a like-for-like computation, not an apples-to-orchids average. A
shadow comparison between an external model and the internal model is only
meaningful if both are normalized the same way.

AUTHORITY
---------
This adapter ADVISES, exactly as the adviser always has. The existing invariant
holds unchanged: it can never open or size a position, never raise a hold score,
never extend a position's life, never weaken a protection verdict. Its output is
evidence for the deterministic policy layer, never an instruction.
"""

from __future__ import annotations

import time
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
from nexus_scalp.ai_providers.registry import PROVIDER_TYPE_INTERNAL, ProviderConfig, ProviderRegistryStore
from nexus_scalp.ai_providers.templates import TEMPLATE_VERSION
from nexus_scalp.ai_providers.transport import TransportResult
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.position_adviser.models import ADVISER_ACTIONS
from nexus_scalp.settings.secret_store import SecureSecretStore

from .base import BaseAIProviderAdapter

logger = get_logger("nexus_scalp.ai_providers.adapters.internal_ml")

__all__ = ["InternalNSEMLAdapter", "PROBABILITY_SLOT_BY_ADVISER_ACTION"]

#: The adviser's own action vocabulary -> canonical probability slot. The
#: adviser head is trained over exactly ADVISER_ACTIONS (KEEP/CLOSE/REDUCE),
#: so the canonical action is the adviser action verbatim except that KEEP maps
#: to HOLD to share one vocabulary across all providers.
_ADVISER_TO_CANONICAL: dict[str, AIProviderAction] = {
    "KEEP": AIProviderAction.HOLD,
    "CLOSE": AIProviderAction.CLOSE,
    "REDUCE": AIProviderAction.REDUCE,
}

#: The three probability slots the internal head produces, in class order.
PROBABILITY_SLOT_BY_ADVISER_ACTION: dict[str, str] = {
    "KEEP": "p_hold",
    "CLOSE": "p_close",
    "REDUCE": "p_reduce",
}


class InternalNSEMLAdapter(BaseAIProviderAdapter):
    """The existing NSE position adviser, as one more canonical provider."""

    provider_id = "internal_nse_ml"
    provider_type = PROVIDER_TYPE_INTERNAL
    display_name = "Internal NSE ML"
    capabilities = ("position_decision",)
    supports_model_listing = False
    supports_usage = False

    def __init__(
        self,
        *,
        config: ProviderConfig,
        registry: ProviderRegistryStore,
        secret_store: SecureSecretStore,
        adviser_service: Any | None = None,
    ) -> None:
        super().__init__(config=config, registry=registry, secret_store=secret_store)
        #: ``PositionAdviserService`` (or a test double). Resolved lazily so the
        #: adapter can be constructed before the engine installs the adviser.
        self._adviser_service = adviser_service
        self._last_raw: dict[str, Any] = {}

    # -- adviser resolution -----------------------------------------------------
    def _adviser(self) -> Any:
        """Resolve the live PositionAdviserService.

        Late-bound: the engine installs the adviser after this adapter is built.
        Unavailable or disabled -> ProviderError, so the fallback chain engages
        instead of a silent skip.
        """
        svc = self._adviser_service
        if svc is None:
            try:
                from nexus_scalp.web.position_adviser_routes import get_position_adviser_service

                svc = get_position_adviser_service()
                self._adviser_service = svc
            except Exception as exc:  # noqa: BLE001 - fail closed
                raise ProviderError(
                    ProviderErrorCategory.UPSTREAM_UNAVAILABLE,
                    "internal adviser service is unavailable",
                    provider_id=self.provider_id,
                ) from exc
        if not getattr(svc, "enabled", False):
            raise ProviderError(
                ProviderErrorCategory.UPSTREAM_UNAVAILABLE,
                "internal adviser is disabled",
                provider_id=self.provider_id,
            )
        return svc

    def _build_wire_payload(
        self, request: PositionDecisionRequest, canonical_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """The internal adviser consumes a live position state dict, not JSON.

        Returned here only so the base class's shared path stays unchanged; the
        adapter does not make a network call.
        """
        return _request_to_adviser_state(request)

    def _provider_test_payload(self) -> dict[str, Any]:
        """No network probe: the internal model is tested via its own routes."""
        return {"mode": "internal", "adviser": "PositionAdviserService"}

    # -- the decision call ------------------------------------------------------
    def evaluate_position(self, request: PositionDecisionRequest) -> PositionDecisionResponse:
        """Ask the existing adviser, then normalize onto the canonical contract.

        Never a network call: this is a local inference on an already-loaded
        head, so there is no retry and no breaker to apply.
        """
        started = time.monotonic()
        state = _request_to_adviser_state(request)
        try:
            advisory = self._adviser().evaluate(request.ticket, state)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - the adviser's own contract is
            # fail-closed; anything escaping it is a bug, not a verdict.
            raise ProviderError(
                ProviderErrorCategory.UPSTREAM_UNAVAILABLE,
                f"internal adviser evaluation failed: {exc.__class__.__name__}",
                provider_id=self.provider_id,
            ) from exc
        if advisory is None:
            raise ProviderError(
                ProviderErrorCategory.UPSTREAM_UNAVAILABLE,
                "internal adviser returned no verdict (disabled or refused)",
                provider_id=self.provider_id,
            )

        # Normalize the adviser's output onto the canonical evidence shape.
        action_name = str(advisory.action).upper()
        probs = dict(advisory.probabilities or {})
        # The adviser's probabilities are keyed by ITS action names.
        p_hold = float(probs.get("KEEP", probs.get("HOLD", 0.0)))
        p_close = float(probs.get("CLOSE", 0.0))
        p_reduce = float(probs.get("REDUCE", 0.0))
        total = p_hold + p_close + p_reduce
        if total <= 0:
            raise ProviderError(
                ProviderErrorCategory.SCHEMA_VIOLATION,
                "internal adviser probabilities sum to zero",
                provider_id=self.provider_id,
            )
        scale = 1.0 / total
        p_hold, p_close, p_reduce = p_hold * scale, p_close * scale, p_reduce * scale

        canonical_action = _ADVISER_TO_CANONICAL.get(action_name, AIProviderAction.NO_ACTION)
        confidence = float(advisory.confidence)

        # R-space expectations from the request's own causal distances.
        exp_up = max(0.0, p_hold) * abs(request.distance_to_tp)
        exp_down = -max(0.0, p_close) * abs(request.distance_to_sl)
        evidence = DecisionEvidence(
            action=canonical_action,  # internal ML has no TP/SL head
            confidence=min(1.0, max(0.0, confidence)),
            p_hold=p_hold,
            p_close=p_close,
            p_reduce=p_reduce,
            expected_remaining_r=(p_hold * exp_up) + (p_reduce * -0.15),
            expected_downside_r=exp_down,
            expected_upside_r=exp_up,
            regime_change_probability=0.0,
            uncertainty=max(0.0, 1.0 - confidence),
            rationale=f"internal_nse_ml adviser action={action_name} confidence={confidence:.3f}",
        )
        tp = TpProposal(recommendation="POLICY_OWNED")
        sl = SlProposal(recommendation="POLICY_OWNED")
        return PositionDecisionResponse(
            schema_version=request.schema_version,
            provider=self.provider_id,
            model=str(advisory.model_id or "internal_nse_ml"),
            decision=evidence,
            tp=tp,
            sl=sl,
            reason_codes=[f"INTERNAL_{action_name}"],
            warnings=[],
            provider_generation_id=str(advisory.advisory_id or ""),
            request_id=request.provider_request_id,
            latency_ms=(time.monotonic() - started) * 1000.0,
            template_version=TEMPLATE_VERSION,
            config_version=request.decision_context_version,
            input_tokens=None,
            output_tokens=None,
            cost_estimate=None,
            from_live_provider=False,
            raw_response_summary=str(advisory.to_dict())[:400],
        )


def _request_to_adviser_state(request: PositionDecisionRequest) -> dict[str, Any]:
    """Map the canonical request onto the adviser's own feature vocabulary.

    The adviser consumes exactly the 12 causal features the existing integration
    builds (``position_adviser/integration.py``), so an internal and an external
    provider evaluate the SAME causal snapshot -- which is what makes a
    comparison or a shadow run meaningful.
    """
    return {
        "unrealized_pnl_r": request.unrealized_r,
        "current_r_net": request.unrealized_r,
        "current_return": (
            request.current_price - request.entry_price
        ) / max(request.entry_price, 1e-9) * (1 if request.side == "BUY" else -1),
        "distance_to_stop_r": request.distance_to_sl,
        "distance_to_target_r": request.distance_to_tp,
        "position_age_bars": max(0, int(request.position_age_sec / 60.0)),
        "atr": request.atr,
        "spread": request.spread,
        "estimated_slippage": 0.05,
        "model_probability": request.model_confidence,
        "model_confidence": request.model_confidence,
        "signal_age": min(request.position_age_sec, 3600.0),
    }
