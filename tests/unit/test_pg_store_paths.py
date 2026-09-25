"""PG-READ-PLANE-001/Lane D — provider-aware store paths.

Covers the two defects the lane owns:

* D5 — ``IncidentStore(db_path="", audit_repo=<non-SQLite repo>)`` used to
  raise ``IncidentStore requires db_path or audit_repo`` every 60s, so
  incident response never ran. It must now resolve the audit domain's
  pooled fabric planes and persist/read through them.
* D4 — ``_resolve_cycle_store_db_path`` returned ``:memory:`` under a
  non-SQLite provider, so the learning cycle store ran on a throwaway DB
  (``no such table: learning_cycles`` on every boot, state lost restart).

The SQLite paths stay byte-for-byte: every SQLite assertion here exercises
the unchanged branch and must keep passing.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.incidents.models import (
    EventSource,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    QuarantineEntry,
    TimelineEvent,
    ValueTrace,
)
from nexus_scalp.incidents.store import (
    INCIDENT_DDL,
    IncidentStore,
    _resolve_audit_backends,
)
from nexus_scalp.model_lifecycle.learning_loop import (
    _audit_repo_is_nonsqlite,
    _cycle_store_workspace_path,
    _resolve_cycle_store_db_path,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeReadPlane:
    """Read-side stand-in: the shape ``_registered_audit_read_plane`` accepts.

    Mirrors ``PgReadPlane`` (``query`` / ``scalar``, no ``execute``) so the
    store's read-plane refusal contract (reject a write-shaped backend) is
    exercised by the real code path.
    """

    def __init__(self, backend: FakePgBackend) -> None:
        self._backend = backend

    def query(self, sql: str, args: Any = ()) -> list[dict[str, Any]]:
        return self._backend.query(sql, args)

    def query_one(self, sql: str, args: Any = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: Any = ()) -> Any:
        return self._backend.scalar(sql, args)


class FakePgBackend:
    """A pooled-write-plane stand-in over a real SQLite file.

    Speaks the pooled plane's contract only: ``execute(sql, flat_args)`` with
    ``?`` placeholders, which it translates itself (like the real driver
    boundary). This keeps the test hermetic — no live PostgreSQL, no second
    DSN — while proving the store routes through the resolved backend and
    emits portable SQL.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path) as conn:
            for ddl in INCIDENT_DDL:
                conn.execute(ddl)

    # -- pooled write plane surface ------------------------------------

    def execute(self, sql: str, args: Any = ()) -> None:
        flat = tuple(args)
        self.executed.append((sql, flat))
        with sqlite3.connect(self.path) as conn:
            conn.execute(self._translate(sql), flat)

    def execute_batch(self, statements: Any) -> None:  # pragma: no cover
        raise AssertionError("Lane D save path must not batch")

    # -- pooled read plane surface (read side of the resolved backend) ----
    # The store reads via the same resolved backend: query/query_one/scalar
    # return dicts (rows) so dict-key access works exactly as on a real
    # pooled plane. ``?`` placeholders are translated exactly like execute().

    def query(self, sql: str, args: Any = ()) -> list[dict[str, Any]]:
        flat = tuple(args)
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(self._translate(sql), flat).fetchall()]

    def query_one(self, sql: str, args: Any = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: Any = ()) -> Any:
        flat = tuple(args)
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(self._translate(sql), flat).fetchone()
        return row[0] if row is not None else None

    def close(self) -> None:
        return None

    @staticmethod
    def _translate(sql: str) -> str:
        # The real driver boundary rewrites qmark placeholders; emulate it so
        # statements the store emits are validated as qmark-shaped. This fake
        # is SQLite-backed, so it also rewrites the portable standard spellings
        # the non-SQLite code path emits back to SQLite's own — exactly the
        # translation the real pooled boundary performs. SQLite rejects a
        # separator with DISTINCT (``GROUP_CONCAT(DISTINCT x, ',')`` is a
        # syntax error there), so the separator argument is dropped: it is
        # exactly the ',' STRING_AGG was asked for and SQLite's own default
        # separator is the same comma.
        out = sql.replace("?", "?")
        while "STRING_AGG(DISTINCT " in out:
            head, _, rest = out.partition("STRING_AGG(DISTINCT ")
            arg, _, tail = rest.partition(")")
            # ``arg`` is ``category, ','`` — keep only the first (DISTINCT
            # already collapses duplicates, so the separator is cosmetic).
            distinct_arg = arg.split(",")[0].strip()
            out = f"{head}GROUP_CONCAT(DISTINCT {distinct_arg}){tail}"
        return out


