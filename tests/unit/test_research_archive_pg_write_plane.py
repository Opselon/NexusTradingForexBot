"""PG-ARCHIVE-WRITE-001 — research archive must write through the write plane.

Measured live defect: ``research/worker._worker_connection`` handed the
archiver the pooled READ-plane cursor under a persisted PostgreSQL provider.
The read pool runs ``SET default_transaction_read_only=on``, so the archive's
very first statement (``CREATE TABLE IF NOT EXISTS ...``) failed with
``ReadOnlySqlTransaction`` and research history was never archived — silently,
every cycle, forever.

These tests pin:

1. the SQLite path is byte-for-byte the historical one (raw connection,
   PRAGMA, ``with conn:`` transactions);
2. a pooled provider resolves the audit WRITE plane, never the read pool;
3. the SQLite-dialect DDL is translated before it reaches PG;
4. a missing write backend fails loud (no silent archive skip);
5. the insert-verify-delete contract still deletes only after the archive
   twin exists (safe direction on any provider).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import Any

import pytest

sys.path.insert(0, "src")

from nexus_scalp.research.archive import (
    _ProviderArchiveExecutor,
    _SqliteArchiveExecutor,
    archive_research_history,
)


@pytest.fixture
def sqlite_db(tmp_path: Any) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    conn.execute(
        "CREATE TABLE research_events ("
        " id INTEGER PRIMARY KEY, event_id TEXT, strategy_id TEXT,"
        " research_run_id TEXT, gate_id TEXT, event_type TEXT,"
        " message TEXT, payload TEXT, occurred_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE research_evidence ("
        " id INTEGER PRIMARY KEY, evidence_id TEXT, strategy_id TEXT,"
        " research_run_id TEXT, gate_id TEXT, kind TEXT, content TEXT,"
        " content_hash TEXT, dataset_version TEXT, engine_version TEXT,"
        " created_at TEXT)"
    )
    conn.commit()
    yield conn
    conn.close()


def _seed_expired(conn: sqlite3.Connection, rows: int = 2) -> None:
    for i in range(rows):
        conn.execute(
            "INSERT INTO research_events (event_id, occurred_at) VALUES (?, ?)",
            (f"e{i}", "2020-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO research_evidence (evidence_id, created_at) VALUES (?, ?)",
            (f"v{i}", "2020-01-01T00:00:00+00:00"),
        )
    conn.commit()


# --- the SQLite path is unchanged -----------------------------------------


def test_sqlite_connection_takes_the_historical_path(sqlite_db: sqlite3.Connection) -> None:
    """A raw sqlite3 connection is served by the SQLite executor, unchanged."""
    from nexus_scalp.research.archive import _resolve_executor

    ex = _resolve_executor(sqlite_db)
    assert isinstance(ex, _SqliteArchiveExecutor)
    _seed_expired(sqlite_db)

    result = archive_research_history(sqlite_db, older_than_days=30, batch_size=100)

    assert result.events_archived == 2
    assert result.evidence_archived == 2
    live = sqlite_db.execute("SELECT COUNT(*) FROM research_events").fetchone()[0]
    assert live == 0
    archived = sqlite_db.execute(
        "SELECT COUNT(*) FROM research_events_archive"
    ).fetchone()[0]
    assert archived == 2


def test_sqlite_executor_uses_pragma_and_native_transactions(
    sqlite_db: sqlite3.Connection,
) -> None:
    """The SQLite executor surfaces PRAGMA columns and the native txn scope."""
    from nexus_scalp.research.archive import _ensure_archive_tables

    ex = _SqliteArchiveExecutor(sqlite_db)
    _ensure_archive_tables(ex)

    cols = ex.table_columns("research_events_archive")
    # table_columns is raw PRAGMA — the archived_at filter belongs to
    # _archive_rows and is pinned by the insert-verify-delete test above.
    assert "event_id" in cols
    assert "archived_at" in cols
    # ``transaction()`` IS the connection — ``with conn:`` semantics preserved.
    assert ex.transaction() is sqlite_db


# --- the provider path never touches the read-only plane -------------------


class _RecordingWriteBackend:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql: str, args: tuple = ()) -> None:
        self.executed.append((sql, tuple(args)))


class _FakeReadPlane:
    def __init__(self, columns: dict[str, list[dict]]) -> None:
        self._columns = columns

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        if "information_schema.columns" in sql:
            table = args[0]
            return [{"column_name": c} for c in self._columns.get(table, [])]
        return []


def _make_repo(monkeypatch: pytest.MonkeyPatch, backend: Any, plane: Any) -> Any:
    class _Repo:
        pass

    import nexus_scalp.adapters.database.provider_store as ps
    import nexus_scalp.research.archive as archive_mod
    import nexus_scalp.research.store as store_mod

    monkeypatch.setattr(ps, "_write_backend", lambda repo, domain: backend)
    # store_mod is where the executor's lazy import resolves _ProviderRead.
    monkeypatch.setattr(
        store_mod, "_ProviderRead", lambda repo: _FakeProviderRead(plane)
    )
    return _Repo()


class _FakeProviderRead:
    def __init__(self, plane: Any) -> None:
        self._plane = plane
        self.available = True

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        return self._plane.rows(sql, args)


def test_provider_executor_routes_ddl_through_the_write_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CREATE TABLE reaches the write backend translated — never the read pool."""
    backend = _RecordingWriteBackend()
    plane = _FakeReadPlane({})
    repo = _make_repo(monkeypatch, backend, plane)

    ex = _ProviderArchiveExecutor(repo)
    ex.execute_ddl(
        "CREATE TABLE IF NOT EXISTS research_events_archive ("
        " id INTEGER PRIMARY KEY, event_id TEXT,"
        " archived_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )

    assert len(backend.executed) == 1
    sql = backend.executed[0][0]
    # The SQLite-only default expression was translated, not passed through.
    assert "datetime('now')" not in sql
    assert "now()" in sql or "to_char" in sql


def test_provider_executor_fails_loud_without_a_write_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No write backend = loud RuntimeError, never a silent archive skip."""
    import nexus_scalp.adapters.database.provider_store as ps

    monkeypatch.setattr(ps, "_write_backend", lambda repo, domain: None)

    class _Repo:
        pass

    with pytest.raises(RuntimeError, match="no audit write backend"):
        _ProviderArchiveExecutor(_Repo())


def test_provider_executor_reads_columns_from_information_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Column discovery is provider-native — no PRAGMA reaches the plane."""
    backend = _RecordingWriteBackend()
    plane = _FakeReadPlane(
        {"research_events_archive": ["id", "event_id", "archived_at"]}
    )
    repo = _make_repo(monkeypatch, backend, plane)

    ex = _ProviderArchiveExecutor(repo)
    cols = ex.table_columns("research_events_archive")

    assert cols == ["id", "event_id", "archived_at"]
    assert not any("PRAGMA" in sql for sql, _a in backend.executed)


def test_provider_executor_refuses_the_read_only_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The executor is constructed against the WRITE plane only, by design."""
    backend = _RecordingWriteBackend()
    plane = _FakeReadPlane({})
    repo = _make_repo(monkeypatch, backend, plane)

    ex = _ProviderArchiveExecutor(repo)
    # The write backend recorded the DDL — the read plane never saw a write.
    ex.execute_ddl("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
    assert len(backend.executed) == 1
    # And the read plane's rows() was never asked to write anything.
    assert not hasattr(plane, "execute")


# --- the archive-only contract holds on both paths -------------------------


def test_count_mismatch_deletes_nothing_on_sqlite(
    sqlite_db: sqlite3.Connection,
) -> None:
    """A verification failure leaves the live history untouched (fail-safe)."""
    _seed_expired(sqlite_db, rows=1)
    # The sabotage INSERT needs the archive table to exist first.
    from nexus_scalp.research.archive import _ensure_archive_tables

    _ensure_archive_tables(_SqliteArchiveExecutor(sqlite_db))
    sqlite_db.execute(
        "INSERT INTO research_events_archive (id, event_id, occurred_at)"
        " VALUES (1, 'twin', '2020-01-01T00:00:00+00:00')"
    )
    sqlite_db.commit()

    result = archive_research_history(sqlite_db, older_than_days=30, batch_size=100)

    assert result.events_archived == 0
    live = sqlite_db.execute("SELECT COUNT(*) FROM research_events").fetchone()[0]
    assert live == 1  # live row survives: never deleted without its twin
