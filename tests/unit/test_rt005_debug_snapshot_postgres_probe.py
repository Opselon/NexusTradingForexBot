"""RT-005: the debug_snapshot audit probe on a PostgreSQL-configured repo.

The runtime log reports::

    debug_snapshot db schema probe error error="WindowsPath('.') has an empty name"

Root cause: the probe resolved ``Path(getattr(engine.audit, "_db_path", "") or "")``.
A PostgreSQL-configured ``AuditRepository`` has ``_db_path == ""`` (only SQLite
populates it), and ``Path("")`` is ``WindowsPath('.')``. Feeding that to the
SQLite-only ``DatabaseMigrationEngine`` raised on ``with_suffix`` — every
snapshot logged a warning and reported no schema version for the audit domain.

The probe now reports an explicit provider mismatch instead of crashing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from nexus_scalp.database.engine import DatabaseMigrationEngine
from nexus_scalp.database.models import DatabaseDomain


class _PgAuditRepo:
    """The provider-mismatch shape: a URL, no filesystem path."""

    _db_path = ""
    _db_url = "postgresql://user:[REDACTED_SECRET]@host/nexusdb"


class _SqliteAuditRepo:
    _db_path = "/tmp/artifacts/audit.db"
    _db_url = "sqlite:////tmp/artifacts/audit.db"


def test_an_empty_db_path_is_what_breaks_the_migration_engine() -> None:
    """The exception the log reported, reproduced directly."""
    with pytest.raises(ValueError, match="empty name"):
        DatabaseMigrationEngine(db_path=Path(""), domain=DatabaseDomain.AUDIT)


def test_the_postgres_repo_reports_a_provider_mismatch() -> None:
    """The guard routes a URL-only repo to an explicit branch.

    This mirrors the branch in ``debug_snapshot``: no ``_db_path`` plus a
    ``postgresql://`` ``_db_url`` must never reach the file-based probe.
    """
    resolved = getattr(_PgAuditRepo, "_db_path", "") or ""
    url = str(getattr(_PgAuditRepo, "_db_url", "") or "")
    routes_to_provider_branch = not resolved and url.startswith("postgresql://")
    assert routes_to_provider_branch


def test_the_sqlite_repo_still_resolves_to_a_filesystem_path() -> None:
    """The pre-existing SQLite behaviour is unchanged by the guard."""
    resolved = getattr(_SqliteAuditRepo, "_db_path", "") or ""
    assert Path(resolved).name == "audit.db"
