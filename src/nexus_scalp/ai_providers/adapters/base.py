"""Provider-agnostic adapter interface (ECOSYSTEM-001, Section 2).

THE CONTRACT
    AIProvider
      provider_id / provider_type / display_name / enabled / capabilities
      endpoint / models / authentication / timeout / retry_policy
      health() / list_models() / test() / evaluate_position()

Position Manager knows PROVIDER -- not SYSTEMONE HTTP DETAILS, not OPENROUTER
HTTP DETAILS, not what a chat completion is. A future provider is an additive
subclass of this base, not a change to the decision pipeline.

WHAT AN ADAPTER OWNS
    1. Resolving its DPAPI secret at call time (never held in a serializable
       field, so a key can never reach a log, a diagnostic, or the UI).
    2. Mapping the canonical request onto its own wire shape.
    3. Mapping its own response back onto the canonical response contract --
       and only onto that. No partial or invented contract.
    4. Raising ProviderError on failure.

WHAT AN ADAPTER MAY NEVER DO (Section 1)
    Place or modify an order, increase size, widen an SL beyond policy, bypass
    a risk limit, bypass broker constraints, override a deterministic safety
    gate, invent account values, invent market data, fabricate a position state,
    or claim an execution occurred. An adapter returning anything other than an
    advisory contract has broken the invariant this ecosystem exists to enforce.
"""

from __future__ import annotations

import abc
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.ai_providers.contract import (
    AIProviderAction,
    PositionDecisionRequest,
    PositionDecisionResponse,
)
from nexus_scalp.ai_providers.errors import ProviderError, ProviderErrorCategory
from nexus_scalp.ai_providers.registry import (
    PROVIDER_TYPE_EXTERNAL,
    ProviderConfig,
    ProviderRegistryStore,
)
from nexus_scalp.ai_providers.templates import build_position_decision_payload
from nexus_scalp.ai_providers.transport import CircuitBreaker, TransportResult, retrying_json_call
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.settings.secret_store import SecureSecretStore

logger = get_logger("nexus_scalp.ai_providers.adapters")

__all__ = ["AIProviderHealth", "AIProviderTestResult", "BaseAIProviderAdapter"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _perf_start() -> float:
    return time.monotonic()


def _perf_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000.0


def _summarize(payload: Any, limit: int = 400) -> str:
    """Compact, non-secret view of a raw response for the test centre."""
    try:
        import json as _json

        return _json.dumps(payload, default=str)[:limit]
    except Exception:
        return str(payload)[:limit]


@dataclass
class AIProviderHealth:
    """A provider's live health (Section 31)."""

    available: bool = False
    authenticated: bool = False
    model_available: bool = False
    latency_ms: float | None = None
    last_success: str | None = None
    last_failure: str | None = None
    consecutive_failures: int = 0
    rate_limited: bool = False
    quota_state: str = "UNKNOWN"
    circuit_breaker_state: str = "CLOSED"
    last_error: str = ""
    checked_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "authenticated": self.authenticated,
            "model_available": self.model_available,
            "latency_ms": self.latency_ms,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "consecutive_failures": self.consecutive_failures,
            "rate_limited": self.rate_limited,
            "quota_state": self.quota_state,
            "circuit_breaker_state": self.circuit_breaker_state,
            "last_error": self.last_error,
            "checked_at": self.checked_at,
        }


@dataclass
class AIProviderTestResult:
    """Outcome of a provider test (Section 22)."""

    passed: bool
    detail: str
    stage: str = "connection"
    latency_ms: float | None = None
    raw_response: str = ""
    normalized_response: Any = None
    error: Any = ""


