"""Global provider gate — ONE boundary for ALL external LLM requests.

CHG-0034 (2026-09-01, MASTER STEER: provider rate-limit hardening).

Why this module exists
----------------------
The external OpenAI-compatible provider (Strategy Factory assisted
generation + News AI analysis) is an UNRELIABLE, RATE-LIMITED dependency.
The original provider code made one bare ``httpx.post`` per call with no
429 handling, no backoff, no circuit breaker, while the news pipeline
added its own soft retry (x2) and the news worker re-queued failed
articles (x3) — a retry-amplification storm: N articles x 2 retries x 3
requeues = 6N provider hits during an outage, each answered with HTTP 429.

Contract (PROVIDER_HEALTH_GATE v1):

* ALL outbound provider traffic goes through :class:`ProviderGate`.
  No caller may bypass the configuration check, rate limiter, concurrency
  limit, retry owner, or circuit breaker (proven by
  tests/unit/test_provider_gate_hardening.py).
* ONE retry owner: the gate. The HTTP transport has retries disabled
  (httpx transport retries=0) and callers MUST NOT re-loop on failure.
* 429 = RATE_LIMITED (capacity condition), NOT a permanent error: bounded
  retries honoring ``Retry-After`` with exponential backoff + jitter;
  sustained 429s open the circuit (temporary pause), the feature recovers
  automatically via half-open probe.
* Permanent configuration errors (API_KEY_MISSING, HOST_MISSING,
  INVALID_HOST, INVALID_CONFIG) NEVER touch the network and are
  AUTO_DISABLED instantly (BUG-187). Authentication failures (401/403)
  are permanent credential errors: bounded retries then auto-disable.
* Trading isolation (INV-024): the gate NEVER blocks or slows the
  trading loop — it runs inside the already-off-loop factory worker /
  news worker threads; callers get a structured
  :class:`GateResult` instead of exceptions, and waiting only ever
  happens on the external-request path (bounded), never on market data,
  70D inference, strategy, risk, or execution paths.
* Secrets NEVER leave the secret store boundary: no method on this module
  returns or logs the API key; log helpers redact credential-bearing
  substrings from URLs.

State machine (BUG-186 remediation)::

    AVAILABLE --(failures)--> RATE_LIMITED / DEGRADED
    RATE_LIMITED --(sustained)--> CIRCUIT_OPEN
    CIRCUIT_OPEN --(cooldown)--> HALF_OPEN --(probe OK)--> AVAILABLE
    HALF_OPEN --(probe fail)--> CIRCUIT_OPEN
    config/auth error (permanent) --> AUTO_DISABLED (until user/config action)

The gate is deliberately self-contained (stdlib + httpx) with NO engine
dependencies so it is unit-testable with a fake transport and can never
import the live engine (no order authority — mirrors research/ safety).
"""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.strategies.factory.provider_gate")

__all__ = [
    "DisableReason",
    "FailureCategory",
    "GateConfig",
    "GateResult",
    "ProviderGate",
    "ProviderState",
    "redact_url",
]


# ---------------------------------------------------------------------------
# Normalized states / categories (steer sections 17, 26)
# ---------------------------------------------------------------------------


class ProviderState(StrEnum):
    """User-visible provider runtime state (health endpoint / UI)."""

    AVAILABLE = "AVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    DEGRADED = "DEGRADED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    HALF_OPEN = "HALF_OPEN"
    AUTO_DISABLED = "AUTO_DISABLED"
    DISABLED_BY_USER = "DISABLED_BY_USER"
    UNAVAILABLE = "UNAVAILABLE"


class FailureCategory(StrEnum):
    """Normalized health-check result categories (steer section 17).

    Callers classify raw HTTP/network outcomes into ONE of these; nobody
    downstream inspects raw HTTP details independently.
    """

    AVAILABLE = "AVAILABLE"
    CONFIG_ERROR = "CONFIG_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    SERVER_ERROR = "SERVER_ERROR"
    UNKNOWN = "UNKNOWN"


class DisableReason(StrEnum):
    """Permanent auto-disable reasons (steer sections 4, 24, 25)."""

    API_KEY_MISSING = "API_KEY_MISSING"
    HOST_MISSING = "HOST_MISSING"
    INVALID_HOST = "INVALID_HOST"
    INVALID_CONFIG = "INVALID_CONFIG"
    AUTH_FAILED = "AUTH_FAILED"
    NONE = "NONE"