class FakeAuditRepo:
    """Minimal non-SQLite audit repo double (``_db_path == ""`` under PG)."""

    def __init__(self, backend: Any, read_backend: Any = None) -> None:
        self._is_sqlite = False
        self._db_path = ""
        self._write_backend = backend
        self._read_backend = read_backend

    # The store composes the repo's own resolution seams.
    def _build_pooled_write_backend(self) -> Any:
        return self._write_backend

    def _registered_audit_read_plane(self) -> Any:
        # Mirrors the real AuditRepository rule exactly: a read plane must
        # expose query/scalar and never execute, or it is refused. This
        # matters because a FakeWriteShapedReadPlane is how the tests assert
        # the refusal contract — returning it verbatim would defeat it.
        backend = self._read_backend
        if backend is None or hasattr(backend, "execute") or not hasattr(backend, "query"):
            return None
        return backend


class FakeAuditRepoWithUri(FakeAuditRepo):
    """The post-PG-READ-PLANE-001 shape: ``_db_path`` holds the provider URI."""

    def __init__(self, backend: Any, read_backend: Any = None) -> None:
        super().__init__(backend, read_backend)
        self._db_path = "postgresql://localhost:5432/nexusdb"


class FakeAuditRepoNoResolver:
    """A non-SQLite repo exposing neither resolution seam.

    The store must fall back to the fabric accessor itself and still fail
    loudly when nothing is registered.
    """

    def __init__(self) -> None:
        self._is_sqlite = False
        self._db_path = ""