class BaseAIProviderAdapter(abc.ABC):
    """One provider's adapter. Subclass per provider; nothing else changes.

    Shared machinery lives here (retry, breaking, health, latency, secrets).
    Subclasses implement exactly four hooks:
    ``_build_wire_payload``, ``_normalize_response``,
    ``_provider_test_payload`` and (optionally) ``_list_models_impl``.
    """

    provider_id: str = ""
    provider_type: str = PROVIDER_TYPE_EXTERNAL
    display_name: str = ""
    capabilities: tuple[str, ...] = ("position_decision",)
    supports_model_listing: bool = False
    supports_usage: bool = False

    def __init__(
        self,
        *,
        config: ProviderConfig,
        registry: ProviderRegistryStore,
        secret_store: SecureSecretStore,
    ) -> None:
        self.config = config
        self._registry = registry
        self._secret_store = secret_store
        self._lock = threading.RLock()
        self._health = AIProviderHealth()
        self._breaker = CircuitBreaker(
            failure_threshold=max(2, config.max_retries + 2), cooldown_sec=60.0
        )

    # -- identity --------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    @property
    def model(self) -> str:
        return self.config.default_model

    def to_public_dict(self) -> dict[str, Any]:
        """Identity + health for the UI provider table (Section 19)."""
        h = self._health.to_dict()
        return {
            "provider_id": self.provider_id,
            "provider_name": self.config.provider_name or self.display_name,
            "type": self.provider_type,
            "display_name": self.display_name,
            "endpoint": self.config.endpoint,
            "model": self.model,
            "enabled": self.enabled,
            "capabilities": list(self.capabilities),
            "timeout": self.config.timeout,
            "max_retries": self.config.max_retries,
            "supports_model_listing": self.supports_model_listing,
            "supports_usage": self.supports_usage,
            "health": h,
            "rate_limit_state": "RATE_LIMITED" if h["rate_limited"] else "OK",
            "last_error": h["last_error"],
            "cost_metadata": dict(self.config.cost_metadata),
            "circuit_breaker_state": h["circuit_breaker_state"],
            "has_secret": bool(self.config.secret_name),
        }

    # -- secrets (Section 37) --------------------------------------------------
    def _resolve_secret(self) -> str:
        """Resolve the DPAPI secret at call time. Never held on ``self``."""
        name = self.config.secret_name or f"ai_provider_{self.provider_id}_key"
        try:
            value = self._secret_store.get_secret(name)
        except Exception as exc:
            raise ProviderError(
                ProviderErrorCategory.AUTH_FAILED,
                "secret store unavailable",
                provider_id=self.provider_id,
            ) from exc
        if not value:
            raise ProviderError(
                ProviderErrorCategory.AUTH_FAILED,
                "no API key configured for this provider",
                provider_id=self.provider_id,
            )
        return value

    def _build_auth_headers(self, headers: dict[str, str], key: str) -> None:
        headers["Authorization"] = f"Bearer {key}"

    # -- health (Section 31) ---------------------------------------------------
    def health(self) -> AIProviderHealth:
        return self._health

    def _update_health(
        self, *, ok: bool, latency_ms: float | None = None, error: ProviderError | None = None
    ) -> None:
        """Feed BOTH directions (Section: a breaker that only counts errors
        can never recover from a transient fault)."""
        with self._lock:
            now = _now()
            h = self._health
            if ok:
                h.available = True
                h.authenticated = True
                h.model_available = True
                h.last_success = now
                h.consecutive_failures = 0
                h.rate_limited = False
                h.last_error = ""
                h.latency_ms = latency_ms
                self._breaker.record_success()
            else:
                h.available = False
                h.last_failure = now
                h.consecutive_failures += 1
                if error is not None:
                    h.last_error = f"{error.category.value}: {error}"
                    h.rate_limited = error.category is ProviderErrorCategory.RATE_LIMITED
                self._breaker.record_failure()
            h.circuit_breaker_state = self._breaker.state
            h.checked_at = now

    # -- round trip ------------------------------------------------------------
    def _round_trip(self, json_body: Any) -> TransportResult:
        """Authenticated call with retry + breaking (Sections 31/32/35)."""
        key = self._resolve_secret()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        self._build_auth_headers(headers, key)
        result = retrying_json_call(
            url=self.config.endpoint,
            json_body=json_body,
            headers=headers,
            timeout=self.config.timeout,
            max_retries=self.config.max_retries,
            breaker=self._breaker,
            provider_id=self.provider_id,
            secrets=(key,),
        )
        self._update_health(ok=True, latency_ms=result.latency_ms)
        return result

    # -- normalization boundary (Sections 10, 40) ------------------------------
    @staticmethod
    def classify_payload(payload: Any) -> ProviderErrorCategory | None:
        """Classify a raw provider payload before it becomes a contract object.

        Runs ahead of :class:`PositionDecisionResponse` construction, so an
        incomplete or impossible proposal is rejected HERE and surfaces as
        ``SCHEMA_VIOLATION`` -- the category the fallback chain keys off. The
        contract's own model validator is non-deferrable (it runs at
        construction), so without this check an adapter would raise an opaque
        ``pydantic.ValidationError`` that the orchestrator would bucket as
        ``UNKNOWN``, hiding the real cause.

        Returns ``None`` when the payload is structurally acceptable.
        """
        if not isinstance(payload, dict):
            return ProviderErrorCategory.MALFORMED_RESPONSE

        decision = payload.get("decision")
        if not isinstance(decision, dict):
            return ProviderErrorCategory.SCHEMA_VIOLATION

        action = decision.get("action")
        valid_actions = {a.value for a in AIProviderAction}
        if action not in valid_actions:
            return ProviderErrorCategory.SCHEMA_VIOLATION

        for name in ("confidence", "p_hold", "p_close", "p_reduce", "uncertainty"):
            raw = decision.get(name, 0.0)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return ProviderErrorCategory.SCHEMA_VIOLATION
            if not math.isfinite(float(raw)) or not 0.0 <= float(raw) <= 1.0:
                return ProviderErrorCategory.SCHEMA_VIOLATION

        # An ADJUST_* action must carry the proposal it is about: an
        # ADJUST_SL with no candidate price is not a weaker SL opinion, it is a
        # non-answer, and must not be scored as one (Section 10).
        tp = payload.get("tp") or {}
        sl = payload.get("sl") or {}
        if action == AIProviderAction.ADJUST_SL.value:
            candidate = sl.get("candidate_price")
            if (
                candidate is None
                or not isinstance(candidate, (int, float))
                or not math.isfinite(float(candidate))
            ):
                return ProviderErrorCategory.SCHEMA_VIOLATION
        if action == AIProviderAction.ADJUST_TP.value:
            candidate = tp.get("candidate_price")
            if (
                candidate is None
                or not isinstance(candidate, (int, float))
                or not math.isfinite(float(candidate))
            ):
                return ProviderErrorCategory.SCHEMA_VIOLATION

        return None

    # -- the canonical decision call (Sections 7/10) ---------------------------
    def evaluate_position(self, request: PositionDecisionRequest) -> PositionDecisionResponse:
        """Advise on one open position. Returns the canonical contract.

        The ONLY method the orchestrator calls. It executes nothing; its output
        is evidence for the deterministic policy, never an instruction.
        """
        if not self.enabled:
            raise ProviderError(
                ProviderErrorCategory.UNKNOWN,
                "provider is disabled",
                provider_id=self.provider_id,
            )
        started = _perf_start()
        canonical = build_position_decision_payload(request)
        wire = self._build_wire_payload(request, canonical)
        result = self._round_trip(wire)
        response = self._normalize_response(request, result)
        response.latency_ms = _perf_ms(started)
        self._update_health(ok=True, latency_ms=response.latency_ms)
        return response

    # -- model listing (Section 21) --------------------------------------------
    def list_models(self) -> list[str]:
        if not self.supports_model_listing:
            return []
        try:
            return self._list_models_impl()
        except ProviderError as exc:
            logger.warning(
                "[AI-PROV] %s model listing failed: %s", self.provider_id, exc.category.value
            )
            return []

    def _list_models_impl(self) -> list[str]:
        return []

    # -- health probe (Sections 22/65) -----------------------------------------
    def test(self) -> AIProviderTestResult:
        """Connection + auth + model + structured-output test.

        Uses a FIXED harmless payload, never a live position, so a test can
        never trigger a real trade (Section 22).
        """
        try:
            key = self._resolve_secret()
        except ProviderError as exc:
            return AIProviderTestResult(
                False, "no API key configured", stage="authentication", error=str(exc)
            )

        started = _perf_start()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        self._build_auth_headers(headers, key)
        try:
            result = retrying_json_call(
                url=self.config.endpoint,
                json_body=self._provider_test_payload(),
                headers=headers,
                timeout=self.config.timeout,
                max_retries=0,
                breaker=self._breaker,
                provider_id=self.provider_id,
                secrets=(key,),
            )
        except ProviderError as exc:
            self._update_health(ok=False, error=exc)
            return AIProviderTestResult(False, str(exc), stage="request", error=exc.to_dict())

        latency = _perf_ms(started)
        try:
            response = self._normalize_response(self._sample_request(), result)
        except (ValueError, ProviderError) as exc:
            self._update_health(ok=False)
            return AIProviderTestResult(
                False,
                f"connection OK but structured output invalid: {exc}",
                stage="structured_output",
                latency_ms=latency,
                raw_response=_summarize(result.payload),
                error=str(exc),
            )
        self._update_health(ok=True, latency_ms=latency)
        return AIProviderTestResult(
            True,
            "connection, authentication, model and structured output all valid",
            stage="complete",
            latency_ms=latency,
            raw_response=_summarize(result.payload),
            normalized_response=response.model_dump(mode="json"),
        )

    # -- provider-specific hooks ------------------------------------------------
    @abc.abstractmethod
    def _build_wire_payload(
        self, request: PositionDecisionRequest, canonical_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Map the canonical payload onto THIS provider's request shape."""

    @abc.abstractmethod
    def _normalize_response(
        self, request: PositionDecisionRequest, result: TransportResult
    ) -> PositionDecisionResponse:
        """Map THIS provider's response onto the canonical contract."""

    @abc.abstractmethod
    def _provider_test_payload(self) -> dict[str, Any]:
        """A harmless, fixed test request (no live position)."""

    def _sample_request(self) -> PositionDecisionRequest:
        """A clearly-marked SIMULATED TEST snapshot (Section 56)."""
        from nexus_scalp.ai_providers.sample import SIMULATED_SAMPLE_REQUEST

        return SIMULATED_SAMPLE_REQUEST
