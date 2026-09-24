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
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.ai_providers.registry")

__all__ = [
    "ACTIVATION_VERSION",
    "BUILTIN_PROVIDER_IDS",
    "PROVIDER_TYPE_EXTERNAL",
    "PROVIDER_TYPE_INTERNAL",
    "ActivationState",
    "DecisionMode",
    "ProviderConfig",
    "ProviderRegistryStore",
]

PROVIDER_TYPE_INTERNAL = "internal"
PROVIDER_TYPE_EXTERNAL = "external"

#: Built-in ids. ``internal_nse_ml`` is always present and always enabled: it
#: is the safe floor the fallback chain lands on (Section 6).
BUILTIN_PROVIDER_IDS: tuple[str, ...] = ("internal_nse_ml", "system_one", "openrouter")
_KNOWN_IDS: frozenset[str] = frozenset(BUILTIN_PROVIDER_IDS)

#: Bumped when the activation row shape changes; stored for forward compat.
ACTIVATION_VERSION = "1"


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

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.RLock()
        self._ensure_schema()

    # -- schema -----------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
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
            conn.commit()

    # -- provider rows ------------------------------------------------------------
    def upsert(self, cfg: ProviderConfig, actor: str = "ui") -> bool:
        """Insert/replace one provider. Refuses an id nothing implements."""
        if cfg.provider_id not in _KNOWN_IDS:
            logger.error("[AI-PROV] refused write of unknown provider_id=%s", cfg.provider_id)
            return False
        blob = json.dumps(cfg.to_storage_dict(), default=str)
        with self._lock, self._connect() as conn:
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
            conn.commit()
        logger.info(
            "[AI-PROV] saved provider=%s actor=%s v=%s",
            cfg.provider_id,
            actor,
            cfg.configuration_version,
        )
        return True

    def delete(self, provider_id: str) -> bool:
        """Delete a provider row. Built-ins are protected from removal."""
        if provider_id in BUILTIN_PROVIDER_IDS:
            logger.warning("[AI-PROV] refused delete of built-in provider %s", provider_id)
            return False
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"DELETE FROM {self._TABLE_CONFIG} WHERE provider_id = ?", (provider_id,)
            ).rowcount
            conn.commit()
        if cur:
            logger.info("[AI-PROV] removed provider=%s", provider_id)
        return bool(cur)

    def get(self, provider_id: str) -> ProviderConfig | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT blob FROM {self._TABLE_CONFIG} WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
        return None if row is None else _blob_to_config(row["blob"])

    def list(self) -> list[ProviderConfig]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT provider_id, blob FROM {self._TABLE_CONFIG}").fetchall()
        return [c for c in (_blob_to_config(r["blob"]) for r in rows) if c is not None]

    def list_provider_ids(self) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT provider_id FROM {self._TABLE_CONFIG}").fetchall()
        return [r["provider_id"] for r in rows]

    # -- activation ---------------------------------------------------------------
    def set_activation(self, state: ActivationState, actor: str = "ui") -> bool:
        """Persist the active selection atomically (Section 14).

        Refuses an unknown primary: activation is the last gate before a
        provider can influence a decision, so this is where "enabled" stops
        silently meaning "active".
        """
        if state.primary_provider not in _KNOWN_IDS:
            logger.error("[AI-PROV] refused activation, unknown primary=%s", state.primary_provider)
            return False
        for pid in (state.secondary_provider, state.fallback_provider, state.shadow_provider):
            if pid and pid not in _KNOWN_IDS:
                logger.error("[AI-PROV] refused activation, unknown peer=%s", pid)
                return False
        try:
            DecisionMode(state.decision_mode)
        except ValueError:
            logger.error("[AI-PROV] refused activation, unknown mode=%s", state.decision_mode)
            return False
        state.updated_at = _now_iso()
        with self._lock, self._connect() as conn:
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
            conn.commit()
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
        with self._lock, self._connect() as conn:
            row = conn.execute(f"SELECT * FROM {self._TABLE_ACTIVATION} WHERE id = 1").fetchone()
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