class FakeWriteShapedReadPlane:
    """Write-shaped object: the store must refuse it as a read plane."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def execute(self, sql: str, args: Any = ()) -> None:  # pragma: no cover
        self._backend.execute(sql, args)


# ---------------------------------------------------------------------------
# Incident model fixture
# ---------------------------------------------------------------------------


def _sample_incident(incident_id: str = "INC-PG-001") -> Incident:
    return Incident(
        incident_id=incident_id,
        detected_at=datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC),
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN,
        component="risk_engine",
        operation="check_drawdown",
        correlation_id="COR-1",
        fingerprint="FP-1",
        tags=["money-path"],
        timeline=[
            TimelineEvent(
                timestamp=datetime(2026, 9, 24, 10, 1, 0, tzinfo=UTC),
                event_type="DETECTED",
                source=EventSource.RUNTIME,
                payload={"k": "v"},
                correlation_id="COR-1",
            )
        ],
        value_traces=[ValueTrace(field="equity", source="ledger")],
        quarantine_entries=[
            QuarantineEntry(
                target_table="orders",
                record_key="ORD-9",
                status="HELD",
                reason="stale equity",
                incident_id=incident_id,
                evidence="probe",
                quarantined_at=datetime(2026, 9, 24, 10, 2, 0, tzinfo=UTC),
            )
        ],
    )


# ---------------------------------------------------------------------------
# D5: IncidentStore resolves a real backend under a non-SQLite provider
# ---------------------------------------------------------------------------


def test_incident_store_constructs_over_fabric_planes_when_db_path_empty(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    repo = FakeAuditRepo(backend, FakeReadPlane(backend))

    store = IncidentStore(audit_repo=repo)

    assert store.db_path == ""
    assert store._write_backend is backend
    assert store._read_backend is not None


def test_incident_store_still_requires_a_path_or_backend(tmp_path: Path) -> None:
    # No db_path, no audit repo: unchanged contract.
    with pytest.raises(ValueError, match="requires db_path or audit_repo"):
        IncidentStore()

    # A repo with no resolvable backend: fail loudly, never silently succeed.
    with pytest.raises(ValueError, match="requires db_path or audit_repo"):
        IncidentStore(audit_repo=FakeAuditRepoNoResolver())


def test_incident_store_write_and_read_roundtrip_through_write_plane(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    store = IncidentStore(audit_repo=FakeAuditRepo(backend, FakeReadPlane(backend)))

    incident = _sample_incident()
    assert store.save(incident) == "INC-PG-001"

    # The store bypassed the queue (no _queue on the fake repo) and went
    # straight to the pooled write plane.
    assert backend.executed, "save must reach the write plane"
    assert any("INSERT INTO incidents" in sql for sql, _ in backend.executed)
    assert any("INSERT INTO incident_events" in sql for sql, _ in backend.executed)
    assert any("INSERT INTO incident_value_traces" in sql for sql, _ in backend.executed)
    assert any("INSERT INTO incident_quarantine" in sql for sql, _ in backend.executed)

    # Read-back through the read plane.
    got = store.get("INC-PG-001")
    assert got is not None
    assert got.component == "risk_engine"
    assert got.severity == IncidentSeverity.HIGH
    assert len(got.timeline) == 1
    assert got.timeline[0].event_type == "DETECTED"
    assert len(got.value_traces) == 1
    assert got.value_traces[0].field == "equity"
    assert len(got.quarantine_entries) == 1
    assert got.quarantine_entries[0].target_table == "orders"


def test_incident_store_upsert_is_idempotent_under_write_plane(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    store = IncidentStore(audit_repo=FakeAuditRepo(backend, FakeReadPlane(backend)))

    incident = _sample_incident()
    store.save(incident)
    store.save(incident)

    assert store.count()["total"] == 1
    assert store.get("INC-PG-001") is not None


def test_incident_store_quarantine_upsert_conflicts_on_unique(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    store = IncidentStore(audit_repo=FakeAuditRepo(backend, FakeReadPlane(backend)))

    store.save(_sample_incident())
    # Same (incident_id, target_table, record_key) — must UPDATE, not duplicate.
    store.save(_sample_incident())

    rows = store._query("SELECT COUNT(*) AS n FROM incident_quarantine", ())
    assert int(rows[0]["n"]) == 1


def test_incident_store_list_count_search_through_read_plane(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    store = IncidentStore(audit_repo=FakeAuditRepo(backend, FakeReadPlane(backend)))

    store.save(_sample_incident("INC-A"))
    store.save(_sample_incident("INC-B"))

    listed = store.list_incidents(limit=10)
    assert {i.incident_id for i in listed} == {"INC-A", "INC-B"}

    counts = store.count()
    assert counts["total"] == 2
    assert counts["high"] == 2
    assert counts["open"] == 2

    found = store.search("risk_engine")
    assert len(found) == 2

    by_component = store.stats_by_component()
    assert by_component and by_component[0]["component"] == "risk_engine"


def test_incident_store_delete_by_id_uses_explicit_existence_probe(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    store = IncidentStore(audit_repo=FakeAuditRepo(backend, FakeReadPlane(backend)))

    assert store.delete_by_id("INC-MISSING") is False
    store.save(_sample_incident())
    assert store.delete_by_id("INC-PG-001") is True
    assert store.get("INC-PG-001") is None
    assert store.count()["total"] == 0


def test_incident_store_recurring_fingerprints_uses_provider_spellings(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    store = IncidentStore(audit_repo=FakeAuditRepo(backend, FakeReadPlane(backend)))

    store.save(_sample_incident("INC-A"))
    store.save(_sample_incident("INC-B"))

    rec = store.recurring_fingerprints()
    assert rec and rec[0]["fingerprint"] == "FP-1"
    assert rec[0]["occurrences"] == 2


def test_incident_store_ignores_write_shaped_read_plane(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    repo = FakeAuditRepo(backend, FakeWriteShapedReadPlane(backend))
    store = IncidentStore(audit_repo=repo)

    # A write-shaped read backend is refused: reads degrade (empty), and the
    # store never routes a read through the write path.
    assert store._read_backend is None
    assert store.list_incidents() == []
    assert store.count()["total"] == 0


def test_incident_store_archive_anchors_to_workspace_without_db_path(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    store = IncidentStore(audit_repo=FakeAuditRepo(backend, FakeReadPlane(backend)))

    store.save(_sample_incident())
    out = store.archive_evidence("INC-PG-001", archive_dir=str(tmp_path / "archive"))
    assert out is not None and out.exists()
    assert out.name == "INC-PG-001.json"


def test_incident_store_sql_is_portable_no_sqlite_dialect_on_write_plane(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    store = IncidentStore(audit_repo=FakeAuditRepo(backend, FakeReadPlane(backend)))

    store.save(_sample_incident())

    # The write-plane branch must not emit SQLite-only INSERT OR IGNORE /
    # INSERT OR REPLACE verbs (the audit queue path does, the direct plane
    # path must not).
    for sql, _ in backend.executed:
        assert "INSERT OR IGNORE" not in sql, sql
        assert "INSERT OR REPLACE" not in sql, sql


# ---------------------------------------------------------------------------
# D5: the write hook to audit_repo._queue is preserved
# ---------------------------------------------------------------------------


def test_queued_save_hook_is_preserved_when_a_queue_exists(tmp_path: Path) -> None:
    class QueuedRepo(FakeAuditRepo):
        def __init__(self, backend: Any) -> None:
            super().__init__(backend)
            self._queue: list[tuple[str, dict[str, Any]]] = []
            self._queue_put = 0

        def save(self, *args: Any) -> Any:  # pragma: no cover - unused
            raise AssertionError

    repo = QueuedRepo(FakePgBackend(str(tmp_path / "audit.db")))
    # Reuse the real queue.Queue surface the store checks for.
    import queue as _queue

    q: _queue.Queue[tuple[str, tuple]] = _queue.Queue(maxsize=10)
    repo._queue = q  # type: ignore[assignment]

    store = IncidentStore(audit_repo=repo)
    store.save(_sample_incident())

    sql, values = q.get_nowait()
    assert "INSERT INTO incidents" in sql
    assert isinstance(values, dict)
    assert values["incident_id"] == "INC-PG-001"


# ---------------------------------------------------------------------------
# D5: SQLite path is unchanged (byte-for-byte contract)
# ---------------------------------------------------------------------------


def test_sqlite_path_still_uses_direct_connections(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    store = IncidentStore(db_path=str(db))

    incident = _sample_incident()
    store.save(incident)

    got = store.get(incident.incident_id)
    assert got is not None
    assert got.component == "risk_engine"
    assert store.count()["total"] == 1
    assert store.delete_by_id(incident.incident_id) is True
    assert store.get(incident.incident_id) is None


def test_sqlite_path_keeps_dialect_spellings(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    store = IncidentStore(db_path=str(db))
    store.save(_sample_incident())

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM incident_quarantine WHERE incident_id = ?", ("INC-PG-001",)
        ).fetchone()
    assert rows[0] == 1

    # SQLite keeps its own aggregate spelling.
    store.save(_sample_incident("INC-PG-002"))
    rec = store.recurring_fingerprints()
    assert rec and rec[0]["occurrences"] == 2


def test_ensure_schema_sqlite_path(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    store = IncidentStore(db_path=str(db))
    store.ensure_schema()

    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "incidents",
        "incident_events",
        "incident_value_traces",
        "incident_quarantine",
    } <= tables


# ---------------------------------------------------------------------------
# D4: LearningCycleOrchestrator path resolution
# ---------------------------------------------------------------------------


def test_resolve_cycle_store_db_path_uses_audit_db_path_for_sqlite(tmp_path: Path) -> None:
    repo = FakeAuditRepo.__new__(FakeAuditRepo)
    repo._is_sqlite = True
    repo._db_path = str(tmp_path / "audit.db")

    assert _resolve_cycle_store_db_path(repo) == str(tmp_path / "audit.db")


def test_resolve_cycle_store_db_path_non_sqlite_is_a_real_file() -> None:
    repo = FakeAuditRepo(backend=None)

    path = _resolve_cycle_store_db_path(repo)

    assert path != ":memory:"
    assert path != ""
    assert Path(path).name == "learning_cycles.db"
    # A real, durable file — not an in-memory throwaway.
    assert Path(path).suffix == ".db"
    assert Path(path).parent.exists()


def test_resolve_cycle_store_db_path_magicmock_fallback_unchanged() -> None:
    """The test-double fallback must stay (test_htf_warmup_gate depends on it)."""
    from unittest.mock import MagicMock

    repo = MagicMock()
    # A MagicMock's _db_path is not a str -> the hermetic in-memory fallback.
    assert _resolve_cycle_store_db_path(repo) == ":memory:"


def test_resolve_cycle_store_db_path_none_audit_repo_is_in_memory() -> None:
    assert _resolve_cycle_store_db_path(None) == ":memory:"  # type: ignore[arg-type]


def test_cycle_store_workspace_path_is_stable_and_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    first = _cycle_store_workspace_path()
    second = _cycle_store_workspace_path()

    assert first == second, "the path must be stable across calls (restart-safe)"
    assert Path(first).is_absolute()


def test_cycle_store_works_on_the_resolved_workspace_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleStore

    monkeypatch.chdir(tmp_path)
    repo = FakeAuditRepo(backend=None)

    path = _resolve_cycle_store_db_path(repo)
    store = LearningCycleStore(path)

    cycle_id = store.start_cycle(trigger="manual", trigger_identity="ti")
    assert store.get_cycle(cycle_id) is not None
    # Restart-safety: a second store over the SAME file sees the cycle.
    store2 = LearningCycleStore(path)
    assert store2.get_cycle(cycle_id) is not None


def test_audit_repo_is_nonsqlite_classification() -> None:
    assert _audit_repo_is_nonsqlite(FakeAuditRepo(backend=None)) is True
    sqlite_repo = FakeAuditRepo.__new__(FakeAuditRepo)
    sqlite_repo._is_sqlite = True
    assert _audit_repo_is_nonsqlite(sqlite_repo) is False
    assert _audit_repo_is_nonsqlite(None) is False


# ---------------------------------------------------------------------------
# Lane D seams: resolution helpers
# ---------------------------------------------------------------------------


def test_resolve_audit_backends_requires_write_plane(tmp_path: Path) -> None:
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    repo = FakeAuditRepo(backend)

    write_plane, read_plane = _resolve_audit_backends(repo)
    assert write_plane is backend
    assert read_plane is None  # no read plane registered on this repo


def test_resolve_audit_backends_returns_none_pair_when_unresolvable() -> None:
    assert _resolve_audit_backends(FakeAuditRepoNoResolver()) == (None, None)
    assert _resolve_audit_backends(None) == (None, None)


def test_resolve_audit_backends_sqlite_repo_yields_nothing(tmp_path: Path) -> None:
    sqlite_repo = FakeAuditRepo.__new__(FakeAuditRepo)
    sqlite_repo._is_sqlite = True
    assert _resolve_audit_backends(sqlite_repo) == (None, None)


# ---------------------------------------------------------------------------
# Live-engine call-site shape: empty db_path + non-SQLite repo must not raise
# ---------------------------------------------------------------------------


def test_live_engine_call_site_shape_no_longer_raises(tmp_path: Path) -> None:
    """``getattr(self.audit, "_db_path", "")`` then IncidentStore(...)."""
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    audit = FakeAuditRepo(backend, FakeReadPlane(backend))

    db_path = getattr(audit, "_db_path", "")
    store = IncidentStore(db_path=db_path, audit_repo=audit)

    assert store.db_path == ""
    store.save(_sample_incident())
    assert store.count()["total"] == 1


def test_learning_cycle_orchestrator_constructs_under_non_sqlite(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from nexus_scalp.model_lifecycle.learning_config import LearningConfig
    from nexus_scalp.model_lifecycle.learning_loop import LearningCycleOrchestrator

    monkeypatch_dir = tmp_path
    cwd = Path.cwd()
    try:
        import os

        os.chdir(monkeypatch_dir)
        repo = FakeAuditRepo(backend=None)
        orchestrator = LearningCycleOrchestrator(
            audit_repo=repo,
            ledger=MagicMock(),
            orchestrator=MagicMock(),
            config=LearningConfig(),
        )
    finally:
        import os

        os.chdir(cwd)

    assert orchestrator.cycles.db_path != ":memory:"
    assert Path(orchestrator.cycles.db_path).exists()


def _default_audit_repo_is_sqlite() -> None:  # pragma: no cover - sanity guard
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = AuditRepository()
    assert repo._is_sqlite is True
    assert repo._db_path  # non-empty by default


# ---------------------------------------------------------------------------
# PG-DBPATH-BOOT-001: the provider URI must not become the store's sqlite path
# ---------------------------------------------------------------------------


def test_incident_store_ignores_the_provider_uri_as_db_path(tmp_path: Path) -> None:
    """``AuditRepository._db_path`` is a URI under a non-SQLite provider.

    The incidents API constructs ``IncidentStore(audit_repo=repo)`` with no
    explicit path; adopting the URI routed the store down the SQLite branch
    and ``ensure_schema`` died on ``sqlite3.connect``. The URI must be
    rejected so the provider-aware resolution runs instead.
    """
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    repo = FakeAuditRepoWithUri(backend, FakeReadPlane(backend))

    store = IncidentStore(audit_repo=repo)

    assert store.db_path == ""
    assert store._write_backend is backend
    assert store._read_backend is not None


def test_provider_uri_as_explicit_db_path_is_treated_as_absent(tmp_path: Path) -> None:
    """Defense in depth: an explicit URI is not a filesystem location either."""
    backend = FakePgBackend(str(tmp_path / "audit.db"))
    repo = FakeAuditRepoWithUri(backend, FakeReadPlane(backend))

    store = IncidentStore(db_path="postgresql://localhost:5432/nexusdb", audit_repo=repo)

    assert store.db_path == ""
    assert store._write_backend is backend


def test_provider_uri_repo_without_backends_still_raises() -> None:
    """The guard does not mask the hard requirement: no backend, no store."""
    repo = FakeAuditRepoWithUri(backend=None)

    with pytest.raises(ValueError, match="requires db_path or audit_repo"):
        IncidentStore(audit_repo=repo)


def test_sqlite_repo_db_path_still_wins_over_the_uri(tmp_path: Path) -> None:
    """An explicit SQLite path is unchanged and beats the repo attribute."""
    db = tmp_path / "incidents.db"
    store = IncidentStore(db_path=str(db), audit_repo=FakeAuditRepoWithUri(None))
    assert store.db_path == str(db)


def test_is_usable_sqlite_path_classifies_paths_and_uris() -> None:
    from nexus_scalp.incidents.store import _is_usable_sqlite_path

    assert _is_usable_sqlite_path("postgresql://localhost:5432/nexusdb") is False
    assert _is_usable_sqlite_path("postgresql://user:pass@host:5432/nexusdb") is False
    assert _is_usable_sqlite_path("") is False
    assert _is_usable_sqlite_path("  ") is False
    assert _is_usable_sqlite_path(":memory:") is False
    assert _is_usable_sqlite_path(None) is False
    assert _is_usable_sqlite_path(7) is False

    assert _is_usable_sqlite_path("/tmp/incidents.db") is True
    assert _is_usable_sqlite_path("C:/Users/x/incidents.db") is True
    assert _is_usable_sqlite_path(" incidents.db ") is True
    assert _is_usable_sqlite_path(Path("incidents.db")) is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(tempfile.gettempdir())
