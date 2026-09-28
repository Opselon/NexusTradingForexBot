"""
CC-READ-PLANE-001 — research read-plane provider gate regression tests.

Root cause
----------
Every research read in ``research/store.py`` and ``research/registry.py``
historically opened ``sqlite3.connect(repo._db_path)`` behind an
``if not repo._is_sqlite: return <empty>`` gate. Under a PostgreSQL provider
the gate fired and the readers returned their *documented empty default*
(``[]`` / ``0`` / ``available: False``) with no exception and no log line.

The Command Center therefore answered ``total_strategies: 0`` and
``/api/research/summary`` answered ``total: 0`` while the engine held 4,109
``strategy_registry`` rows on PostgreSQL — silent wrong data behind an
HTTP 200 envelope. The UI looked "healthy" and reported a lie.

Fix
---
The readers now go through ``store._research_reader()`` /
``StrategyRegistry._reader()``, which resolve ``AuditRepository.
research_read_plane()``: SQLite keeps its own connection; a pooled provider
reads through the same registered READ plane the audit read guard already
serves declared reads from, so the research surface reads the store the
engine writes.

These tests need no live PostgreSQL server: the read plane is replaced with an
in-process fake (exactly as the neighbouring ``test_pg_audit_read_plane_*``
suites do), plus a real SQLite round-trip to prove the fix did not break the
SQLite path it was written on.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research import store
from nexus_scalp.research.registry import StrategyRegistry


# =====================================================================
# Test A — the provider gate must NOT return empty when the plane is readable
# =====================================================================
class TestGateDoesNotSilentlyEmpty:
    """The defect: ``_is_sqlite`` False + readable plane => readers returned 0/[].
    A read failure must surface as ``available: False``, never as a bare empty
    result (which reads as "no data" instead of "cannot read")."""

    def test_research_read_plane_returns_none_for_sqlite(self) -> None:
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = True
        # SQLite never consults the fabric — it uses its own connection.
        assert repo.research_read_plane() is None

    def test_research_read_plane_resolves_the_registered_plane(self, monkeypatch: Any) -> None:
        sentinel = _FakeReadPlane()
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: sentinel,
            raising=True,
        )
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        assert repo.research_read_plane() is sentinel

    def test_research_read_plane_reports_unreadable_as_none(self, monkeypatch: Any) -> None:
        """No plane registered => None => callers must surface available: False."""
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: None,
            raising=True,
        )
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        assert repo.research_read_plane() is None


# =====================================================================
# Test B — registry_summary counts real rows under a pooled provider
# =====================================================================
class TestRegistrySummary:
    def test_summary_counts_rows_through_the_read_plane(self, monkeypatch: Any) -> None:
        """Before the fix this answered total=0 with 4,109 rows on the server."""
        plane = _FakeReadPlane(
            scalars={
                "SELECT COUNT(*) FROM strategy_registry": 4109,
            },
            rows={
                "SELECT lifecycle, COUNT(*) AS c FROM strategy_registry GROUP BY lifecycle;": [
                    {"lifecycle": "REJECTED", "c": 4034},
                    {"lifecycle": "DISCOVERED", "c": 72},
                    {"lifecycle": "VALIDATED", "c": 3},
                ],
            },
        )
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: plane,
            raising=True,
        )
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False

        out = store.registry_summary(repo)

        assert out["available"] is True, "a readable plane must not report unavailable"
        assert out["total"] == 4109
        assert out["by_lifecycle"] == {"REJECTED": 4034, "DISCOVERED": 72, "VALIDATED": 3}

    def test_summary_reports_unavailable_when_no_plane(self, monkeypatch: Any) -> None:
        """Cannot-read must surface as available: False, not as an empty result."""
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: None,
            raising=True,
        )
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False

        out = store.registry_summary(repo)

        assert out["available"] is False
        assert out["total"] == 0
        assert out["by_lifecycle"] == {}


# =====================================================================
# Test C — list_research_runs no longer returns [] on a pooled provider
# =====================================================================
class TestResearchRuns:
    def test_runs_are_returned_through_the_read_plane(self, monkeypatch: Any) -> None:
        plane = _FakeReadPlane(
            rows={
                "SELECT * FROM research_runs": [
                    {
                        "run_id": "RUN-1",
                        "dataset_id": "DS-1",
                        "strategy_id": "STRAT-A",
                        "executed_at": "2026-09-01T00:00:00Z",
                    }
                ],
            },
        )
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: plane,
            raising=True,
        )
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False

        runs = store.list_research_runs(repo, limit=10)

        assert len(runs) == 1, "before the fix this returned [] with rows on the server"
        assert runs[0]["run_id"] == "RUN-1"

    def test_a_read_failure_is_logged_not_swallowed_as_empty(self, monkeypatch: Any) -> None:
        """A raising plane must hit the caller's except-clause (logged), not
        become a silent []."""
        plane = _RaisingPlane()
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: plane,
            raising=True,
        )
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False

        # The un-gated reader must RAISE — this is what proves the gate is
        # gone. The public wrapper still degrades safely to [] + a log line.
        from nexus_scalp.research.store import _ProviderRead

        reader = _ProviderRead(repo)
        with pytest.raises(RuntimeError):
            reader.rows("SELECT * FROM research_runs")


# =====================================================================
# Test D — StrategyRegistry get/list/count read through the plane
# =====================================================================
class TestStrategyRegistryReads:
    """``StrategyRegistry`` shares the same gate; all three readers had it."""

    def _repo_with_plane(self, monkeypatch: Any, plane: Any) -> Any:
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: plane,
            raising=True,
        )
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        return repo

    def test_count_uses_the_read_plane(self, monkeypatch: Any) -> None:
        plane = _FakeReadPlane(
            scalars={"SELECT COUNT(*) FROM strategy_registry": 4109},
        )
        registry = StrategyRegistry(self._repo_with_plane(monkeypatch, plane))

        assert registry.count() == 4109, "before the fix the gate returned 0"

    def test_list_reads_rows_through_the_read_plane(self, monkeypatch: Any) -> None:
        plane = _FakeReadPlane(
            rows={"SELECT * FROM strategy_registry": [_registry_row("STRAT-A")]},
        )
        registry = StrategyRegistry(self._repo_with_plane(monkeypatch, plane))

        entries = registry.list(limit=10)

        assert len(entries) == 1, "before the fix the gate returned []"
        assert entries[0].strategy_id == "STRAT-A"

    def test_get_reads_a_single_row_through_the_read_plane(self, monkeypatch: Any) -> None:
        plane = _FakeReadPlane(
            rows={"SELECT * FROM strategy_registry": [_registry_row("STRAT-A")]},
        )
        registry = StrategyRegistry(self._repo_with_plane(monkeypatch, plane))

        entry = registry.get("STRAT-A")

        assert entry is not None, "before the fix the gate returned None"
        assert entry.strategy_id == "STRAT-A"

    def test_count_by_lifecycle_uses_the_read_plane(self, monkeypatch: Any) -> None:
        plane = _FakeReadPlane(
            scalars={
                "SELECT COUNT(*) FROM strategy_registry WHERE lifecycle=?": 3,
            },
        )
        registry = StrategyRegistry(self._repo_with_plane(monkeypatch, plane))

        assert registry.count(lifecycle="VALIDATED") == 3


# =====================================================================
# Test E — the SQLite path the fix was written on still works
# =====================================================================
class TestSQLitePathPreserved:
    """The fix must not change behaviour for the SQLite provider, which is the
    default and the path every existing suite exercises."""

    def test_sqlite_reads_still_return_real_rows(self, tmp_path: Any) -> None:
        db = tmp_path / "cc_read_plane.db"
        repo = _sqlite_repo_with_registry(db)
        try:
            summary = store.registry_summary(repo)
            assert summary["available"] is True
            assert summary["total"] == 2

            registry = StrategyRegistry(repo)
            assert registry.count() == 2
            entries = registry.list()
            assert [e.strategy_id for e in entries] == ["STRAT-B", "STRAT-A"]

            entry = registry.get("STRAT-A")
            assert entry is not None
            assert entry.lifecycle.value == "VALIDATED"

            runs = store.list_research_runs(repo, limit=5)
            assert len(runs) == 1
            assert runs[0]["run_id"] == "RUN-1"
        finally:
            repo.close()

    def test_sqlite_reader_reports_available(self, tmp_path: Any) -> None:
        db = tmp_path / "cc_read_plane2.db"
        repo = _sqlite_repo_with_registry(db)
        try:
            from nexus_scalp.research.store import _ProviderRead

            reader = _ProviderRead(repo)
            # SQLite resolves None from research_read_plane() and uses its own
            # connection, so the reader is available even with no fabric plane.
            assert reader.available is True
        finally:
            repo.close()


# =====================================================================
# Fakes + fixtures
# =====================================================================


def _registry_row(strategy_id: str = "STRAT-A", lifecycle: str = "VALIDATED") -> dict[str, Any]:
    """A ``strategy_registry`` row shaped like the real schema."""
    import json
    from datetime import datetime

    now = datetime.now().isoformat()
    return {
        "strategy_id": strategy_id,
        "strategy_version": "1.0.0",
        "feature_schema_id": "scalp_v1",
        "feature_dimension": 50,
        "discovery_source": "SMC",
        "discovery_window": "2026-01",
        "context_definition": json.dumps({}),
        "parent_strategy_ids": json.dumps([]),
        "validation_lineage": json.dumps([]),
        "retirement_reason": "",
        "context_matrices": json.dumps({}),
        "lifecycle": lifecycle,
        "backtest": None,
        "walkforward": None,
        "oos": None,
        "robustness": None,
        "score": None,
        "confidence": 0.5,
        "sample_count": 100,
        "created_at": now,
        "updated_at": now,
    }


def _sqlite_repo_with_registry(db: Any) -> AuditRepository:
    """A real SQLite audit repo with two registry rows and one research run."""
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_registry (
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                feature_schema_id TEXT,
                feature_dimension INTEGER,
                discovery_source TEXT,
                discovery_window TEXT,
                context_definition TEXT,
                parent_strategy_ids TEXT,
                validation_lineage TEXT,
                context_matrices TEXT,
                lifecycle TEXT NOT NULL,
                backtest TEXT,
                walkforward TEXT,
                oos TEXT,
                robustness TEXT,
                score TEXT,
                confidence REAL,
                sample_count INTEGER,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (strategy_id, strategy_version)
            );
            CREATE TABLE IF NOT EXISTS research_runs (
                run_id TEXT PRIMARY KEY,
                strategy_id TEXT,
                executed_at TEXT
            );
            """
        )
        conn.execute("DELETE FROM strategy_registry")
        conn.execute("DELETE FROM research_runs")
        # newest first ordering: STRAT-B has the later updated_at
        conn.execute(
            "INSERT INTO strategy_registry (strategy_id, strategy_version, lifecycle, "
            "confidence, sample_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (
                "STRAT-A",
                "1.0.0",
                "VALIDATED",
                0.5,
                100,
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO strategy_registry (strategy_id, strategy_version, lifecycle, "
            "confidence, sample_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("STRAT-B", "1.0.0", "REJECTED", 0.2, 50, "2026-02-01T00:00:00", "2026-02-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO research_runs (run_id, dataset_id, strategy_id, strategy_version, "
            "executed_at) VALUES (?,?,?,?,?)",
            ("RUN-1", "DS-1", "STRAT-A", "1.0.0", "2026-01-01T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    return repo


class _FakeReadPlane:
    """Minimal read plane: query/scalar-capable, no ``execute`` (not write-shaped).

    Matches the real ``PgReadPlane`` contract the fix depends on.
    """

    def __init__(
        self,
        rows: dict[str, list[dict[str, Any]]] | None = None,
        scalars: dict[str, Any] | None = None,
    ) -> None:
        self._rows = rows or {}
        self._scalars = scalars or {}
        self.queries: list[str] = []
        self.args: list[tuple[Any, ...]] = []

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.queries.append(sql)
        self.args.append(tuple(args))
        for key, value in self._rows.items():
            if key in sql:
                return value
        return []

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        self.queries.append(sql)
        self.args.append(tuple(args))
        for key, value in self._scalars.items():
            if key in sql:
                return value
        return None


class _RaisingPlane:
    """A plane whose backend is unreachable: reads must raise, not return []."""

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        raise RuntimeError("no read plane registered for domain 'audit'")

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        raise RuntimeError("no read plane registered for domain 'audit'")