#: Status codes that are permanent credential problems (steer section 25).
_AUTH_STATUS_CODES = frozenset({401, 403})
#: Status codes that mean the provider is over capacity right now.
_RATE_LIMIT_STATUS = frozenset({429})
#: Transient server-side conditions worth a bounded retry.
_TRANSIENT_STATUS = frozenset({500, 502, 503, 504})


@dataclass(frozen=True)
class GateConfig:
    """Bounded gate parameters (steer sections 19-23, 39).

    Defaults are conservative: the provider is an OPTIONAL assisted source,
    so the gate prefers failing fast + pausing over hammering.
    """

    #: Max concurrent in-flight provider requests (bounded semaphore).
    max_in_flight: int = 2
    #: Sustained token-bucket refill rate (requests/second, float).
    requests_per_second: float = 0.5
    #: Bucket capacity (burst size).
    bucket_capacity: int = 3
    #: Retry attempts per logical request (0 = no retry; total attempts =
    #: 1 + max_retries). One retry owner only.
    max_retries: int = 2
    #: Exponential backoff base (seconds): delay = base * 2**attempt + jitter.
    backoff_base_seconds: float = 2.0
    #: Backoff ceiling.
    backoff_max_seconds: float = 60.0
    #: Cap when the provider sends a huge Retry-After (seconds).
    retry_after_max_seconds: float = 120.0
    #: Consecutive rate-limit/network failures that open the circuit.
    circuit_breaker_threshold: int = 4
    #: Cooldown before the half-open probe.
    circuit_breaker_cooldown_seconds: float = 300.0
    #: Bound on per-request wall time inside the gate (seconds).
    request_timeout_sec: float = 120.0
    #: Max queued waiters (acquire backlog) before rejecting with
    #: RATE_LIMITED (bounded queue — steer section 28).
    max_queue: int = 8


@dataclass
class GateResult:
    """Structured outcome of one gated request — callers NEVER see raw HTTP."""

    ok: bool
    data: Any = None
    category: FailureCategory = FailureCategory.AVAILABLE
    state: ProviderState = ProviderState.AVAILABLE
    reason: str = ""
    attempts: int = 0
    retry_after_sec: float | None = None
    duration_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "category": self.category.value,
            "state": self.state.value,
            "reason": self.reason,
            "attempts": self.attempts,
            "retry_after_sec": self.retry_after_sec,
            "duration_ms": round(self.duration_ms, 1),
        }


# ---------------------------------------------------------------------------
# Secret hygiene (steer section 12)
# ---------------------------------------------------------------------------


def redact_url(url: str) -> str:
    """Returns a log-safe URL: userinfo credentials and key query params are
    stripped. Never raises."""
    try:
        parsed = urllib.parse.urlsplit(url)
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[-1]
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        safe_keys = {"key", "api_key", "apikey", "token", "access_token", "password", "secret"}
        safe_query = [(k, ("[REDACTED]" if k.lower() in safe_keys else v)) for k, v in query]
        return urllib.parse.urlunsplit(
            (parsed.scheme, netloc, parsed.path, urllib.parse.urlencode(safe_query), "")
        )
    except Exception:
        return "<unparseable-url>"


# ---------------------------------------------------------------------------
# Configuration validation (steer sections 9, 10, 24, 69)
# ---------------------------------------------------------------------------


