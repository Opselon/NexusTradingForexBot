"""Shared HTTP transport for external AI providers (Sections 31, 32, 35).

WHY A SHARED TRANSPORT
----------------------
Both external providers (System One, OpenRouter) need the same machinery: a
bounded timeout, exponential backoff with jitter, ``Retry-After`` honouring,
circuit breaking, and latency measurement. Implementing it twice would
duplicate business logic across adapters, which Section 2 forbids -- the
adapter maps a provider's semantics onto a canonical behaviour, it does not
re-invent transport.

WHAT THIS DOES *NOT* DO
-----------------------
It knows nothing about the canonical decision contract. It moves JSON. An
adapter is what turns a response into a
:class:`~nexus_scalp.ai_providers.contract.PositionDecisionResponse`, so a
provider-specific shape can never leak into the transport layer.

CIRCUIT BREAKER (Section 31)
---------------------------
Counts consecutive failures; after ``failure_threshold`` it OPENS and refuses
requests for ``cooldown_sec`` instead of hammering a provider that is already
failing (preventing request storms). A success resets it. This is the same
hot-path circuit discipline the engine already uses, applied to egress.

NO SECRET IN A LOG, EVER
------------------------
``request_headers`` is passed as a dict whose Authorization value the CALLER
assembles; this module never logs it, never includes it in an error, and the
``ProviderError.body_snippet`` it builds is passed through
:func:`~nexus_scalp.ai_providers.errors.redact` first (Section 37).
"""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from nexus_scalp.ai_providers.errors import (
    ProviderError,
    ProviderErrorCategory,
    redact,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.ai_providers.transport")

__all__ = ["CircuitBreaker", "TransportResult", "retrying_json_call"]

#: HTTP status -> failure category. 400/401/403/404/422 are permanent;
#: 429 and 5xx are transient. Anything else is UNKNOWN (treated retryable-once).
_STATUS_MAP: dict[int, ProviderErrorCategory] = {
    400: ProviderErrorCategory.INVALID_REQUEST,
    401: ProviderErrorCategory.AUTH_FAILED,
    403: ProviderErrorCategory.AUTH_FAILED,
    404: ProviderErrorCategory.MODEL_UNAVAILABLE,
    408: ProviderErrorCategory.TIMEOUT,
    409: ProviderErrorCategory.INVALID_REQUEST,
    422: ProviderErrorCategory.INVALID_REQUEST,
    429: ProviderErrorCategory.RATE_LIMITED,
    500: ProviderErrorCategory.UPSTREAM_UNAVAILABLE,
    502: ProviderErrorCategory.UPSTREAM_UNAVAILABLE,
    503: ProviderErrorCategory.UPSTREAM_UNAVAILABLE,
    504: ProviderErrorCategory.UPSTREAM_UNAVAILABLE,
}


@dataclass
class CircuitBreaker:
    """Per-provider consecutive-failure breaker (Section 31).

    A breaker that counts errors and is never fed successes cannot recover
    (observed in the engine's own hot path). ``record_success`` MUST be wired
    on the success path -- it is, in ``retrying_json_call``.
    """

    failure_threshold: int = 5
    cooldown_sec: float = 60.0
    state: str = "CLOSED"
    failure_count: int = 0
    opened_at: float = 0.0

    def allow(self, now: float | None = None) -> bool:
        """True when a request may be attempted."""
        if self.state != "OPEN":
            return True
        ts = time.time() if now is None else now
        if ts - self.opened_at >= self.cooldown_sec:
            self.state = "HALF_OPEN"
            return True
        return False

    def record_success(self) -> None:
        self.state = "CLOSED"
        self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold and self.state != "OPEN":
            self.state = "OPEN"
            self.opened_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "cooldown_sec": self.cooldown_sec,
        }


class TransportResult:
    """A completed provider round trip: parsed JSON plus latency budget."""

    def __init__(
        self,
        *,
        status_code: int,
        payload: Any,
        latency_ms: float,
        request_id: str,
        retry_after: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.latency_ms = latency_ms
        self.request_id = request_id
        self.retry_after = retry_after
        self.headers = headers or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "latency_ms": round(self.latency_ms, 3),
            "request_id": self.request_id,
            "retry_after": self.retry_after,
        }


