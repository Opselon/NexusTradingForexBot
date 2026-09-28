"""Provider registry and persistent configuration (ECOSYSTEM-001, Section 3).

One canonical source of provider identity. Nothing in the ecosystem may
hardcode a provider id, endpoint, model or key -- it all comes from here, which
is what makes a future provider an additive change instead of a rewrite.

PERSISTENCE
    ``ProviderRegistryStore`` is the ONLY writer to the ``ai_provider_config``
    and ``ai_provider_activation`` tables. Additive schema only (``CREATE TABLE
    IF NOT EXISTS``), same SQLite conventions as the settings service. Providers
    are consulted off-tick -- never from the tick hot path (INV-001).

SECRETS (Section 37)
    API keys NEVER live here. Endpoints/models/timeouts do; the key lives in the
    existing DPAPI ``SecureSecretStore`` and is referenced by secret NAME only.
    ``to_public_dict`` is the sole serialization that leaves this module and it
    emits neither the key nor its secret name -- not to UI, logs, diagnostics,
    or config export (Section 48).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.ai_providers.registry")

__all__ = [
    "ACTIVATION_VERSION",
    "BUILTIN_PROVIDER_IDS",
    "CUSTOM_ID_PREFIX",
    "PROVIDER_TEMPLATES",
    "PROVIDER_TYPE_EXTERNAL",
    "PROVIDER_TYPE_INTERNAL",
    "STATE_ACTIVE",
    "STATE_AUTH_FAILED",
    "STATE_CONTRACT_FAILED",
    "STATE_DEACTIVATING",
    "STATE_INACTIVE",
    "STATE_LOADED",
    "STATE_REGISTERED",
    "STATE_TESTING",
    "STATE_TEST_FAILED",
    "STATE_UNAVAILABLE",
    "STATE_VERIFIED",
    "ActivationState",
    "DecisionMode",
    "ProviderConfig",
    "ProviderRegistryStore",
    "ProviderTemplate",
    "is_valid_provider_id",
    "lifecycle_for_test_failure",
    "lifecycle_transition",
    "template_for",
]

PROVIDER_TYPE_INTERNAL = "internal"
PROVIDER_TYPE_EXTERNAL = "external"

#: Built-in ids. ``internal_nse_ml`` is always present and always enabled: it
#: is the safe floor the fallback chain lands on (Section 6).
BUILTIN_PROVIDER_IDS: tuple[str, ...] = ("internal_nse_ml", "system_one", "openrouter")
_KNOWN_IDS: frozenset[str] = frozenset(BUILTIN_PROVIDER_IDS)

#: Prefix a custom provider id must carry. Custom ids are namespaced so they
#: can never collide with a future built-in adapter id (Section 40).
CUSTOM_ID_PREFIX = "custom_"

#: Bumped when the activation row shape changes; stored for forward compat.
ACTIVATION_VERSION = "1"


def _normalize_custom_id(provider_id: str) -> str:
    """Canonicalize a caller-supplied provider id (Section 40).

    A custom id is lower-cased, trimmed and namespaced. It must be a safe
    identifier — it lands in a SQLite/Postgres primary key and in log lines —
    so anything with a path separator, whitespace or a quote is rejected by the
    caller (``is_valid_provider_id``), not silently mangled.
    """
    pid = (provider_id or "").strip().lower()
    if not pid:
        return ""
    if not pid.startswith(CUSTOM_ID_PREFIX):
        pid = CUSTOM_ID_PREFIX + pid
    return pid


def is_valid_provider_id(provider_id: str) -> bool:
    """A safe identifier for a provider id (used for BOTH built-ins and custom).

    Rejection is fail-closed: an id that could reach a log line, a URL path or a
    SQL identifier as anything other than a plain token is refused here.
    """
    pid = (provider_id or "").strip().lower()
    if not pid or len(pid) > 64:
        return False
    if pid in _KNOWN_IDS:
        return True
    return pid.startswith(CUSTOM_ID_PREFIX) and pid[len(CUSTOM_ID_PREFIX) :].isalnum()


class DecisionMode(StrEnum):
    """Provider modes (Section 13). Persisted by value, so stable forever."""

    DISABLED = "DISABLED"
    INTERNAL_ONLY = "INTERNAL_ONLY"
    EXTERNAL_ONLY = "EXTERNAL_ONLY"
    HYBRID = "HYBRID"
    SHADOW = "SHADOW"
    COMPARISON = "COMPARISON"
    FALLBACK = "FALLBACK"
    DETERMINISTIC_ONLY = "DETERMINISTIC_ONLY"


#: Provider lifecycle (Sections 30/45). The registry persists only INACTIVE /
#: VERIFIED-style states; the transient ones (TESTING/LOADED) are runtime-only
#: and never written to a row — a restart must not claim a provider was mid-test.
STATE_REGISTERED = "REGISTERED"
STATE_TESTING = "TESTING"
STATE_VERIFIED = "VERIFIED"
STATE_LOADED = "LOADED"
STATE_ACTIVE = "ACTIVE"
STATE_DEACTIVATING = "DEACTIVATING"
STATE_INACTIVE = "INACTIVE"
STATE_TEST_FAILED = "TEST_FAILED"
STATE_CONTRACT_FAILED = "CONTRACT_FAILED"
STATE_AUTH_FAILED = "AUTH_FAILED"
STATE_UNAVAILABLE = "UNAVAILABLE"

#: States that may be PERSISTED in a provider row.
_PERSISTED_STATES: frozenset[str] = frozenset(
    {
        STATE_REGISTERED,
        STATE_VERIFIED,
        STATE_INACTIVE,
        # ACTIVE IS persisted: "which provider is live" is durable state that
        # must survive a restart, and the activation row alone is not enough --
        # it records the operator's CHOICE, while lifecycle_state records the
        # provider's own runtime state (Section 42: no UI-only states).
        STATE_ACTIVE,
        STATE_TEST_FAILED,
        STATE_CONTRACT_FAILED,
        STATE_AUTH_FAILED,
        STATE_UNAVAILABLE,
    }
)

#: The legal lifecycle transition table (Section 45). A transition absent from
#: this map is rejected by the store, so the backend cannot be driven into an
#: impossible state by a stale UI or a racing request.
#:
#: NOTE on REGISTERED: TESTING is transient and never persisted, so after a
#: successful test the row is still REGISTERED and the move the store actually
#: validates is REGISTERED -> VERIFIED (or -> a failure state). Omitting those
#: edges would make a first test on a fresh provider impossible.
_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_REGISTERED: frozenset(
        {
            STATE_TESTING,
            STATE_VERIFIED,
            STATE_INACTIVE,
            STATE_TEST_FAILED,
            STATE_CONTRACT_FAILED,
            STATE_AUTH_FAILED,
            STATE_UNAVAILABLE,
        }
    ),
    STATE_TESTING: frozenset(
        {
            STATE_VERIFIED,
            STATE_TEST_FAILED,
            STATE_CONTRACT_FAILED,
            STATE_AUTH_FAILED,
            STATE_UNAVAILABLE,
        }
    ),
    STATE_VERIFIED: frozenset(
        {
            STATE_LOADED,
            # ACTIVATE goes straight from VERIFIED: the switch is gated on a
            # passing test, and a synthetic LOADED step the runtime never
            # performs would only hide the real transition (Section 26).
            STATE_ACTIVE,
            STATE_INACTIVE,
            STATE_REGISTERED,
            STATE_TESTING,
        }
    ),
    STATE_LOADED: frozenset({STATE_ACTIVE, STATE_INACTIVE, STATE_VERIFIED}),
    STATE_ACTIVE: frozenset({STATE_DEACTIVATING, STATE_INACTIVE, STATE_UNAVAILABLE}),
    STATE_DEACTIVATING: frozenset({STATE_INACTIVE, STATE_ACTIVE}),
    STATE_INACTIVE: frozenset({STATE_TESTING, STATE_REGISTERED, STATE_VERIFIED}),
    # Failure states recover through the explicit re-test path only.
    STATE_TEST_FAILED: frozenset({STATE_TESTING, STATE_INACTIVE}),
    STATE_CONTRACT_FAILED: frozenset({STATE_TESTING, STATE_INACTIVE}),
    STATE_AUTH_FAILED: frozenset({STATE_TESTING, STATE_INACTIVE}),
    STATE_UNAVAILABLE: frozenset({STATE_TESTING, STATE_INACTIVE}),
}

#: Map a test failure onto the lifecycle state that records WHY it failed
#: (Section 25: technical compatibility is the gate, and the reason is shown).
_TEST_FAILURE_STATES: dict[str, str] = {
    "AUTH_FAILED": STATE_AUTH_FAILED,
    "CONTRACT_FAILED": STATE_CONTRACT_FAILED,
    "SCHEMA_VIOLATION": STATE_CONTRACT_FAILED,
    "MALFORMED_RESPONSE": STATE_CONTRACT_FAILED,
    "MODEL_UNAVAILABLE": STATE_CONTRACT_FAILED,
    "UPSTREAM_UNAVAILABLE": STATE_UNAVAILABLE,
    "NETWORK": STATE_UNAVAILABLE,
    "TIMEOUT": STATE_UNAVAILABLE,
    "RATE_LIMITED": STATE_UNAVAILABLE,
}


def lifecycle_transition(current: str, target: str) -> bool:
    """Is ``current -> target`` a legal lifecycle transition (Section 45)?

    A provider always starts from REGISTERED when it has no recorded state, so
    the first test is legal. Self-transitions are allowed (idempotent re-report
    from health polling must not be rejected as an illegal jump).
    """
    if current == target:
        return True
    return target in _TRANSITIONS.get(current, frozenset())


def lifecycle_for_test_failure(error_category: str | None) -> str:
    """Which persisted state records a test failure of this category?"""
    if not error_category:
        return STATE_TEST_FAILED
    return _TEST_FAILURE_STATES.get(error_category, STATE_TEST_FAILED)


class ProviderTemplate:
    """A provider blueprint (Section 3): identity + safe contract skeleton.

    Selecting a template in the UI populates a skeleton (endpoint, auth type,
    default model) the operator then customizes — never arbitrary code, and
    never the secret itself. ``adapter_type`` is the class the registry builds.
    """

    def __init__(
        self,
        *,
        template_id: str,
        label: str,
        description: str,
        adapter_type: str,
        capabilities: list[str],
        supports_model_listing: bool,
        supports_usage: bool,
        defaults: dict[str, str],
        fields: list[str],
    ) -> None:
        self.template_id = template_id
        self.label = label
        self.description = description
        self.adapter_type = adapter_type
        self.capabilities = list(capabilities)
        self.supports_model_listing = bool(supports_model_listing)
        self.supports_usage = bool(supports_usage)
        self.defaults = dict(defaults)
        self.fields = list(fields)

    def to_public_dict(self) -> dict[str, Any]:
        """Skeleton for the UI. Carries no secret and no key reference."""
        return {
            "template_id": self.template_id,
            "label": self.label,
            "description": self.description,
            "adapter_type": self.adapter_type,
            "capabilities": list(self.capabilities),
            "supports_model_listing": self.supports_model_listing,
            "supports_usage": self.supports_usage,
            "defaults": dict(self.defaults),
            "fields": list(self.fields),
        }


#: The provider template catalogue (Section 3). One entry per real adapter;
#: built-ins resolve to their own template so the same "add" path serves both.
PROVIDER_TEMPLATES: tuple[ProviderTemplate, ...] = (
    ProviderTemplate(
        template_id="internal_nse_ml",
        label="NSE Internal Model",
        description="The built-in NSE position ML. First-class provider, always available as the fallback floor. No endpoint or credential.",
        adapter_type="internal_nse_ml",
        capabilities=["position_decision", "internal_ml"],
        supports_model_listing=False,
        supports_usage=False,
        defaults={"endpoint": "", "auth_type": "none", "default_model": "nse-internal"},
        fields=["provider_name", "enabled"],
    ),
    ProviderTemplate(
        template_id="system_one",
        label="Custom HTTP JSON",
        description="A custom OpenAI-compatible HTTP JSON endpoint that answers calibrated structured questions (the System One shape).",
        adapter_type="system_one",
        capabilities=["position_decision", "structured_questions"],
        supports_model_listing=False,
        supports_usage=True,
        defaults={"endpoint": "", "auth_type": "bearer", "default_model": ""},
        fields=["provider_name", "endpoint", "default_model", "api_key", "timeout", "max_retries"],
    ),
    ProviderTemplate(
        template_id="openrouter",
        label="OpenRouter-compatible",
        description="An OpenRouter-compatible chat-completions endpoint with a /models discovery API.",
        adapter_type="openrouter",
        capabilities=["position_decision", "chat_completions", "model_discovery"],
        supports_model_listing=True,
        supports_usage=True,
        defaults={
            "endpoint": "https://openrouter.ai/api/v1/chat/completions",
            "auth_type": "bearer",
            "default_model": "",
        },
        fields=["provider_name", "endpoint", "default_model", "api_key", "timeout", "max_retries"],
    ),
)


def template_for(template_id: str) -> ProviderTemplate | None:
    for t in PROVIDER_TEMPLATES:
        if t.template_id == template_id:
            return t
    return None


class ProviderConfig:
    """One provider's configuration. The registry's unit of state.

    A frozen-ish dataclass (mutable, guarded by the store's RLock): runtime
    health fields update in place; configuration fields change only through the
    store so ``configuration_version`` stays meaningful.
    """

    def __init__(
        self,
        provider_id: str,
        provider_name: str,
        type: str = PROVIDER_TYPE_EXTERNAL,
        endpoint: str = "",
        auth_type: str = "bearer",
        secret_name: str = "",
        default_model: str = "",
        available_models: list[str] | None = None,
        capabilities: list[str] | None = None,
        timeout: float = 20.0,
        max_retries: int = 3,
        enabled: bool = False,
        active: bool = False,
        health_status: str = "UNKNOWN",
        last_test: str | None = None,
        latency_ms: float | None = None,
        failure_count: int = 0,
        last_error: str = "",
        last_success: str | None = None,
        cost_metadata: dict[str, Any] | None = None,
        rate_limit_state: str = "UNKNOWN",
        circuit_breaker_state: str = "CLOSED",
        configuration_version: str = "1",
        lifecycle_state: str = STATE_REGISTERED,
        template_id: str = "",
    ) -> None:
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.type = type
        self.endpoint = endpoint
        self.auth_type = auth_type
        #: DPAPI key reference. The plaintext never appears on this object.
        self.secret_name = secret_name
        self.default_model = default_model
        self.available_models = list(available_models or [])
        self.capabilities = list(capabilities or [])
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.enabled = bool(enabled)
        self.active = bool(active)
        self.health_status = health_status
        self.last_test = last_test
        self.latency_ms = latency_ms
        self.failure_count = int(failure_count)
        self.last_error = last_error
        self.last_success = last_success
        self.cost_metadata = dict(cost_metadata or {})
        self.rate_limit_state = rate_limit_state
        self.circuit_breaker_state = circuit_breaker_state
        self.configuration_version = configuration_version
        #: Lifecycle (Section 45). Only persistable states are ever written to a
        #: row; the transient ones are runtime-only.
        self.lifecycle_state = (
            lifecycle_state if lifecycle_state in _PERSISTED_STATES else STATE_REGISTERED
        )
        #: The template this provider was built from (Section 3). For built-ins
        #: this equals the provider id; for a custom provider it names the
        #: adapter class the registry must instantiate.
        self.template_id = template_id

    @property
    def has_secret(self) -> bool:
        return bool(self.secret_name)

    @property
    def is_internal(self) -> bool:
        return self.type == PROVIDER_TYPE_INTERNAL

    def to_public_dict(self) -> dict[str, Any]:
        """Serialization for UI/API/CLI/export. NEVER contains a secret.

        Omitting ``secret_name`` too means an exported config (Section 48)
        carries no key handle -- imported credentials must be re-entered.
        """
        d: dict[str, Any] = {
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "type": self.type,
            "endpoint": self.endpoint,
            "auth_type": self.auth_type,
            "default_model": self.default_model,
            "available_models": list(self.available_models),
            "capabilities": list(self.capabilities),
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "enabled": self.enabled,
            "active": self.active,
            "health_status": self.health_status,
            "last_test": self.last_test,
            "latency_ms": self.latency_ms,
            "failure_count": self.failure_count,
            "last_error": self.last_error,
            "last_success": self.last_success,
            "cost_metadata": dict(self.cost_metadata),
            "rate_limit_state": self.rate_limit_state,
            "circuit_breaker_state": self.circuit_breaker_state,
            "configuration_version": self.configuration_version,
            "lifecycle_state": self.lifecycle_state,
            "template_id": self.template_id,
            "has_secret": self.has_secret,
            # Masked hint only -- never the key itself (Section 20: "Secrets
            # must be masked. Never display the full secret after save.").
            "secret_hint": "***SET***" if self.has_secret else None,
        }
        return d

    def to_storage_dict(self) -> dict[str, Any]:
        """Row payload. Carries the secret *reference* so an adapter can resolve
        it at call time; never the key."""
        d = {
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "type": self.type,
            "endpoint": self.endpoint,
            "auth_type": self.auth_type,
            "secret_name": self.secret_name,
            "default_model": self.default_model,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "enabled": self.enabled,
            "active": self.active,
            "health_status": self.health_status,
            "last_test": self.last_test,
            "latency_ms": self.latency_ms,
            "failure_count": self.failure_count,
            "last_error": self.last_error,
            "last_success": self.last_success,
            "rate_limit_state": self.rate_limit_state,
            "circuit_breaker_state": self.circuit_breaker_state,
            "configuration_version": self.configuration_version,
            "lifecycle_state": self.lifecycle_state,
            "template_id": self.template_id,
        }
        # Containers are JSON-encoded: a list/dict cannot live in one SQLite
        # column, and encoding keeps the round-trip exact.
        d["available_models"] = json.dumps(list(self.available_models))
        d["capabilities"] = json.dumps(list(self.capabilities))
        d["cost_metadata"] = json.dumps(self.cost_metadata, default=str)
        return d


@dataclass
class ActivationState:
    """The active selection: primary/secondary/fallback + mode + shadow."""

    primary_provider: str
    secondary_provider: str | None = None
    fallback_provider: str | None = None
    decision_mode: str = DecisionMode.INTERNAL_ONLY
    shadow_provider: str | None = None
    configuration_version: str = ACTIVATION_VERSION
    updated_at: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "primary_provider": self.primary_provider,
            "secondary_provider": self.secondary_provider,
            "fallback_provider": self.fallback_provider,
            "decision_mode": self.decision_mode,
            "shadow_provider": self.shadow_provider,
            "configuration_version": self.configuration_version,
            "updated_at": self.updated_at,
        }


#: The primary the switch in flight is moving TO. Thread-local would be the
#: clean carrier, but ``set_activation`` already holds ``self._lock`` for the
#: whole transaction, so this single slot is race-free within one store and is
#: reset on the way out.
_PENDING_PRIMARY: dict[str, str | None] = {"value": None}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ProviderRegistryStore:
    """The single reader/writer of provider configuration state.

    Thread-safe (RLock) and safe to share between the API, the CLI and the
    engine, because all three are clients of this one backend contract
    (Section 61: backend is the source of truth).
    """

    _TABLE_CONFIG = "ai_provider_config"
    _TABLE_ACTIVATION = "ai_provider_activation"
    _TABLE_PREV_ACTIVATION = "ai_provider_prev_activation"

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Construct the provider-configuration registry.

        ``db_path`` accepts the two target shapes the fabric uses, mirroring
        ``ProviderDecisionStore`` (PR #462): a SQLite ``Path``/``str`` or a
        PostgreSQL DSN ``str``. When it is omitted, the target is resolved
        from the persisted application database provider, so a box switched to
        PostgreSQL no longer keeps its provider configuration in a stray
        SQLite file while the engine uses PostgreSQL.

        A SQLite ``Path``/``str`` argument is honored as given — tests pass a
        temp file and expect the store to use exactly that file, not the
        persisted provider (the ``NEXUS_SETTINGS_DB`` / ``NEXUS_AUDIT_DB``
        test-isolation contract).
        """
        self._lock = threading.RLock()
        if db_path is None:
            from nexus_scalp.settings.paths import resolve_registry_target

            db_path = resolve_registry_target()
        if isinstance(db_path, str):
            # A DSN string means PostgreSQL; the SQLite driver is never built
            # from a path here (see the SQLite branch below).
            self._cfg = load_database_config()
            self._driver = get_driver(self._cfg)
        else:
            self._db_path = Path(db_path)
            self._cfg = DatabaseConfig.for_sqlite("settings", path=str(self._db_path))
            self._driver = get_driver(self._cfg)
        self._ensure_schema()

    # -- schema -----------------------------------------------------------------
    def _ensure_schema(self) -> None:
        with self._lock:
            with self._driver.transaction() as conn:
                conn.execute(
                    f"""CREATE TABLE IF NOT EXISTS {self._TABLE_CONFIG} (
                    provider_id TEXT PRIMARY KEY,
                    blob TEXT NOT NULL,
                    configuration_version TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
                )
                conn.execute(
                    f"""CREATE TABLE IF NOT EXISTS {self._TABLE_ACTIVATION} (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    primary_provider TEXT NOT NULL,
                    secondary_provider TEXT,
                    fallback_provider TEXT,
                    decision_mode TEXT NOT NULL,
                    shadow_provider TEXT,
                    configuration_version TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
                )
                # ROLLBACK target (Section 17): the activation that was live
                # BEFORE the current one. Additive — an older registry simply
                # has an empty history and rollback then reports "nothing to
                # restore", which is the honest answer.
                conn.execute(
                    f"""CREATE TABLE IF NOT EXISTS {self._TABLE_PREV_ACTIVATION} (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    primary_provider TEXT NOT NULL,
                    secondary_provider TEXT,
                    fallback_provider TEXT,
                    decision_mode TEXT NOT NULL,
                    shadow_provider TEXT,
                    configuration_version TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
                )

    # -- provider rows ------------------------------------------------------------
    def upsert(self, cfg: ProviderConfig, actor: str = "ui") -> bool:
        """Insert/replace one provider.

        Accepts a known built-in id or a SAFE custom id (``custom_*``). An id
        nothing implements — an unsafe identifier, or one that is neither
        built-in nor namespaced custom — is refused: a row the registry cannot
        later address is a silent future failure (Section 40).
        """
        if not is_valid_provider_id(cfg.provider_id):
            logger.error("[AI-PROV] refused write of invalid provider_id=%s", cfg.provider_id)
            return False
        blob = json.dumps(cfg.to_storage_dict(), default=str)
        with self._lock:
            with self._driver.transaction() as conn:
                conn.execute(
                    f"""INSERT INTO {self._TABLE_CONFIG}
                        (provider_id, blob, configuration_version, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(provider_id) DO UPDATE SET
                        blob=excluded.blob,
                        configuration_version=excluded.configuration_version,
                        updated_at=excluded.updated_at""",
                    (cfg.provider_id, blob, cfg.configuration_version, _now_iso()),
                )
        logger.info(
            "[AI-PROV] saved provider=%s actor=%s v=%s",
            cfg.provider_id,
            actor,
            cfg.configuration_version,
        )
        return True

    def apply_lifecycle(
        self,
        provider_id: str,
        target: str,
        *,
        failure_category: str | None = None,
        actor: str = "runtime",
    ) -> bool:
        """Move one provider along its lifecycle (Section 45).

        Illegal transitions are REJECTED and logged, never silently coerced —
        a UI that posts a stale sequence cannot drive the registry into a state
        the backend never agreed to. Returns whether the transition happened.

        ``target`` may be a transient state (TESTING/LOADED/ACTIVE): those are
        validated for legality but only persistable states are written, so a
        restart never claims a provider was mid-test or already active.
        """
        if not is_valid_provider_id(provider_id):
            return False
        with self._lock:
            cfg = self.get_config(provider_id)
            if cfg is None:
                # A lifecycle move on an unknown row registers it first: the
                # common UI path is "add then test", and add writes the row.
                return False
            current = cfg.lifecycle_state
            if not lifecycle_transition(current, target):
                logger.warning(
                    "[AI-PROV] rejected lifecycle %s -> %s for %s (illegal transition)",
                    current,
                    target,
                    provider_id,
                )
                return False
            persisted = target if target in _PERSISTED_STATES else current
            if persisted == current:
                # A legal transient move (e.g. TESTING) records nothing but
                # still succeeded: the caller may proceed.
                return True
            cfg.lifecycle_state = persisted
            if target in (STATE_TEST_FAILED, STATE_CONTRACT_FAILED, STATE_AUTH_FAILED):
                cat = failure_category or ""
                cfg.last_error = f"lifecycle={target}" + (f" category={cat}" if cat else "")
                cfg.last_test = _now_iso()
            elif target == STATE_VERIFIED:
                cfg.last_test = _now_iso()
                cfg.last_error = ""
            elif target == STATE_INACTIVE:
                cfg.last_error = ""
            return self.upsert(cfg, actor=actor)

    def delete(self, provider_id: str) -> bool:
        """Delete a provider row. Built-ins are protected from removal."""
        if provider_id in BUILTIN_PROVIDER_IDS:
            logger.warning("[AI-PROV] refused delete of built-in provider %s", provider_id)
            return False
        if not is_valid_provider_id(provider_id):
            logger.warning("[AI-PROV] refused delete of invalid provider_id=%s", provider_id)
            return False
        with self._lock:
            # Read the DELETE's own rowcount inside the transaction. A
            # pre-transaction existence check on a separate connection lies
            # under concurrency: two threads both read existed=True, both
            # DELETE, both return True while only one removed a row. On
            # PostgreSQL MVCC the snapshot can disagree too.
            with self._driver.transaction() as conn:
                removed = conn.execute(
                    f"DELETE FROM {self._TABLE_CONFIG} WHERE provider_id = ?",
                    (provider_id,),
                ).rowcount
        if removed:
            logger.info("[AI-PROV] removed provider=%s", provider_id)
        return bool(removed)

    def get_config(self, provider_id: str) -> ProviderConfig | None:
        """One provider config, or ``None`` if absent. Named ``get_config`` to
        avoid shadowing the ``ProviderConfig`` ``get``/``list`` helpers."""
        with self._lock:
            row = self._driver.query_one(
                f"SELECT blob FROM {self._TABLE_CONFIG} WHERE provider_id = ?",
                (provider_id,),
            )
        return None if row is None else _blob_to_config(row["blob"])

    def list_configs(self) -> list[ProviderConfig]:
        """All stored provider configs. (Named ``list_configs``: ``ProviderConfig``
        also has a ``list`` helper, and shadowing it here made the type invalid.)"""
        with self._lock:
            rows = self._driver.query(f"SELECT provider_id, blob FROM {self._TABLE_CONFIG}")
        return [c for c in (_blob_to_config(r["blob"]) for r in rows) if c is not None]

    def list_provider_ids(self) -> list[str]:
        with self._lock:
            rows = self._driver.query(f"SELECT provider_id FROM {self._TABLE_CONFIG}")
        return [r["provider_id"] for r in rows]

    # -- activation ---------------------------------------------------------------
    def set_activation(self, state: ActivationState, actor: str = "ui") -> bool:
        """Persist the active selection atomically (Section 14).

        Refuses an unknown primary: activation is the last gate before a
        provider can influence a decision, so this is where "enabled" stops
        silently meaning "active". A primary must be a registered provider id
        the registry can actually address (built-in or a safe custom id).
        """
        if not is_valid_provider_id(state.primary_provider):
            logger.error("[AI-PROV] refused activation, invalid primary=%s", state.primary_provider)
            return False
        for pid in (state.secondary_provider, state.fallback_provider, state.shadow_provider):
            if pid and not is_valid_provider_id(pid):
                logger.error("[AI-PROV] refused activation, invalid peer=%s", pid)
                return False
        try:
            DecisionMode(state.decision_mode)
        except ValueError:
            logger.error("[AI-PROV] refused activation, unknown mode=%s", state.decision_mode)
            return False
        state.updated_at = _now_iso()
        with self._lock:
            _PENDING_PRIMARY["value"] = state.primary_provider
            with self._driver.transaction() as conn:
                # Snapshot the activation being REPLACED before the write lands
                # (Section 17: rollback needs the state that was live before
                # the switch, and the snapshot must be taken in the SAME
                # transaction as the switch or a crash leaves a rollback target
                # that never corresponded to a live configuration).
                self._snapshot_prev_activation(conn)
                _PENDING_PRIMARY["value"] = None
                conn.execute(
                    f"""INSERT INTO {self._TABLE_ACTIVATION}
                        (id, primary_provider, secondary_provider, fallback_provider,
                         decision_mode, shadow_provider, configuration_version, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        primary_provider=excluded.primary_provider,
                        secondary_provider=excluded.secondary_provider,
                        fallback_provider=excluded.fallback_provider,
                        decision_mode=excluded.decision_mode,
                        shadow_provider=excluded.shadow_provider,
                        configuration_version=excluded.configuration_version,
                        updated_at=excluded.updated_at""",
                    (
                        state.primary_provider,
                        state.secondary_provider,
                        state.fallback_provider,
                        str(state.decision_mode),
                        state.shadow_provider,
                        state.configuration_version,
                        state.updated_at,
                    ),
                )
        logger.info(
            "[AI-PROV] activation primary=%s secondary=%s fallback=%s mode=%s shadow=%s actor=%s",
            state.primary_provider,
            state.secondary_provider,
            state.fallback_provider,
            state.decision_mode,
            state.shadow_provider,
            actor,
        )
        return True

    def get_activation(self) -> ActivationState | None:
        with self._lock:
            row = self._driver.query_one(f"SELECT * FROM {self._TABLE_ACTIVATION} WHERE id = 1")
        if row is None:
            return None
        return ActivationState(
            primary_provider=row["primary_provider"],
            secondary_provider=row["secondary_provider"],
            fallback_provider=row["fallback_provider"],
            decision_mode=row["decision_mode"],
            shadow_provider=row["shadow_provider"],
            configuration_version=row["configuration_version"],
            updated_at=row["updated_at"],
        )

    def _snapshot_prev_activation(self, conn: Any) -> None:
        """Copy the CURRENT activation into the rollback table (Section 17).

        Called inside the switch transaction. Skips a no-op switch: overwriting
        the rollback target with the same activation would erase the last good
        rollback point for nothing.
        """
        row = conn.execute(f"SELECT * FROM {self._TABLE_ACTIVATION} WHERE id = 1").fetchone()
        if row is None:
            return
        as_dict = dict(row)
        if as_dict.get("primary_provider") == _PENDING_PRIMARY["value"]:
            return
        conn.execute(
            f"""INSERT INTO {self._TABLE_PREV_ACTIVATION}
                (id, primary_provider, secondary_provider, fallback_provider,
                 decision_mode, shadow_provider, configuration_version, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                primary_provider=excluded.primary_provider,
                secondary_provider=excluded.secondary_provider,
                fallback_provider=excluded.fallback_provider,
                decision_mode=excluded.decision_mode,
                shadow_provider=excluded.shadow_provider,
                configuration_version=excluded.configuration_version,
                updated_at=excluded.updated_at""",
            (
                as_dict["primary_provider"],
                as_dict.get("secondary_provider"),
                as_dict.get("fallback_provider"),
                as_dict.get("decision_mode"),
                as_dict.get("shadow_provider"),
                as_dict.get("configuration_version"),
                as_dict.get("updated_at"),
            ),
        )

    def get_previous_activation(self) -> ActivationState | None:
        """The activation that was live before the current one (Section 17)."""
        with self._lock:
            row = self._driver.query_one(
                f"SELECT * FROM {self._TABLE_PREV_ACTIVATION} WHERE id = 1"
            )
        if row is None:
            return None
        return ActivationState(
            primary_provider=row["primary_provider"],
            secondary_provider=row["secondary_provider"],
            fallback_provider=row["fallback_provider"],
            decision_mode=row["decision_mode"],
            shadow_provider=row["shadow_provider"],
            configuration_version=row["configuration_version"],
            updated_at=row["updated_at"],
        )

    def clear_previous_activation(self) -> None:
        """Drop the rollback target (after a rollback consumes it)."""
        with self._lock:
            with self._driver.transaction() as conn:
                conn.execute(f"DELETE FROM {self._TABLE_PREV_ACTIVATION} WHERE id = 1")


def _blob_to_config(blob: str) -> ProviderConfig | None:
    """Deserialize a storage row, tolerating an older shape.

    A row whose blob cannot be parsed is dropped with an error rather than
    raising -- one corrupt provider must not take the whole registry down.
    """
    try:
        d = json.loads(blob)
        for key in ("available_models", "capabilities", "cost_metadata"):
            raw = d.get(key)
            if isinstance(raw, str):
                d[key] = json.loads(raw) if raw else ([] if key != "cost_metadata" else {})
        return ProviderConfig(**d)
    except Exception as exc:
        logger.error("[AI-PROV] dropped unreadable provider row: %s", exc)
        return None
