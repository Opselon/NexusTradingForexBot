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
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from nexus_scalp.incidents.store import IncidentStore


class _PgLikeRepo:
    """The provider-mismatch shape: a URL, no filesystem path."""

    def __init__(self, url: str = "postgresql://user:[REDACTED_SECRET]@host/nexusdb") -> None:
        self._db_url = url
        self._is_sqlite = False
        self._db_path = ""


class _SqliteLikeRepo:
    def __init__(self, path: str = "/tmp/incidents.db") -> None:
        self._db_url = f"sqlite:///{path}"
        self._is_sqlite = True
        self._db_path = path


def test_a_postgres_repo_with_no_db_path_is_accepted() -> None:
    """The exact state that raised ``requires db_path or audit_repo``."""
    store = IncidentStore(db_path="", audit_repo=_PgLikeRepo())
    assert store.db_url.startswith("postgresql://")
    assert store.db_path == ""


def test_a_sqlite_repo_still_resolves_to_a_filesystem_path() -> None:
    """The pre-existing SQLite behaviour is unchanged: the URL is not kept."""
    store = IncidentStore(db_path="", audit_repo=_SqliteLikeRepo())
    assert store.db_path.endswith("incidents.db")
    assert store.db_url == ""


def test_an_explicit_sqlite_path_wins_over_the_repo() -> None:
    store = IncidentStore(db_path="explicit.db", audit_repo=_PgLikeRepo())
    assert store.db_path == "explicit.db"
    assert store.db_url == ""


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