def classify_config(api_key: str, base_url: str, model: str) -> tuple[DisableReason, str]:
    """Pure config validation — NO network. Returns (reason, detail).

    Cached by callers until configuration changes (steer section 69).
    """
    if not (api_key or "").strip():
        return DisableReason.API_KEY_MISSING, "API key is not configured (empty/missing)"
    if not (base_url or "").strip():
        return DisableReason.HOST_MISSING, "Provider base URL is not configured"
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme not in ("http", "https"):
        return (
            DisableReason.INVALID_HOST,
            f"Base URL scheme must be http/https, got '{parsed.scheme or '(none)'}'",
        )
    if not parsed.hostname:
        return DisableReason.INVALID_HOST, "Base URL has no host part"
    try:
        urllib.parse.urlparse(base_url.strip())
    except ValueError:
        return DisableReason.INVALID_HOST, "Base URL is not parseable"
    if not (model or "").strip():
        return DisableReason.INVALID_CONFIG, "Model name is not configured"
    return DisableReason.NONE, ""


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@dataclass
class _CircuitState:
    """Mutable circuit-breaker state (one instance per provider identity)."""

    state: ProviderState = ProviderState.AVAILABLE
    consecutive_rate_limit_failures: int = 0
    consecutive_network_failures: int = 0
    opened_at: float = 0.0
    last_transition_ts: float = field(default_factory=time.time)
    last_error_category: str = ""
    last_error_detail: str = ""
    auto_disabled: bool = False
    auto_disabled_reason: DisableReason = DisableReason.NONE
    auto_disabled_detail: str = ""
    auto_disabled_at: float = 0.0
    last_success_ts: float = 0.0
    notified_states: set[str] = field(default_factory=set)


