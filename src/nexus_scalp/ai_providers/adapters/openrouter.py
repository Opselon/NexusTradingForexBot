"""OpenRouter adapter (ECOSYSTEM-001, Section 5).

OpenRouter is an OpenAI-compatible chat-completions router. The adapter speaks
its own wire shape and maps the response back onto the canonical contract -- no
other module in the ecosystem knows what a chat completion is.

FAILURE NORMALIZATION (Section 5)
    200 -> normalize; 400/422 INVALID_REQUEST; 401/403 AUTH_FAILED; 404
    MODEL_UNAVAILABLE; 408 TIMEOUT; 409 INVALID_REQUEST; 429 RATE_LIMITED;
    500/502/503/504 UPSTREAM_UNAVAILABLE; non-JSON MALFORMED_RESPONSE.
    Categories are produced by the shared transport; this adapter adds the
    OpenRouter-specific reading of the error body (upstream code/message) so an
    operator sees the router's own diagnosis instead of a bare HTTP code.

    Retries follow the taxonomy, not a blanket loop: an invalid key, an unknown
    model or a malformed request is never retried as though it were a network
    blip (Section 32).

NO HARDCODED ANYTHING: endpoint, model and key come from the registry / DPAPI.
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
from nexus_scalp.ai_providers.templates import TEMPLATE_VERSION, system_instructions
from nexus_scalp.ai_providers.transport import TransportResult
from nexus_scalp.observability.logging import get_logger

from .base import BaseAIProviderAdapter

logger = get_logger("nexus_scalp.ai_providers.adapters.openrouter")

__all__ = ["OpenRouterAdapter"]


def _finite(value: Any, default: float = 0.0) -> float:
    """Coerce to a finite float, else ``default`` -- never NaN, never raises."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _summarize(payload: Any, limit: int = 400) -> str:
    try:
        return json.dumps(payload, default=str)[:limit]
    except Exception:  # noqa: BLE001
        return str(payload)[:limit]


