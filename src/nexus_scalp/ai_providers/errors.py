"""Normalized failure taxonomy for the AI provider ecosystem.

WHY THIS EXISTS
---------------
Section 5 of the ecosystem contract forbids collapsing every provider failure
into a single ``"AI provider failed."`` string. Every adapter maps its transport
and protocol failures onto exactly one :class:`ProviderErrorCategory` so that
the fallback policy, the circuit breaker, the UI health panel and the CLI can
all reason about failures *mechanically* instead of by parsing prose.

The taxonomy is split along the axis that actually matters for retry policy:

``PERMANENT``  -- retrying cannot help; the operator must change configuration
                  (bad key, unknown model, malformed request). The circuit
                  breaker counts these as configuration faults, not outages.
``RETRYABLE``  -- transient; backoff + jitter + breaker apply.

Anything unmapped lands in ``UNKNOWN`` and is treated as retryable-once, never
as a silent success.

No message produced here may ever contain a credential: adapters pass a
``body_snippet`` that they have already redacted (see ``redact``).
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "PERMANENT_CATEGORIES",
    "RETRYABLE_CATEGORIES",
    "ProviderError",
    "ProviderErrorCategory",
    "redact",
]


class ProviderErrorCategory(StrEnum):
    """Stable, machine-readable failure categories.

    These strings are persisted in health records and surfaced in the UI, so
    they are an API: never rename one, only add.
    """

    #: 401/403 -- credentials rejected. Permanent until the operator edits them.
    AUTH_FAILED = "AUTH_FAILED"
    #: The configured model does not exist / was retired / is not routable.
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    #: 400 -- our request is malformed. Permanent; retrying repeats the error.
    INVALID_REQUEST = "INVALID_REQUEST"
    #: 429 -- transient; honour ``Retry-After`` when present.
    RATE_LIMITED = "RATE_LIMITED"
    #: Deadline exceeded before a complete response arrived.
    TIMEOUT = "TIMEOUT"
    #: 500/502/503 -- provider-side outage, usually transient.
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    #: Connect/DNS/refused/reset -- the box or the network, not the provider.
    NETWORK = "NETWORK"
    #: HTTP 200 whose body is not JSON, or is JSON of the wrong shape.
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    #: Parsed successfully but violates the canonical response contract.
    #: This is NOT a transport failure -- the provider is up and answering,
    #: which is exactly why it must never be masked as "provider failed".
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    #: The local breaker is open; the request was refused before any network IO.
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    #: Nothing else matched. Never reported as success anywhere.
    UNKNOWN = "UNKNOWN"


#: Retrying is meaningful for these. Backoff/jitter/breaker all key off this.
RETRYABLE_CATEGORIES: frozenset[ProviderErrorCategory] = frozenset(
    {
        ProviderErrorCategory.RATE_LIMITED,
        ProviderErrorCategory.TIMEOUT,
        ProviderErrorCategory.UPSTREAM_UNAVAILABLE,
        ProviderErrorCategory.NETWORK,
        ProviderErrorCategory.UNKNOWN,
    }
)

#: Retrying is *harmful* for these: it burns quota and delays the fallback
#: decision. The fallback chain must engage immediately instead.
PERMANENT_CATEGORIES: frozenset[ProviderErrorCategory] = frozenset(
    {
        ProviderErrorCategory.AUTH_FAILED,
        ProviderErrorCategory.MODEL_UNAVAILABLE,
        ProviderErrorCategory.INVALID_REQUEST,
        ProviderErrorCategory.SCHEMA_VIOLATION,
    }
)


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Strip credential-shaped substrings from a provider payload snippet.

    Called by adapters BEFORE a body is attached to an error, because an error
    message is persisted in health records and shown in the UI. Two passes:
    explicit known secrets first (exact match, so a rotated key in flight is
    still caught), then generic token shapes.
    """
    out = text
    for secret in secrets:
        if secret and secret in out:
            out = out.replace(secret, "***REDACTED***")
    # Bearer tokens and the project's own key prefixes.
    for marker in ("Bearer ", "sk-", "Bearer%20"):
        idx = out.find(marker)
        while idx != -1:
            end = idx + len(marker)
            while end < len(out) and out[end] not in ' \t\r\n",\'':
                end += 1
            if end > idx + len(marker):
                out = out[: idx + len(marker)] + "***REDACTED***" + out[end:]
                idx = out.find(marker, idx + len(marker))
            else:  # marker with nothing after it -- nothing to hide
                idx = out.find(marker, idx + len(marker))
    return out[:500]


class ProviderError(RuntimeError):
    """A provider failure carrying its normalized category and retry policy.

    Adapters raise this; nothing else in the ecosystem raises provider errors.
    The orchestrator catches exactly this type, so an adapter bug that raises
    ``ValueError`` instead surfaces as ``UNKNOWN`` rather than vanishing.
    """

    def __init__(
        self,
        category: ProviderErrorCategory,
        message: str,
        *,
        provider_id: str = "",
        status_code: int | None = None,
        retry_after: float | None = None,
        body_snippet: str = "",
        model: str = "",
    ) -> None:
        self.category = ProviderErrorCategory(category)
        self.provider_id = provider_id
        self.status_code = status_code
        #: Seconds the provider asked us to wait (429/503 Retry-After), if any.
        self.retry_after = retry_after
        self.body_snippet = body_snippet[:500]
        self.model = model
        super().__init__(f"[{self.category.value}] {provider_id or '?'}: {message}")

    @property
    def retryable(self) -> bool:
        """True when a retry could plausibly succeed (see ``RETRYABLE``)."""
        return self.category in RETRYABLE_CATEGORIES

    @property
    def permanent(self) -> bool:
        """True when only a configuration change can fix this."""
        return self.category in PERMANENT_CATEGORIES

    def to_dict(self) -> dict[str, object]:
        """Structured form for health records, traces and API responses."""
        return {
            "category": self.category.value,
            "provider_id": self.provider_id,
            "model": self.model,
            "status_code": self.status_code,
            "retry_after": self.retry_after,
            "retryable": self.retryable,
            "message": str(self),
            "body_snippet": self.body_snippet,
        }
