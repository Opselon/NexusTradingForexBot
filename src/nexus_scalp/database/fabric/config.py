"""Fabric configuration — one canonical configuration object.

Phase 35 of the DATABASE FABRIC mission (DB-FABRIC-001).

No subsystem may define a competing DB configuration schema.  This object is
the single representation of: provider, primary/read DSNs, domain overrides,
pool sizing, timeouts, migration policy, read consistency, replica usage,
health-check interval, retry policy and batch size.

Secrets: the persisted form stores connection METADATA + a secret REFERENCE
(never a plaintext password).  The DSN may carry a password in memory after
resolution from the OS-backed SecureSecretStore, but it is never serialized
by this module — use :meth:`masked` for any display/log/API surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nexus_scalp.database.config import (
    PG_PASSWORD_SECRET_KEY,
    mask_url_password,
)
from nexus_scalp.database.fabric.pg_planes import PoolLimits
from nexus_scalp.database.provider import DatabaseProvider


class MigrationPolicy(StrEnum):
    """When the fabric may apply schema migrations."""

    #: Apply safe additive migrations automatically at startup (today's default).
    AUTO = "auto"
    #: Require an operator to run the migration explicitly.
    MANUAL = "manual"
    #: Never migrate; refuse to start on schema drift.
    NEVER = "never"


class ReadConsistency(StrEnum):
    """Default read-consistency posture for a domain."""

    #: Prefer the primary for everything (safest; no replica use at all).
    PRIMARY_ONLY = "primary_only"
    #: STRONG reads to primary, EVENTUAL reads may use a replica.
    SPLIT = "split"


class RetryPolicy(StrEnum):
    """Transient-failure retry posture for writes."""

    NONE = "none"
    #: Retry idempotent writes with bounded backoff.
    IDEMPOTENT_ONLY = "idempotent_only"
    #: Retry all writes (requires the caller to guarantee idempotency).
    ALL = "all"


@dataclass
class FabricDomainConfig:
    """Per-domain override on top of the shared fabric profile."""

    domain: str
    provider: DatabaseProvider = DatabaseProvider.SQLITE
    #: Fully resolved DSN (password already injected; never log this raw).
    dsn: str = ""
    #: Optional read-replica DSN (EVENTUAL reads only).
    read_dsn: str = ""
    sqlite_path: str = ""
    pool_limits: PoolLimits = field(default_factory=PoolLimits)
    read_consistency: ReadConsistency = ReadConsistency.PRIMARY_ONLY
    migration_policy: MigrationPolicy = MigrationPolicy.AUTO
    retry_policy: RetryPolicy = RetryPolicy.IDEMPOTENT_ONLY
    batch_size: int = 500
    health_check_interval_sec: int = 30

    def masked(self) -> dict[str, Any]:
        """Display-safe representation — passwords never appear."""
        return {
            "domain": self.domain,
            "provider": self.provider.value,
            "dsn": mask_url_password(self.dsn) if self.dsn else "",
            "read_dsn": mask_url_password(self.read_dsn) if self.read_dsn else "",
            "sqlite_path": self.sqlite_path,
            "pool": {
                "min_size": self.pool_limits.min_size,
                "max_size": self.pool_limits.max_size,
                "statement_timeout_ms": self.pool_limits.statement_timeout_ms,
            },
            "read_consistency": self.read_consistency.value,
            "migration_policy": self.migration_policy.value,
            "retry_policy": self.retry_policy.value,
            "batch_size": self.batch_size,
            "health_check_interval_sec": self.health_check_interval_sec,
        }


@dataclass
class FabricConfig:
    """The canonical database-fabric configuration.

    A single primary connection profile drives every domain unless a
    per-domain override exists (advanced deployments).
    """

    provider: DatabaseProvider = DatabaseProvider.SQLITE
    #: Primary DSN profile.  May contain ``{db}`` which is replaced by the
    #: per-domain database name; otherwise used verbatim per domain.
    primary_dsn: str = ""
    read_dsn: str = ""
    #: Per-domain overrides (domain name -> config).  Absent => use the profile.
    domains: dict[str, FabricDomainConfig] = field(default_factory=dict)
    pool_limits: PoolLimits = field(default_factory=PoolLimits)
    read_consistency: ReadConsistency = ReadConsistency.PRIMARY_ONLY
    migration_policy: MigrationPolicy = MigrationPolicy.AUTO
    retry_policy: RetryPolicy = RetryPolicy.IDEMPOTENT_ONLY
    batch_size: int = 500
    health_check_interval_sec: int = 30
    #: SQLite workspace root for the default SQLite paths.
    sqlite_workspace: str = ""

    # -- masking -----------------------------------------------------------

    def masked(self) -> dict[str, Any]:
        """Display/log-safe snapshot.  Passwords are always masked."""
        return {
            "provider": self.provider.value,
            "primary_dsn": mask_url_password(self.primary_dsn),
            "read_dsn": mask_url_password(self.read_dsn),
            "pool": {
                "min_size": self.pool_limits.min_size,
                "max_size": self.pool_limits.max_size,
                "connect_timeout_sec": self.pool_limits.connect_timeout_sec,
                "statement_timeout_ms": self.pool_limits.statement_timeout_ms,
                "idle_timeout_sec": self.pool_limits.idle_timeout_sec,
            },
            "read_consistency": self.read_consistency.value,
            "migration_policy": self.migration_policy.value,
            "retry_policy": self.retry_policy.value,
            "batch_size": self.batch_size,
            "domains": {k: v.masked() for k, v in self.domains.items()},
        }

    # -- per-domain resolution --------------------------------------------

    def for_domain(self, domain: str) -> FabricDomainConfig:
        """Resolve the effective config for one domain.

        Override wins; otherwise the shared profile is applied to the domain.
        """
        if domain in self.domains:
            return self.domains[domain]
        return FabricDomainConfig(
            domain=domain,
            provider=self.provider,
            dsn=self.primary_dsn,
            read_dsn=self.read_dsn,
            pool_limits=self.pool_limits,
            read_consistency=self.read_consistency,
            migration_policy=self.migration_policy,
            retry_policy=self.retry_policy,
            batch_size=self.batch_size,
            health_check_interval_sec=self.health_check_interval_sec,
        )

    # -- construction ------------------------------------------------------

    @classmethod
    def sqlite_default(cls, workspace: str = "") -> FabricConfig:
        """Zero-configuration SQLite profile (the default)."""
        return cls(
            provider=DatabaseProvider.SQLITE,
            sqlite_workspace=workspace,
            read_consistency=ReadConsistency.PRIMARY_ONLY,
            migration_policy=MigrationPolicy.AUTO,
        )

    @classmethod
    def for_postgresql(
        cls,
        primary_dsn: str,
        *,
        read_dsn: str = "",
        pool_limits: PoolLimits | None = None,
    ) -> FabricConfig:
        return cls(
            provider=DatabaseProvider.POSTGRESQL,
            primary_dsn=primary_dsn,
            read_dsn=read_dsn,
            pool_limits=pool_limits or PoolLimits(min_size=2, max_size=10),
            read_consistency=ReadConsistency.SPLIT if read_dsn else ReadConsistency.PRIMARY_ONLY,
            migration_policy=MigrationPolicy.AUTO,
        )

    # -- persistence-safe serialization ------------------------------------

    def to_persistable(self) -> dict[str, Any]:
        """Serialize WITHOUT any password (metadata + secret references only)."""
        return {
            "provider": self.provider.value,
            "primary_dsn": _strip_password(self.primary_dsn),
            "read_dsn": _strip_password(self.read_dsn),
            "pool": {
                "min_size": self.pool_limits.min_size,
                "max_size": self.pool_limits.max_size,
                "connect_timeout_sec": self.pool_limits.connect_timeout_sec,
                "statement_timeout_ms": self.pool_limits.statement_timeout_ms,
                "idle_timeout_sec": self.pool_limits.idle_timeout_sec,
            },
            "read_consistency": self.read_consistency.value,
            "migration_policy": self.migration_policy.value,
            "retry_policy": self.retry_policy.value,
            "batch_size": self.batch_size,
            "health_check_interval_sec": self.health_check_interval_sec,
            "password_secret": PG_PASSWORD_SECRET_KEY,
            "domains": {k: v.masked() for k, v in self.domains.items()},
        }


def _strip_password(dsn: str) -> str:
    """Remove the password from a DSN for persistence (keep the shape)."""
    if not dsn:
        return ""
    masked = mask_url_password(dsn)
    # mask_url_password keeps "user:***"; strip the marker too for storage
    if "://***/" in masked:  # url form
        return masked.replace("://***/", ":///")
    if ":***@" in masked:  # keyword/url credential form
        return masked.replace(":***@", "@")
    return masked
