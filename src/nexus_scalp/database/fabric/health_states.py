"""Database health states — the fabric distinguishes, never collapses to "down".

Phase 27 of the DATABASE FABRIC mission (DB-FABRIC-001).

Every state is a distinct, machine-readable condition.  Callers (doctor,
debug snapshot, web health endpoints, startup gate) consume the enum so an
operator sees the real condition instead of a boolean.
"""

from __future__ import annotations

from enum import StrEnum


class DatabaseHealthState(StrEnum):
    """Distinct health conditions for a persistence domain."""

    READY = "READY"
    #: Degraded but usable: elevated latency, lock waits near a threshold,
    #: replica lag within tolerance, soft pool pressure.
    DEGRADED = "DEGRADED"
    #: Cannot serve requests at all right now.
    UNAVAILABLE = "UNAVAILABLE"
    #: The schema is behind the registry-expected version.
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
    #: A migration is in progress.
    MIGRATING = "MIGRATING"
    #: The last migration failed; the schema is at an inconsistent version.
    MIGRATION_FAILED = "MIGRATION_FAILED"
    #: The live schema does not match the manifest (unexpected columns/tables).
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    #: Credentials rejected by the provider.
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    #: All connections in the pool are checked out and the wait was refused.
    POOL_EXHAUSTED = "POOL_EXHAUSTED"
    #: The store is read-only (e.g. SQLite file opened mode=ro, or a standby).
    READ_ONLY = "READ_ONLY"
    #: Writes are refused (disk full, read-only media, writer not started).
    WRITE_BLOCKED = "WRITE_BLOCKED"
    #: A configured read replica is too far behind to serve EVENTUAL reads.
    REPLICA_LAGGING = "REPLICA_LAGGING"

    @property
    def is_ready(self) -> bool:
        return self is DatabaseHealthState.READY

    @property
    def blocks_engine(self) -> bool:
        """States that must prevent the engine from entering READY."""
        return self in {
            DatabaseHealthState.UNAVAILABLE,
            DatabaseHealthState.MIGRATION_FAILED,
            DatabaseHealthState.SCHEMA_DRIFT,
            DatabaseHealthState.AUTHENTICATION_FAILED,
            DatabaseHealthState.WRITE_BLOCKED,
        }

    @property
    def allows_reads(self) -> bool:
        """Reads may proceed (possibly degraded or stale)."""
        return self not in {
            DatabaseHealthState.UNAVAILABLE,
            DatabaseHealthState.AUTHENTICATION_FAILED,
            DatabaseHealthState.MIGRATION_FAILED,
        }

    @property
    def allows_writes(self) -> bool:
        return self in {
            DatabaseHealthState.READY,
            DatabaseHealthState.DEGRADED,
            DatabaseHealthState.MIGRATION_REQUIRED,
            DatabaseHealthState.MIGRATING,
        }


class HealthProbe:
    """One provider health observation (immutable, serializable)."""

    __slots__ = ("checked_at_iso", "detail", "domain", "provider", "state")

    def __init__(
        self,
        state: DatabaseHealthState,
        *,
        domain: str = "",
        provider: str = "",
        detail: str = "",
        checked_at_iso: str = "",
    ) -> None:
        self.state = state
        self.domain = domain
        self.provider = provider
        self.detail = detail
        self.checked_at_iso = checked_at_iso

    def to_dict(self) -> dict[str, str]:
        return {
            "state": self.state.value,
            "domain": self.domain,
            "provider": self.provider,
            "detail": self.detail,
            "checked_at": self.checked_at_iso,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"HealthProbe({self.domain}/{self.provider}={self.state.value})"
