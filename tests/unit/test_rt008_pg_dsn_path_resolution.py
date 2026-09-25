"""BUG-RT008: PG-DSN fed to sqlite3.connect killed every PG boot (2026-09-25).

Live failure, reproduced on the box with ``database.provider=postgresql``::

    FATAL ENGINE ERROR: unable to open database file
    e = OperationalError('unable to open database file')
    db_path = 'postgresql://localhost:5432/nexusdb'   <- SQLite got a DSN
    learning_cycle.py:101 in __init__ -> sqlite3.connect(self.db_path)

How it arose: RT-007 (#458) guarded ``_resolve_cycle_store_db_path`` on
``isinstance(raw, str) and raw.strip()``, *assuming* ``_db_path`` is empty
under a non-SQLite provider. PG-READ-PLANE-001 (#460) then introduced
``_provider_db_path()``, which keeps ``_db_path`` NON-empty under PostgreSQL
(a real server-side DSN, so ``Path(_db_path)`` probes stop collapsing to
``WindowsPath('.')`` — contract defect D9). The guard's assumption broke:
the DSN is a non-empty string, so it passed straight through to
``sqlite3.connect`` and the engine could not boot at all.

This suite locks the resolution order: the non-SQLite branch wins over the
raw-string branch. All four resolutions here are what actually ships now.
"""

from __future__ import annotations

import sqlite3

from nexus_scalp.model_lifecycle.learning_loop import (
    _audit_repo_is_nonsqlite,
    _cycle_store_workspace_path,
    _resolve_cycle_store_db_path,
)


class _PgAuditRepoWithDsn:
    """The REAL audit-repo shape under PostgreSQL, post PG-READ-PLANE-001.

    ``_provider_db_path()`` populates ``_db_path`` with the live DSN so
    observability probes (debug_snapshot's schema probe) build a ``Path``
    that names the real database instead of ``.``. This is exactly the
    object the crash log showed in the crash log.
    """

    _db_url = "postgresql://localhost:5432/nexusdb"
    _is_sqlite = False
    _db_path = "postgresql://localhost:5432/nexusdb"


class _SqliteAuditRepo:
    """Production SQLite audit repo: ``_db_path`` is a real file path."""

    _db_url = "sqlite:///artifacts/audit.db"
    _is_sqlite = True
    _db_path = "artifacts/audit.db"


def _assert_equal(actual, expected) -> None:
    assert actual == expected, f"expected {expected!r}, got {actual!r}"


def test_rt008_non_sqlite_repo_detected() -> None:
    # The flag PG-READ-PLANE-001 carries — must read True for the PG repo.
    assert _audit_repo_is_nonsqlite(_PgAuditRepoWithDsn()) is True
    assert _audit_repo_is_nonsqlite(_SqliteAuditRepo()) is False


def test_rt008_pg_dsn_is_never_handed_to_sqlite() -> None:
    # THE crash: a non-empty PG DSN reached sqlite3.connect. It must not.
    resolved = _resolve_cycle_store_db_path(_PgAuditRepoWithDsn())
    assert not resolved.startswith(("postgresql://", "postgres://")), (
        f"BUG-RT008 regression: a PG DSN was returned for a SQLite path: {resolved}"
    )
    assert resolved != ":memory:", "PG state must be durable, not in-memory"


def test_rt008_pg_resolves_to_a_real_workspace_file() -> None:
    # RT-007's durability intent survives: a real file the store reopens.
    resolved = _resolve_cycle_store_db_path(_PgAuditRepoWithDsn())
    _assert_equal(resolved, _cycle_store_workspace_path())
    # The file must be openable by SQLite exactly as LearningCycleStore does.
    conn = sqlite3.connect(resolved, timeout=5.0)
    conn.close()


def test_rt008_sqlite_path_is_unchanged() -> None:
    # No regression on the production SQLite path — still returns it verbatim.
    resolved = _resolve_cycle_store_db_path(_SqliteAuditRepo())
    _assert_equal(resolved, "artifacts/audit.db")