def _parse_retry_after(value: str | None) -> float | None:
    """Parse ``Retry-After`` as seconds (integer form; the common case)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _backoff_seconds(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """Exponential backoff with full jitter (Section 32)."""
    expo = min(cap, base * (2**attempt))
    return random.uniform(0.0, expo)


def retrying_json_call(
    *,
    url: str,
    method: str = "POST",
    json_body: Any,
    headers: dict[str, str],
    timeout: float = 20.0,
    max_retries: int = 3,
    breaker: CircuitBreaker,
    provider_id: str,
    secrets: tuple[str, ...] = (),
) -> TransportResult:
    """Make an authenticated JSON call with retry, backoff and breaking.

    Raises :class:`ProviderError` on every terminal failure, so an adapter's
    error surface is exactly one type.

    Retries are bounded by ``max_retries`` AND by category: a permanent failure
    (bad key, unknown model, malformed request) is never retried as if it were
    a transient network blip (Section 32).
    """
    request_id = f"{provider_id}-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
    last_error: ProviderError | None = None
    attempt = 0

    while attempt <= max_retries:
        # 1. Breaker gate comes first: no network IO when it is open.
        if not breaker.allow():
            logger.warning("[AI-PROV] circuit open, refusing egress to %s", provider_id)
            raise ProviderError(
                ProviderErrorCategory.CIRCUIT_OPEN,
                "circuit breaker open; refusing to send request",
                provider_id=provider_id,
            )

        started = time.monotonic()
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(method, url, json=json_body, headers=headers)
            elapsed_ms = (time.monotonic() - started) * 1000.0
        except httpx.TimeoutException as exc:
            elapsed_ms = (time.monotonic() - started) * 1000.0
            last_error = ProviderError(
                ProviderErrorCategory.TIMEOUT,
                f"request timed out after {timeout:.1f}s",
                provider_id=provider_id,
                retry_after=None,
                body_snippet="",
            )
            _ = exc
            breaker.record_failure()
            logger.warning("[AI-PROV] %s timeout (attempt %d)", provider_id, attempt + 1)
            if attempt >= max_retries or not last_error.retryable:
                raise
            time.sleep(_backoff_seconds(attempt))
            attempt += 1
            continue
        except httpx.HTTPError as exc:  # connect/DNS/reset -- the network, not the provider
            last_error = ProviderError(
                ProviderErrorCategory.NETWORK,
                f"transport error: {exc.__class__.__name__}",
                provider_id=provider_id,
            )
            breaker.record_failure()
            logger.warning("[AI-PROV] %s network error (attempt %d): %s", provider_id, attempt + 1, exc.__class__.__name__)
            if attempt >= max_retries or not last_error.retryable:
                raise
            time.sleep(_backoff_seconds(attempt))
            attempt += 1
            continue

        # 2. Non-2xx -> category, redacted snippet, and retry decision.
        if response.status_code >= 400:
            retry_after = _parse_retry_after(response.headers.get("retry-after"))
            snippet = redact(response.text[:1000], secrets)
            category = _STATUS_MAP.get(response.status_code, ProviderErrorCategory.UNKNOWN)
            breaker.record_failure()
            err = ProviderError(
                category,
                f"HTTP {response.status_code}",
                provider_id=provider_id,
                status_code=response.status_code,
                retry_after=retry_after,
                body_snippet=snippet,
            )
            last_error = err
            if not err.retryable or attempt >= max_retries:
                logger.error("[AI-PROV] %s terminal failure: %s", provider_id, err)
                raise err
            wait = retry_after if retry_after is not None else _backoff_seconds(attempt)
            logger.warning(
                "[AI-PROV] %s retryable %d (attempt %d, waiting %.2fs)",
                provider_id, response.status_code, attempt + 1, wait,
            )
            time.sleep(wait)
            attempt += 1
            continue

        # 3. 2xx -- but a 200 with an unparseable body is still a failure.
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            breaker.record_failure()
            raise ProviderError(
                ProviderErrorCategory.MALFORMED_RESPONSE,
                "response body is not valid JSON",
                provider_id=provider_id,
                status_code=response.status_code,
                body_snippet=redact(response.text[:1000], secrets),
            ) from exc

        breaker.record_success()
        return TransportResult(
            status_code=response.status_code,
            payload=payload,
            latency_ms=elapsed_ms,
            request_id=request_id,
            retry_after=_parse_retry_after(response.headers.get("retry-after")),
            headers=dict(response.headers),
        )

    # Loop exited without a result: the last retryable failure is the cause.
    if last_error is not None:
        raise last_error
    raise ProviderError(
        ProviderErrorCategory.UNKNOWN,
        "retry loop exhausted without a response",
        provider_id=provider_id,
    )
