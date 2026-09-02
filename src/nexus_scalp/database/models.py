"""
Database Migration Domain Models (TASK-10)
==========================================
Small, typed, SQLite-aware migration contracts. No heavy framework —
a deterministic in-house migration registry (task §56).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class DatabaseDomain(StrEnum):
    """Independent schema domains — each carries its own schema version."""

    AUDIT = "audit"
    NEWS = "news"
    CANDLE_INTEL = "candle_intel"


class MigrationRisk(StrEnum):
    """Risk classification for a migration (task §14)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    DESTRUCTIVE = "DESTRUCTIVE"


class TransactionKind(StrEnum):
    """SQLite transaction classification (task §8)."""

    TRANSACTIONAL = "TRANSACTIONAL"
    NON_TRANSACTIONAL_WITH_SAFETY_PROTOCOL = "NON_TRANSACTIONAL_WITH_SAFETY_PROTOCOL"


class MigrationStatus(StrEnum):
    """Persisted status of one migration in the history table."""

    APPLIED = "applied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class MigrationState(StrEnum):
    """Engine-level state machine (task §7)."""

    DB_MIGRATION_NOT_REQUIRED = "DB_MIGRATION_NOT_REQUIRED"
    DB_MIGRATION_PENDING = "DB_MIGRATION_PENDING"
    DB_MIGRATING = "DB_MIGRATING"
    DB_MIGRATION_SUCCEEDED = "DB_MIGRATION_SUCCEEDED"
    DB_MIGRATION_FAILED = "DB_MIGRATION_FAILED"
    DB_MIGRATION_ROLLBACK = "DB_MIGRATION_ROLLBACK"
    DB_CORRUPTED = "DB_CORRUPTED"
    DB_BLOCKED = "DB_BLOCKED"
    DB_DOWNGRADE_BLOCKED = "DB_DOWNGRADE_BLOCKED"
    DB_MIGRATION_IN_PROGRESS = "DB_MIGRATION_IN_PROGRESS"


@dataclass(frozen=True)
class Migration:
    """One immutable, versioned, checksummed migration operation.

    `apply` receives (connection, db_path) and performs the schema change.
    `verify` returns True when the change is present.
    `rollback` is the compensation strategy — required for DESTRUCTIVE or
    NON_TRANSACTIONAL migrations (may be None when impossible, in which case
    the migration records ROLLED_BACK with a manual-recovery note).
    """

    migration_id: str
    domain: DatabaseDomain
    from_version: int
    to_version: int
    description: str
    apply: Callable[[Any, Path], None]  # (conn, db_path)
    verify: Callable[[Any, Path], bool]  # (conn, db_path) -> present?
    risk: MigrationRisk = MigrationRisk.LOW
    transaction_kind: TransactionKind = TransactionKind.TRANSACTIONAL
    rollback: Callable[[Any, Path], None] | None = None
    backfill: Callable[[Any, Path], None] | None = None  # bounded/checkpointed

    def checksum(self) -> str:
        raw = (
            f"{self.migration_id}|{self.domain.value}|{self.from_version}|"
            f"{self.to_version}|{self.description}|{self.risk.value}|"
            f"{self.transaction_kind.value}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SchemaColumn:
    """Expected column contract for one table (manifest §13)."""

    name: str
    type: str = "TEXT"
    nullable: bool = True
    default: str | None = None


@dataclass(frozen=True)
class SchemaTable:
    """Expected table contract (minimal but typed).

    `full_contract` marks tables whose `columns` list is COMPLETE — only
    complete contracts participate in EXTRA_COLUMN drift detection. A minimal
    skeleton (baseline/migration targets) never flags the application's own
    canonical columns as unexpected drift (§12).
    """

    name: str
    columns: tuple[SchemaColumn, ...] = ()
    indexes: tuple[str, ...] = ()
    unique_indexes: tuple[str, ...] = ()
    full_contract: bool = False


@dataclass(frozen=True)
class SchemaManifest:
    """Machine-readable expected schema for one domain (task §13)."""

    database: DatabaseDomain
    schema_version: int
    tables: tuple[SchemaTable, ...] = ()

    def table_names(self) -> set[str]:
        return {t.name for t in self.tables}

    def column_names(self, table: str) -> set[str]:
        for t in self.tables:
            if t.name == table:
                return {c.name for c in t.columns}
        return set()

    def expected_indexes(self) -> set[str]:
        out: set[str] = set()
        for t in self.tables:
            out.update(t.indexes)
            out.update(t.unique_indexes)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database.value,
            "schema_version": self.schema_version,
            "tables": {
                t.name: {
                    "columns": [
                        {
                            "name": c.name,
                            "type": c.type,
                            "nullable": c.nullable,
                            "default": c.default,
                        }
                        for c in t.columns
                    ],
                    "indexes": list(t.indexes),
                    "unique_indexes": list(t.unique_indexes),
                }
                for t in self.tables
            },
        }


def migration_file_checksum(path: Any) -> str:
    """Deterministic checksum of a migration's source identity (task §41)."""
    raw = f"{path}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class MigrationResult:
    """Engine result for one migrate() run."""

    state: MigrationState = MigrationState.DB_MIGRATION_NOT_REQUIRED
    applied: list[str] = field(default_factory=list)
    current_version: int = 0
    expected_version: int = 0
    error: str = ""
    backup_path: str = ""
    integrity: str = "ok"
    duration_ms: float = 0.0