class ProviderGate:
    """Global rate-limit + circuit-breaker + concurrency boundary.

    One instance per provider identity (the factory provider singleton);
    thread-safe: used from the factory worker thread, news worker thread,
    and web API thread-pool concurrently.
    """

    def __init__(
        self, config: GateConfig | None = None, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.cfg = config or GateConfig()
        self._clock = clock
        self._lock = threading.RLock()
        self._circuit = _CircuitState()
        self._semaphore = threading.BoundedSemaphore(self.cfg.max_in_flight)
        self._in_flight = 0
        self._queue_depth = 0
        # Token bucket (rate limiter — steer section 19).
        self._tokens = float(self.cfg.bucket_capacity)
        self._bucket_last = self._clock()
        # Single-flight dedup (steer section 30): in-flight identical
        # requests share one external call.
        self._single_flight: dict[str, list[_SingleFlightWaiter]] = {}
        # Metrics (steer section 38).
        self.metrics = {
            "provider_requests_total": 0,
            "provider_success_total": 0,
            "provider_429_total": 0,
            "provider_auth_failures_total": 0,
            "provider_network_failures_total": 0,
            "provider_timeout_total": 0,
            "provider_retry_total": 0,
            "provider_circuit_open_total": 0,
            "provider_queue_rejected_total": 0,
            "provider_single_flight_reused_total": 0,
        }

    # ------------------------------------------------------------------
    # Configuration / health (NO network — steer sections 9, 16, 58)
    # ------------------------------------------------------------------

    def validate_config(self, api_key: str, base_url: str, model: str) -> tuple[DisableReason, str]:
        reason, detail = classify_config(api_key, base_url, model)
        if reason is not DisableReason.NONE and not self._circuit.auto_disabled:
            # Permanent configuration error: auto-disable IMMEDIATELY,
            # no retries, no HTTP, no repeated warnings (steer section 24).
            with self._lock:
                self._auto_disable_locked(reason, detail)
        return reason, detail

    def _auto_disable_locked(self, reason: DisableReason, detail: str) -> None:
        c = self._circuit
        if c.auto_disabled and c.auto_disabled_reason is reason:
            return  # idempotent, no repeated notifications (steer 63)
        c.auto_disabled = True
        c.auto_disabled_reason = reason
        c.auto_disabled_detail = detail
        c.auto_disabled_at = self._clock()
        c.state = ProviderState.AUTO_DISABLED
        c.consecutive_rate_limit_failures = 0
        c.consecutive_network_failures = 0
        c.last_transition_ts = self._clock()
        logger.error(
            "[PROVIDER_GATE] event=AUTO_DISABLED reason=%s detail=%s trading_engine=UNAFFECTED",
            reason.value,
            detail,
        )

    def reconfigure(self) -> None:
        """Configuration changed (user saved new key/host): re-validate.

        Called by the settings save path. If the new config is valid, the
        gate returns to AVAILABLE WITHOUT sending anything (recovery probe
        happens on the next real request or explicit Test Provider).
        """
        with self._lock:
            c = self._circuit
            c.auto_disabled = False
            c.auto_disabled_reason = DisableReason.NONE
            c.auto_disabled_detail = ""
            c.state = ProviderState.AVAILABLE
            c.consecutive_rate_limit_failures = 0
            c.consecutive_network_failures = 0
            c.notified_states.clear()
            c.last_transition_ts = self._clock()
            logger.info("[PROVIDER_GATE] event=RECONFIGURED state=AVAILABLE (no probe sent)")

    def health_snapshot(self) -> dict[str, Any]:
        """Secret-free health payload for /api/factory/provider-health
        (steer sections 16, 58). Never includes the API key."""
        with self._lock:
            c = self._circuit
            effective = self.effective_state_locked()
            return {
                "provider_state": c.state.value,
                "effective_state": effective.value,
                "auto_disabled": c.auto_disabled,
                "auto_disabled_reason": c.auto_disabled_reason.value,
                "auto_disabled_detail": c.auto_disabled_detail,
                "auto_disabled_at": c.auto_disabled_at,
                "last_success_ts": c.last_success_ts,
                "last_error_category": c.last_error_category,
                "last_error_detail": c.last_error_detail,
                "consecutive_rate_limit_failures": c.consecutive_rate_limit_failures,
                "consecutive_network_failures": c.consecutive_network_failures,
                "circuit_open": c.state is ProviderState.CIRCUIT_OPEN,
                "cooldown_remaining_sec": (
                    max(
                        0.0,
                        self.cfg.circuit_breaker_cooldown_seconds - (self._clock() - c.opened_at),
                    )
                    if c.state is ProviderState.CIRCUIT_OPEN
                    else 0.0
                ),
                "rate_limited": c.state is ProviderState.RATE_LIMITED,
                "in_flight": self._in_flight,
                "queue_depth": self._queue_depth,
                "max_in_flight": self.cfg.max_in_flight,
                "requests_per_second": self.cfg.requests_per_second,
                "metrics": dict(self.metrics),
            }

    def effective_state_locked(self) -> ProviderState:
        """State as the UI should show it, given auto-disable semantics."""
        c = self._circuit
        if c.auto_disabled:
            return ProviderState.AUTO_DISABLED
        if c.state in (ProviderState.CIRCUIT_OPEN, ProviderState.HALF_OPEN):
            # Cooldown elapsed? Report HALF_OPEN (recovering) prospectively.
            if (
                c.state is ProviderState.CIRCUIT_OPEN
                and self._clock() - c.opened_at >= self.cfg.circuit_breaker_cooldown_seconds
            ):
                return ProviderState.HALF_OPEN
        return c.state

    # ------------------------------------------------------------------
    # Rate limiter (token bucket) — only external requests wait here
    # ------------------------------------------------------------------

    def _acquire_token_locked(self) -> bool:
        now = self._clock()
        elapsed = now - self._bucket_last
        self._bucket_last = now
        self._tokens = min(
            float(self.cfg.bucket_capacity), self._tokens + elapsed * self.cfg.requests_per_second
        )
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    # ------------------------------------------------------------------
    # Circuit breaker helpers
    # ------------------------------------------------------------------

    def _record_rate_limit_failure_locked(self) -> None:
        c = self._circuit
        c.consecutive_rate_limit_failures += 1
        c.consecutive_network_failures = 0
        c.last_error_category = FailureCategory.RATE_LIMITED.value
        if (
            c.consecutive_rate_limit_failures >= self.cfg.circuit_breaker_threshold
            and c.state is not ProviderState.CIRCUIT_OPEN
        ):
            self._open_circuit_locked()

    def _record_network_failure_locked(self, category: FailureCategory, detail: str) -> None:
        c = self._circuit
        c.consecutive_network_failures += 1
        c.last_error_category = category.value
        c.last_error_detail = detail[:200]
        if (
            c.consecutive_network_failures >= self.cfg.circuit_breaker_threshold
            and c.state is not ProviderState.CIRCUIT_OPEN
        ):
            self._open_circuit_locked()

    def _open_circuit_locked(self) -> None:
        c = self._circuit
        c.state = ProviderState.CIRCUIT_OPEN
        c.opened_at = self._clock()
        c.last_transition_ts = c.opened_at
        self.metrics["provider_circuit_open_total"] += 1
        logger.warning(
            "[PROVIDER_GATE] event=CIRCUIT_OPEN consecutive=%s cooldown_sec=%s trading_engine=UNAFFECTED",
            c.consecutive_rate_limit_failures or c.consecutive_network_failures,
            self.cfg.circuit_breaker_cooldown_seconds,
        )

    def _record_success_locked(self) -> None:
        c = self._circuit
        recovered = c.state in (
            ProviderState.RATE_LIMITED,
            ProviderState.DEGRADED,
            ProviderState.CIRCUIT_OPEN,
            ProviderState.HALF_OPEN,
        )
        c.state = ProviderState.AVAILABLE
        c.consecutive_rate_limit_failures = 0
        c.consecutive_network_failures = 0
        c.last_error_category = ""
        c.last_error_detail = ""
        c.last_success_ts = self._clock()
        c.last_transition_ts = c.last_success_ts
        if recovered:
            # ONE recovery event, no spam (steer section 36).
            logger.info("[PROVIDER_GATE] event=RECOVERED state=AVAILABLE")

    # ------------------------------------------------------------------
    # Main entry: gated request execution
    # ------------------------------------------------------------------

    def execute(
        self,
        request_key: str,
        send: Callable[[], GateResult],
        *,
        single_flight: bool = True,
    ) -> GateResult:
        """Runs ONE logical external request under the full gate chain.

        ``send`` performs the raw HTTP call and returns a GateResult with
        the category set; the gate owns retries, pacing, concurrency,
        dedup, and the circuit. Never raises into the caller.
        """
        started = self._clock()
        try:
            result = self._execute_inner(request_key, send, single_flight=single_flight)
        except Exception as e:  # pragma: no cover - absolute isolation guarantee
            logger.error(
                "[PROVIDER_GATE] event=GATE_INTERNAL_ERROR error=%s trading_engine=UNAFFECTED",
                type(e).__name__,
            )
            result = GateResult(
                ok=False,
                category=FailureCategory.UNKNOWN,
                state=ProviderState.DEGRADED,
                reason=f"gate internal error: {type(e).__name__}",
            )
        result.duration_ms = (self._clock() - started) * 1000.0
        return result

    def _execute_inner(
        self, request_key: str, send: Callable[[], GateResult], *, single_flight: bool
    ) -> GateResult:
        c = self._circuit
        # 1. Auto-disable / user-disable short-circuit (steer 24, 33):
        #    no network, no queue, instant structured rejection.
        with self._lock:
            if c.auto_disabled:
                return GateResult(
                    ok=False,
                    category=FailureCategory.CONFIG_ERROR
                    if c.auto_disabled_reason is not DisableReason.AUTH_FAILED
                    else FailureCategory.AUTH_ERROR,
                    state=ProviderState.AUTO_DISABLED,
                    reason=c.auto_disabled_detail or c.auto_disabled_reason.value,
                )
            if c.state is ProviderState.CIRCUIT_OPEN:
                remaining = self.cfg.circuit_breaker_cooldown_seconds - (
                    self._clock() - c.opened_at
                )
                if remaining > 0:
                    return GateResult(
                        ok=False,
                        category=FailureCategory.RATE_LIMITED
                        if c.last_error_category == FailureCategory.RATE_LIMITED.value
                        else FailureCategory.NETWORK_ERROR,
                        state=ProviderState.CIRCUIT_OPEN,
                        reason=f"circuit open, cooldown {remaining:.0f}s remaining",
                        retry_after_sec=remaining,
                    )
                # Cooldown elapsed: half-open probe.
                c.state = ProviderState.HALF_OPEN
                c.last_transition_ts = self._clock()
                logger.info("[PROVIDER_GATE] event=HALF_OPEN probe=armed")

        # 2. Single-flight dedup (steer section 30): identical concurrent
        #    logical requests share ONE external call.
        if single_flight:
            waiter = _SingleFlightWaiter()
            with self._lock:
                waiters = self._single_flight.get(request_key)
                if waiters is not None:
                    waiters.append(waiter)
                    self.metrics["provider_single_flight_reused_total"] += 1
                    # Wait for the leader's outcome (bounded by its own retry
                    # budget); leader broadcasts to all waiters.
                    return waiter.wait()
                self._single_flight[request_key] = [waiter]
            try:
                result = self._execute_gated(send)
            finally:
                with self._lock:
                    group = self._single_flight.pop(request_key, None)
            if group:
                waiter.broadcast(result)
                # Include followers in our own return path? No — we return
                # the leader result directly (we ARE the leader).
            return result
        return self._execute_gated(send)

    def _execute_gated(self, send: Callable[[], GateResult]) -> GateResult:
        """Leader path: pacing -> queue -> concurrency -> bounded retries."""
        # 3. Queue admission (bounded backlog — steer section 28).
        with self._lock:
            if self._queue_depth >= self.cfg.max_queue:
                self.metrics["provider_queue_rejected_total"] += 1
                return GateResult(
                    ok=False,
                    category=FailureCategory.RATE_LIMITED,
                    state=self._circuit.state,
                    reason="provider queue full (bounded); request deferred",
                )
            self._queue_depth += 1
        try:
            return self._execute_paced(send)
        finally:
            with self._lock:
                self._queue_depth -= 1

    def _execute_paced(self, send: Callable[[], GateResult]) -> GateResult:
        max_attempts = 1 + max(0, self.cfg.max_retries)
        result = GateResult(ok=False, category=FailureCategory.UNKNOWN)
        for attempt in range(max_attempts):
            result = self._attempt_once(send, attempt)
            result.attempts = attempt + 1
            if result.ok:
                return result
            if result.category in (
                FailureCategory.CONFIG_ERROR,
                FailureCategory.AUTH_ERROR,
            ):
                # Permanent: no retry (steer sections 24, 25).
                return result
            if attempt < max_attempts - 1:
                self.metrics["provider_retry_total"] += 1
                delay = self._retry_delay(result, attempt)
                if delay > 0:
                    # Sleep ONLY on the external path (off-loop worker
                    # threads — INV-024: never the trading tick loop).
                    time.sleep(delay)
        return result

    def _retry_delay(self, result: GateResult, attempt: int) -> float:
        if result.retry_after_sec is not None:
            return min(result.retry_after_sec, self.cfg.retry_after_max_seconds)
        base = min(self.cfg.backoff_base_seconds * (2**attempt), self.cfg.backoff_max_seconds)
        return base * (0.5 + random.random() * 0.5)  # jitter

    def _attempt_once(self, send: Callable[[], GateResult], attempt: int) -> GateResult:
        # 4. Rate-limit pacing BEFORE taking a concurrency slot so a wait
        #    never holds a worker slot hostage.
        with self._lock:
            if not self._acquire_token_locked():
                return GateResult(
                    ok=False,
                    category=FailureCategory.RATE_LIMITED,
                    state=self._circuit.state,
                    reason="local rate limiter: bucket empty (pacing)",
                )
        acquired = self._semaphore.acquire(timeout=self.cfg.request_timeout_sec)
        if not acquired:
            return GateResult(
                ok=False,
                category=FailureCategory.RATE_LIMITED,
                state=self._circuit.state,
                reason="concurrency limit: no slot within timeout",
            )
        self._in_flight += 1
        try:
            self.metrics["provider_requests_total"] += 1
            raw = send()
        finally:
            self._in_flight -= 1
            self._semaphore.release()

        # 5. Circuit accounting (single classification authority).
        with self._lock:
            if raw.category is FailureCategory.RATE_LIMITED:
                self.metrics["provider_429_total"] += 1
                self._circuit.state = ProviderState.RATE_LIMITED
                self._record_rate_limit_failure_locked()
            elif raw.category in (FailureCategory.NETWORK_ERROR, FailureCategory.TIMEOUT):
                if raw.category is FailureCategory.TIMEOUT:
                    self.metrics["provider_timeout_total"] += 1
                self.metrics["provider_network_failures_total"] += 1
                self._circuit.state = ProviderState.DEGRADED
                self._record_network_failure_locked(raw.category, raw.reason)
            elif raw.category is FailureCategory.AUTH_ERROR:
                self.metrics["provider_auth_failures_total"] += 1
                # Invalid credentials are a PERMANENT configuration problem
                # (steer section 25): auto-disable after the bounded attempt.
                self._auto_disable_locked(
                    DisableReason.AUTH_FAILED,
                    raw.reason or "provider rejected the credentials (authentication failure)",
                )
            elif raw.ok:
                self.metrics["provider_success_total"] += 1
                self._record_success_locked()
            else:
                # SERVER_ERROR / UNKNOWN: degrade without opening circuit
                # unless repeated (counted as network-class failures).
                self._record_network_failure_locked(raw.category, raw.reason)
        return raw


# ---------------------------------------------------------------------------
# Shared HTTP execution (one classification authority — steer section 17)
# ---------------------------------------------------------------------------


def parse_retry_after(header_value: str | None) -> float | None:
    """Parses a Retry-After header (delay-seconds or HTTP-date) to seconds."""
    if not header_value:
        return None
    value = header_value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds())
    except Exception:
        return None


