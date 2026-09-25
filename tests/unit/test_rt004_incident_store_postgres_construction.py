"""RT-004: IncidentStore must construct against a PostgreSQL-configured repo.

The runtime log reports::

    [INCIDENT_WORKER] event=START_FAILED (isolated)
    error='IncidentStore requires db_path or audit_repo'

Root cause: ``_ensure_incident_worker`` reads ``getattr(self.audit,
"_db_path", "")``. A PostgreSQL-configured ``AuditRepository`` sets
``_db_path = ""`` (only SQLite populates it), so ``IncidentStore.__init__``
raised and the incident worker never started — production incidents were
not recorded at all.

The write path already worked (it queues through the repo), so the defect was
purely at construction. This test locks in that a repo with no ``_db_path``
but a real ``_db_url`` is accepted.

PG-DBPATH-BOOT-001: since PR #460 ``AuditRepository._db_path`` is no longer
empty under a non-SQLite provider — ``_provider_db_path()`` stores the
provider URI (``postgresql://localhost:5432/nexusdb``). The store must treat
that URI exactly like the empty string, otherwise it takes the SQLite branch
and ``ensure_schema`` dies in ``sqlite3.connect``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from typing import Any

import pytest

from nexus_scalp.incidents.store import IncidentStore


class _PgWritePlane:
    """Minimal pooled write plane: the shape the store resolves and calls."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str, args: Any = ()) -> None:
        self.executed.append(sql)


class _PgLikeRepo:
    """The provider-mismatch shape: a URL, no filesystem path."""

    def __init__(self, url: str = "postgresql://user:***@host/nexusdb") -> None:
        self._db_url = url
        self._is_sqlite = False
        self._db_path = ""
        self._write_backend = _PgWritePlane()

    def _build_pooled_write_backend(self) -> Any:
        return self._write_backend


class _SqliteLikeRepo:
    def __init__(self, path: str = "/tmp/incidents.db") -> None:
        self._db_url = f"sqlite:///{path}"
        self._is_sqlite = True
        self._db_path = path


def test_a_postgres_repo_with_no_db_path_is_accepted() -> None:
    """The exact state that raised ``requires db_path or audit_repo``."""
    repo = _PgLikeRepo()
    store = IncidentStore(db_path="", audit_repo=repo)
    assert store.db_path == ""
    # No SQLite path, so the provider-aware branch ran and owns the schema.
    assert store._write_backend is repo._write_backend
    assert repo._write_backend.executed, "ensure_schema ran on the write plane"


def test_a_sqlite_repo_still_resolves_to_a_filesystem_path() -> None:
    """The pre-existing SQLite behaviour is unchanged: the URL is not kept."""
    store = IncidentStore(db_path="", audit_repo=_SqliteLikeRepo())
    assert store.db_path.endswith("incidents.db")
    assert store._write_backend is None


def test_an_explicit_sqlite_path_wins_over_the_repo() -> None:
    store = IncidentStore(db_path="explicit.db", audit_repo=_PgLikeRepo())
    assert store.db_path == "explicit.db"
    assert store._write_backend is None


def test_a_postgres_repo_whose_db_path_is_the_provider_uri_is_accepted() -> None:
    """The live web-API site (``api_v1/incidents.py:54``): no explicit path."""
    repo = _PgLikeRepo()
    repo._db_path = "postgresql://localhost:5432/nexusdb"
    store = IncidentStore(audit_repo=repo)
    assert store.db_path == ""
    assert store._write_backend is repo._write_backend


def test_the_worker_call_sites_provider_uri_is_treated_as_absent() -> None:
    """``live_engine.py:2740`` passes the repo URI explicitly — same outcome."""
    repo = _PgLikeRepo()
    repo._db_path = "postgresql://localhost:5432/nexusdb"
    db_path = getattr(repo, "_db_path", "")
    store = IncidentStore(db_path=db_path, audit_repo=repo)
    assert store.db_path == ""
    assert store._write_backend is repo._write_backend


def test_no_path_and_no_repo_still_raises() -> None:
    """The guard is not weakened: an empty store must still fail loudly."""
    with pytest.raises(ValueError, match="requires db_path or audit_repo"):
        IncidentStore(db_path="", audit_repo=None)


def test_a_repo_with_neither_path_nor_url_raises() -> None:
    """A repo object that holds nothing is the same as no repo."""

    class Empty:
        _db_path = ""
        _db_url = ""

    with pytest.raises(ValueError, match="requires db_path or audit_repo"):
        IncidentStore(db_path="", audit_repo=Empty())