class OpenRouterAdapter(BaseAIProviderAdapter):
    """OpenRouter as a position-decision adviser."""

    provider_id = "openrouter"
    provider_type = PROVIDER_TYPE_EXTERNAL
    display_name = "OpenRouter"
    capabilities = ("position_decision", "chat_completions")
    supports_model_listing = True
    supports_usage = True

    #: Sub-path appended to the configured endpoint. The endpoint in the
    #: registry is the router base (e.g. https://openrouter.ai/api/v1), never a
    #: hardcoded URL.
    _CHAT_PATH = "/chat/completions"
    _MODELS_PATH = "/models"

    # -- endpoint resolution ----------------------------------------------------
    def _endpoint(self, path: str = _CHAT_PATH) -> str:
        base = (self.config.endpoint or "").rstrip("/")
        if not base:
            raise ProviderError(
                ProviderErrorCategory.INVALID_REQUEST,
                "no endpoint configured for this provider",
                provider_id=self.provider_id,
            )
        return base + path

    # -- wire shape -------------------------------------------------------------
    def _build_wire_payload(
        self, request: PositionDecisionRequest, canonical_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """OpenAI-style chat completion over the canonical payload.

        ``response_format`` pins the model to JSON so the parse is deterministic;
        the instructions themselves are what keep it from acting on market text
        (Section 39).
        """
        prompt = _render_prompt(canonical_payload)
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instructions()},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

    def _provider_test_payload(self) -> dict[str, Any]:
        """A harmless probe over SIMULATED TEST DATA (Sections 22, 56).

        Asks for the REAL decision contract against a synthetic snapshot, so the
        test proves the provider can produce a contract-valid response rather
        than merely that HTTP 200 came back (Section 65 activation gate).
        """
        from nexus_scalp.ai_providers.sample import SIMULATED_SAMPLE_REQUEST
        from nexus_scalp.ai_providers.templates import build_position_decision_payload

        canonical = build_position_decision_payload(SIMULATED_SAMPLE_REQUEST)
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instructions()},
                {
                    "role": "user",
                    "content": (
                        "This is an NSE TEST CENTER probe using SIMULATED TEST "
                        "DATA (not a real position, no order). Assess it and "
                        "reply with the JSON object the system instructions "
                        f"describe.\n{__import__('json').dumps(canonical, default=str)}"
                    ),
                },
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

    # -- model listing (Section 21) --------------------------------------------
    def _list_models_impl(self) -> list[str]:
        from nexus_scalp.ai_providers.transport import retrying_json_call

        key = self._resolve_secret()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        self._build_auth_headers(headers, key)
        result = retrying_json_call(
            url=self._endpoint(self._MODELS_PATH),
            json_body=None,
            headers=headers,
            timeout=self.config.timeout,
            max_retries=1,
            breaker=self._breaker,
            provider_id=self.provider_id,
            secrets=(key,),
        )
        payload = result.payload
        if not isinstance(payload, dict):
            return []
        models = []
        for item in payload.get("data", []):
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        return models

    # -- response normalization -------------------------------------------------
    def _normalize_response(
        self, request: PositionDecisionRequest, result: TransportResult
    ) -> PositionDecisionResponse:
        """Map an OpenAI-style chat completion onto the canonical contract."""
        payload = result.payload
        if not isinstance(payload, dict):
            raise ProviderError(
                ProviderErrorCategory.MALFORMED_RESPONSE,
                "response is not a JSON object",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_summarize(payload),
            )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                ProviderErrorCategory.SCHEMA_VIOLATION,
                "response has no 'choices'",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_summarize(payload),
            )
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(
                ProviderErrorCategory.SCHEMA_VIOLATION,
                "response choice has no content",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_summarize(payload),
            )
        # The model was pinned to JSON; an unparseable body is a schema break.
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                ProviderErrorCategory.MALFORMED_RESPONSE,
                "model content is not valid JSON",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_summarize(content),
            ) from exc

        # -- decision block ----------------------------------------------------
        decision = parsed.get("decision") if isinstance(parsed, dict) else None
        if not isinstance(decision, dict):
            raise ProviderError(
                ProviderErrorCategory.SCHEMA_VIOLATION,
                "model JSON has no 'decision' object",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_summarize(parsed),
            )
        action_raw = decision.get("action")
        try:
            action = AIProviderAction(action_raw)
        except ValueError as exc:
            raise ProviderError(
                ProviderErrorCategory.SCHEMA_VIOLATION,
                f"model returned an unsupported action: {action_raw!r}",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_summarize(parsed),
            ) from exc

        p_hold = _finite(decision.get("p_hold"), 0.0)
        p_close = _finite(decision.get("p_close"), 0.0)
        p_reduce = _finite(decision.get("p_reduce"), 0.0)
        # The contract requires the simplex; normalize here rather than reject,
        # because a model can emit 0.8/0.3/0.3 without being "broken" -- the
        # canonical validator still catches a genuine contradiction.
        total = p_hold + p_close + p_reduce
        if total <= 0:
            raise ProviderError(
                ProviderErrorCategory.SCHEMA_VIOLATION,
                "model probabilities sum to zero",
                provider_id=self.provider_id,
                status_code=result.status_code,
                body_snippet=_summarize(parsed),
            )
        scale = 1.0 / total
        p_hold, p_close, p_reduce = p_hold * scale, p_close * scale, p_reduce * scale

        tp_block = parsed.get("tp") if isinstance(parsed, dict) else None
        sl_block = parsed.get("sl") if isinstance(parsed, dict) else None
        tp_price = _maybe_price(tp_block, "candidate_price") if isinstance(tp_block, dict) else None
        sl_price = _maybe_price(sl_block, "candidate_price") if isinstance(sl_block, dict) else None
        tp = TpProposal(
            recommendation=str((tp_block or {}).get("recommendation") or "KEEP"),
            candidate_price=tp_price,
            confidence=_finite((tp_block or {}).get("confidence"), 0.0),
        )
        sl = SlProposal(
            recommendation=str((sl_block or {}).get("recommendation") or "KEEP"),
            candidate_price=sl_price,
            risk_change=_finite((sl_block or {}).get("risk_change"), 0.0),
            confidence=_finite((sl_block or {}).get("confidence"), 0.0),
        )

        evidence = DecisionEvidence(
            action=action,
            confidence=_finite(decision.get("confidence"), 0.0),
            p_hold=p_hold,
            p_close=p_close,
            p_reduce=p_reduce,
            expected_remaining_r=_finite(decision.get("expected_remaining_r"), 0.0),
            expected_downside_r=_finite(decision.get("expected_downside_r"), 0.0),
            expected_upside_r=_finite(decision.get("expected_upside_r"), 0.0),
            regime_change_probability=_finite(decision.get("regime_change_probability"), 0.0),
            uncertainty=_finite(
                decision.get("uncertainty"),
                max(0.0, 1.0 - _finite(decision.get("confidence"), 0.0)),
            ),
            rationale=str(decision.get("rationale") or "")[:4000],
        )

        # Usage/cost (Section 36): reported when present, never invented.
        raw_usage = payload.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else None
        input_tokens = output_tokens = None
        if usage:
            input_tokens = int(_finite(usage.get("prompt_tokens", usage.get("input_tokens")), 0)) or None
            output_tokens = int(_finite(usage.get("completion_tokens", usage.get("output_tokens")), 0)) or None
            self.config.cost_metadata = {
                **self.config.cost_metadata,
                "last_input_tokens": input_tokens,
                "last_output_tokens": output_tokens,
                "last_cost": None,
                "currency": "USD",
            }
        return PositionDecisionResponse(
            schema_version=request.schema_version,
            provider=self.provider_id,
            model=str(payload.get("model") or self.model),
            decision=evidence,
            tp=tp,
            sl=sl,
            reason_codes=[],
            warnings=[],
            provider_generation_id=str(payload.get("id") or ""),
            request_id=request.provider_request_id,
            latency_ms=result.latency_ms,
            template_version=TEMPLATE_VERSION,
            config_version=request.decision_context_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=None,
            from_live_provider=True,
            raw_response_summary=_summarize(payload),
        )


def _maybe_price(block: dict[str, Any], key: str) -> float | None:
    """A candidate price must be a positive finite number, else None."""
    raw = block.get(key)
    if raw is None:
        return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0.0 else None


def _render_prompt(c: dict[str, Any]) -> str:
    """Render the canonical payload as the user message.

    Data only: no credentials, no paths. The model assesses it; the schema and
    the policy layer decide (Sections 38/39).
    """
    return (
        f"Assess this open position and reply with the JSON object described in "
        f"the system instructions (decision/tp/sl with probabilities summing to 1).\n"
        f"{json.dumps(c, default=str)}"
    )