def classify_status(status_code: int) -> FailureCategory:
    """Maps an HTTP status to ONE normalized category (steer section 17)."""
    if status_code == 200:
        return FailureCategory.AVAILABLE
    if status_code in _AUTH_STATUS_CODES:
        return FailureCategory.AUTH_ERROR
    if status_code in _RATE_LIMIT_STATUS:
        return FailureCategory.RATE_LIMITED
    if status_code in _TRANSIENT_STATUS:
        return FailureCategory.SERVER_ERROR
    return FailureCategory.UNKNOWN


def execute_http_post(
    gate: ProviderGate,
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    request_key: str,
    single_flight: bool = True,
) -> GateResult:
    """ONE gated JSON POST used by every external caller.

    Handles: gate chain (config/circuit/rate/concurrency/retry/dedup),
    response classification, Retry-After parsing, SSE-framing-tolerant
    200-body parsing (some compatible endpoints append 'data: [DONE]').
    Never raises. Never logs or returns the Authorization header.
    """

    def send() -> GateResult:
        try:
            import httpx
        except ImportError:
            return GateResult(
                ok=False, category=FailureCategory.UNKNOWN, reason="httpx unavailable"
            )
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        except httpx.TimeoutException as e:
            return GateResult(
                ok=False, category=FailureCategory.TIMEOUT, reason=f"timeout: {type(e).__name__}"
            )
        except Exception as e:
            return GateResult(
                ok=False,
                category=FailureCategory.NETWORK_ERROR,
                reason=f"network: {type(e).__name__}",
            )
        if resp.status_code != 200:
            category = classify_status(resp.status_code)
            retry_after = (
                parse_retry_after(resp.headers.get("Retry-After"))
                if category is FailureCategory.RATE_LIMITED
                else None
            )
            return GateResult(
                ok=False,
                category=category,
                reason=f"HTTP:{resp.status_code}",
                retry_after_sec=retry_after,
            )
        # 200: tolerant JSON parse (direct, then SSE-frame strip).
        try:
            return GateResult(ok=True, data=resp.json(), category=FailureCategory.AVAILABLE)
        except Exception:
            pass
        body = resp.text
        marker = body.rfind("data: [DONE]")
        if marker > 0:
            body = body[:marker].rstrip()
        try:
            return GateResult(ok=True, data=json.loads(body), category=FailureCategory.AVAILABLE)
        except Exception:
            return GateResult(
                ok=False, category=FailureCategory.UNKNOWN, reason="BAD_JSON_RESPONSE"
            )

    return gate.execute(request_key, send, single_flight=single_flight)


def get_provider_gate() -> ProviderGate:
    """Process-wide singleton — the ONE global provider boundary (steer 18)."""
    global _GLOBAL_GATE  # noqa: PLW0603 - deliberate process-wide singleton
    if _GLOBAL_GATE is None:
        _GLOBAL_GATE = ProviderGate()
    return _GLOBAL_GATE


_GLOBAL_GATE: ProviderGate | None = None


class _SingleFlightWaiter:
    """Minimal event-based waiter for single-flight dedup."""

    __slots__ = ("_event", "_result")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._result: GateResult | None = None

    def wait(self, timeout: float = 900.0) -> GateResult:
        self._event.wait(timeout)
        if self._result is not None:
            return self._result
        return GateResult(
            ok=False,
            category=FailureCategory.UNKNOWN,
            state=ProviderState.DEGRADED,
            reason="single-flight leader did not report",
        )

    def broadcast(self, result: GateResult) -> None:
        self._result = result
        self._event.set()
