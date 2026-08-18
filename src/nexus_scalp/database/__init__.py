"""
Nexus Scalp Engine — Database Migration & Schema Evolution (TASK-10)
====================================================================
Automatic, deterministic, idempotent schema migration for the independent
persistent domains (audit.db / news.db / candle_intel.db). No DB deletion,
no manual SQL, no data loss.

    models.py    typed migration/state/result contracts
    manifest.py  machine-readable expected schema per domain (§13)
    registry.py  canonical ordered migrations per domain (§3/§14)
    engine.py    DatabaseMigrationEngine: plan/apply/verify/backup/lock/drift
    gate.py      startup migration gate (§6/§7)
"""

from nexus_scalp.database.engine import DatabaseMigrationEngine, MigrationError
from nexus_scalp.database.gate import run_startup_migration_gate
from nexus_scalp.database.manifest import (
    AUDIT_MANIFEST,
    CANDLE_MANIFEST,
    NEWS_MANIFEST,
    manifest_for,
)
from nexus_scalp.database.models import (
    DatabaseDomain,
    Migration,
    MigrationResult,
    MigrationRisk,
    MigrationState,
    MigrationStatus,
    SchemaColumn,
    SchemaManifest,
    SchemaTable,
    TransactionKind,
)
from nexus_scalp.database.registry import (
    REGISTRY,
    expected_version_for_domain,
    migrations_for,
)

__all__ = [
    "AUDIT_MANIFEST",
    "CANDLE_MANIFEST",
    "NEWS_MANIFEST",
    "REGISTRY",
    "DatabaseDomain",
    "DatabaseMigrationEngine",
    "Migration",
    "MigrationError",
    "MigrationResult",
    "MigrationRisk",
    "MigrationState",
    "MigrationStatus",
    "SchemaColumn",
    "SchemaManifest",
    "SchemaTable",
    "TransactionKind",
    "expected_version_for_domain",
    "manifest_for",
    "migrations_for",
    "run_startup_migration_gate",
]
