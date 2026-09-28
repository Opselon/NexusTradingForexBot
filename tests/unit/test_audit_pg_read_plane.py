"""PG-AUDIT-READ-001 — the Audit page reads a store that does not exist.

SYMBOL: http://localhost:8089/audit renders a self-consistent empty page
("No audit events match.", quick_check "—", database "nexusdb / not present")
while the engine is writing thousands of rows to the PostgreSQL server it
migrated to.

The chain (verified end to end on the live box, 2026-09-28):

    React /alt/audit
      -> GET /api/v1/observability/events  (auditApi.systemEvents)
      -> GET /api/v1/audit/events          (auditApi.ledgerEvents)
      -> incidents.observability_events / audit_events handlers
      -> common.fetch_rows_bounded(repo, sql, args, limit)
      ->     if not repo._is_sqlite: return []          <-- the silent gate
      ->     sqlite3.connect(repo._db_path ...)         <-- never reached

Two independent defects in that chain:

1. ``fetch_rows_bounded`` opened a RAW sqlite3 connection to
   ``repo._db_path`` and gated on ``repo._is_sqlite``. Under the persisted
   PostgreSQL provider (``application_settings.database.provider=postgresql``,
   source=USER_SETTINGS) ``repo._is_sqlite`` is False, so the helper returned
   ``[]`` with NO exception and NO log. The store the engine writes to is the
   server (verified: 11_302 audit_signals / 435 audit_ledger /
   82_726 research_events rows in ``nexusdb``); the API never asked it.

2. The observability handler's SQL selected from ``audit_events``, a table no
   schema in the repo has ever created (no DDL, no manifest entry, no writer —
   proven: ``git log -S "CREATE TABLE.*audit_events"`` is empty; the table
   exists in NEITHER provider). The SQLite path would have raised
   ``no such table: audit_events`` and returned ``[]`` via the bare
   ``except: return []`` — the query was dead on arrival on both providers,
   and the swallowing hid it.

The same SQLite-only gate also broke the three ``/api/v1/database/*`` metadata
routes (``database/status`` reported a live server as a missing file named
"nexusdb"; ``database/integrity`` 503'd; ``database/tables`` 503'd), so the
page's DB panel itself confirmed the misdiagnosis.

This test pins all three fixes: provider-aware bounded reads through the
repo's own declared READ plane, a table that actually exists in both
providers, and metadata routes that report the live server instead of a
missing file. It fails before the fix (the gate returns [] / the table does
not exist) and passes after.

The fake plane is shaped like ``PgReadPlane`` (query/query_one/scalar, no
``execute`` — the guard refuses a write-shaped backend for reads), per the
established shape in ``test_research_read_plane_pg.py`` and
``test_audit_read_plane_registration_chg0067.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.web.api_v1.common import (
    ProviderReadUnavailableError,
    _repo_read_plane,
    fetch_rows_bounded,
)
from nexus_scalp.web.api_v1_wiring import create_v1_app


class _AuditReadPlane:
    """A read-plane-shaped fake standing in for a live PostgreSQL read pool.

    Answers the audit surface's real SQL with the shapes the SQLite path
    would have produced, so the assertions describe behaviour, not mock
    plumbing. Counts are chosen to be unmistakably non-zero (a real empty
    table would answer 0).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    # -- read surface only (no execute: the guard refuses a write plane) ----

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append(("query", sql, tuple(args)))
        s = sql.strip().upper()
        if "RESEARCH_EVENTS" in s and "WHERE" in s:
            return [
                {
                    "id": 3,
                    "event_type": "GATE_FAILED",
                    "occurred_at": "2026-09-22T00:33:28.049604+00:00",
                    "message": "OOS FAIL",
                    "payload": None,
                }
            ]
        if "RESEARCH_EVENTS" in s:
            return [
                {
                    "id": 2,
                    "event_type": "GATE_STARTED",
                    "occurred_at": "2026-09-22T00:33:27.000000+00:00",
                    "message": "gate robustness started",
                    "payload": None,
                },
                {
                    "id": 1,
                    "event_type": "RESEARCH_RUN_STARTED",
                    "occurred_at": "2026-09-22T00:30:00.000000+00:00",
                    "message": "run RUN-PROBE started",
                    "payload": None,
                },
            ]
        if "AUDIT_LEDGER" in s:
            return [
                {
                    "ticket": 5001,
                    "symbol": "XAUUSD",
                    "direction": "BUY",
                    "volume": 0.1,
                    "entry_price": 2650.5,
                    "status": "CLOSED",
                    "timestamp": "2026-09-20T10:00:00+00:00",
                    "pnl": 12.5,
                }
            ]
        if "CURRENT_DATABASE()" in s:
            return [{"database": "nexusdb", "schema": "public"}]
        if "INFORMATION_SCHEMA.TABLES" in s:
            return [{"name": "audit_ledger"}, {"name": "audit_signals"}]
        return []

    def query_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        self.calls.append(("query_one", sql, tuple(args)))
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        self.calls.append(("scalar", sql, tuple(args)))
        s = sql.strip().upper()
        if s == "SELECT 1":
            return 1
        if "COUNT(*)" in s:
            if "AUDIT_SIGNALS" in s:
                return 11302
            if "AUDIT_LEDGER" in s:
                return 435
            if "AUDIT_ORDERS" in s:
                return 8721
            if "AUDIT_EXECUTIONS" in s:
                return 3
        return None


@pytest.fixture()
def isolated_fabric_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private fabric registry: the module-level slot is process-global."""
    from nexus_scalp.database import fabric as fabric_mod

    monkeypatch.setattr(fabric_mod, "_DOMAIN_BACKENDS", {})


@pytest.fixture()
def pg_repo(monkeypatch: pytest.MonkeyPatch, isolated_fabric_registry: None) -> AuditRepository:
    """A non-SQLite audit repo whose ``audit`` READ plane is registered.

    Mirrors the production wiring: the plane is registered in the fabric
    under ``(domain, readonly=True)`` and resolved by the repository's own
    ``_registered_audit_read_plane()`` — no second connection path.
    """
    from nexus_scalp.database import fabric as fabric_mod

    fabric_mod.register_domain_read_backend("audit", _AuditReadPlane())

    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nexusdb"
    repo._db_path = "postgresql://localhost:5432/nexusdb"
    repo.provider_reads_routed = 0
    repo.provider_read_route_errors = 0
    repo.provider_read_degraded_total = 0
    repo.provider_read_degraded_ops = {}
    repo._provider_read_guard_state = {}
    return repo


@pytest.fixture()
def sqlite_repo(tmp_path: object) -> AuditRepository:
    """A real SQLite repo with one research_events row (the SQLite path)."""
    import sqlite3
    from pathlib import Path

    target = Path(str(tmp_path)) / "audit_probe.db"
    conn = sqlite3.connect(target)
    conn.execute(
        "CREATE TABLE research_events ("
        "id INTEGER PRIMARY KEY, event_type TEXT, occurred_at TEXT, "
        "message TEXT, payload TEXT)"
    )
    conn.execute(
        "INSERT INTO research_events (id, event_type, occurred_at, message, payload)"
        " VALUES (7, 'GATE_PASSED', '2026-09-21T09:00:00+00:00', 'ok', NULL)"
    )
    conn.execute(
        "CREATE TABLE audit_ledger (ticket INTEGER, symbol TEXT, direction TEXT,"
        " volume REAL, entry_price REAL, status TEXT, timestamp TEXT, pnl REAL)"
    )
    conn.execute(
        "INSERT INTO audit_ledger VALUES (9001, 'XAUUSD', 'SELL', 0.2, 2640.0,"
        " 'CLOSED', '2026-09-19T08:00:00+00:00', -4.0)"
    )
    conn.commit()
    conn.close()
    repo = AuditRepository(db_url=f"sqlite:///{target}")
    try:
        yield repo
    finally:
        repo.close()


# =====================================================================
# 1. fetch_rows_bounded reads the pooled provider instead of returning []
# =====================================================================


def test_bounded_read_returns_server_rows_for_pooled_provider(pg_repo: AuditRepository) -> None:
    """The event-stream query answers the server's rows.

    Before the fix this returned ``[]`` (the ``if not repo._is_sqlite`` gate)
    and the Audit page rendered "No audit events match." — truthful-looking
    empty data over a store holding 82_726 rows.
    """
    rows = fetch_rows_bounded(
        pg_repo,
        "SELECT id, event_type, occurred_at, message, payload FROM research_events"
        " ORDER BY id DESC",
        (),
        50,
    )
    assert isinstance(rows, list)
    assert len(rows) == 2
    assert rows[0]["event_type"] == "GATE_STARTED"
    assert rows[0]["occurred_at"]


def test_bounded_read_ledger_reads_server_rows(pg_repo: AuditRepository) -> None:
    """The trade-ledger query answers the server's rows (audit_ledger)."""
    rows = fetch_rows_bounded(
        pg_repo,
        "SELECT ticket, symbol, direction, volume, entry_price, status, timestamp,"
        " pnl FROM audit_ledger ORDER BY ticket DESC",
        (),
        50,
    )
    assert isinstance(rows, list)
    assert rows[0]["ticket"] == 5001
    assert rows[0]["symbol"] == "XAUUSD"


def test_bounded_read_limits_the_result(pg_repo: AuditRepository) -> None:
    """The server-side cap is still enforced on the pooled path."""
    rows = fetch_rows_bounded(pg_repo, "SELECT id FROM research_events ORDER BY id DESC", (), 1)
    assert len(rows) == 1


def test_bounded_read_raises_when_no_read_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pooled provider with NO read plane is a failure, not empty data.

    This is the defect class the empty gate hid: the operator must see a real
    error (DEPENDENCY_UNAVAILABLE) instead of "No audit events match.".
    """
    from nexus_scalp.database import fabric as fabric_mod

    monkeypatch.setattr(fabric_mod, "_DOMAIN_BACKENDS", {})

    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nexusdb"
    repo._db_path = "postgresql://localhost:5432/nexusdb"
    repo._provider_read_guard_state = {}

    with pytest.raises(ProviderReadUnavailableError):
        fetch_rows_bounded(repo, "SELECT id FROM research_events ORDER BY id DESC", (), 50)


def test_bounded_read_keeps_its_own_connection_for_sqlite(sqlite_repo: AuditRepository) -> None:
    """The SQLite path is unchanged: the repo's own file is the reader."""
    rows = fetch_rows_bounded(
        sqlite_repo,
        "SELECT id, event_type, occurred_at, message, payload FROM research_events"
        " ORDER BY id DESC",
        (),
        50,
    )
    assert rows[0]["id"] == 7
    assert rows[0]["event_type"] == "GATE_PASSED"


def test_read_plane_resolver_prefers_the_repo_accessor(pg_repo: AuditRepository) -> None:
    """``_repo_read_plane`` composes the repo's own seam, never a new DSN."""
    plane = _repo_read_plane(pg_repo)
    assert plane is not None
    assert callable(plane.query)
    assert not hasattr(plane, "execute"), "a write-shaped plane must never serve reads"


def test_read_plane_is_none_for_sqlite(sqlite_repo: AuditRepository) -> None:
    assert _repo_read_plane(sqlite_repo) is None


# =====================================================================
# 2. the observability query targets a table that actually exists
# =====================================================================


def test_observability_sql_targets_an_existing_table() -> None:
    """``audit_events`` exists in no provider; ``research_events`` exists in both.

    ``git log -S 'CREATE TABLE.*audit_events'`` is empty and no manifest row
    declares it — the old SQL was dead on arrival, and the bare
    ``except: return []`` hid the ``no such table`` error as empty data.
    ``research_events`` is a real audit-domain table (the research module's own
    DDL, applied by the audit-domain schema replay) with a live writer.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.database.migration.schema_snapshot import audit_schema_statements

    # the audit domain's complete runtime schema (every table that exists in a
    # freshly provisioned audit database, both providers)
    ddl = " ".join(audit_schema_statements()).upper()
    assert "RESEARCH_EVENTS" in ddl
    assert "AUDIT_EVENTS" not in ddl, (
        "audit_events appeared in the audit schema — update this test's "
        "target table to whatever the canonical event stream is"
    )
    # the audit repo's read plane is the accessor the fix composes
    assert hasattr(AuditRepository, "audit_read_plane")


# =====================================================================
# 3. the v1 routes serve the server's rows end to end
# =====================================================================


@pytest.fixture()
def pg_v1_client(pg_repo: AuditRepository) -> TestClient:
    """A v1 app whose audit repo is the pooled-provider probe."""
    app = create_v1_app()
    app.state.audit_v1_repo = pg_repo  # type: ignore[attr-defined]
    return TestClient(app)


def test_observability_events_returns_server_rows(pg_v1_client: TestClient) -> None:
    r = pg_v1_client.get("/api/v1/observability/events?page=1&page_size=25")
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body, body
    items = body["data"]["items"]
    assert len(items) == 2
    assert items[0]["event_type"] == "GATE_STARTED"
    assert items[0]["occurred_at"]


def test_observability_events_type_filter(pg_v1_client: TestClient) -> None:
    r = pg_v1_client.get("/api/v1/observability/events?event_type=GATE_FAILED")
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["event_type"] == "GATE_FAILED"
    assert items[0]["message"] == "OOS FAIL"


def test_audit_ledger_events_returns_server_rows(pg_v1_client: TestClient) -> None:
    r = pg_v1_client.get("/api/v1/audit/events?page=1&page_size=25")
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["ticket"] == 5001
    assert items[0]["symbol"] == "XAUUSD"


def test_database_status_reports_the_server_not_a_missing_file(
    pg_v1_client: TestClient,
) -> None:
    """Before the fix this reported ``filename: nexusdb, exists: false`` for a
    live PostgreSQL server (the provider URI was treated as a filename)."""
    r = pg_v1_client.get("/api/v1/database/status")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["provider"] == "postgresql"
    assert data["exists"] is True
    assert data["filename"] == "nexusdb"


def test_database_integrity_reports_server_counts(pg_v1_client: TestClient) -> None:
    """Before the fix this returned 503 (the read-only SQLite probe of a URI)."""
    r = pg_v1_client.get("/api/v1/database/integrity")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["provider"] == "postgresql"
    assert data["quick_check"] == "ok"
    assert data["row_counts"]["audit_signals"] == 11302
    assert data["row_counts"]["audit_ledger"] == 435


def test_database_tables_reports_server_tables(pg_v1_client: TestClient) -> None:
    r = pg_v1_client.get("/api/v1/database/tables")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "audit_ledger" in data["tables"]
    assert data["count"] == 2


def test_unavailable_read_plane_surfaces_as_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No read plane registered -> a real failure, never an empty page."""
    from nexus_scalp.database import fabric as fabric_mod

    monkeypatch.setattr(fabric_mod, "_DOMAIN_BACKENDS", {})

    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nexusdb"
    repo._db_path = "postgresql://localhost:5432/nexusdb"
    repo._provider_read_guard_state = {}

    app = create_v1_app()
    app.state.audit_v1_repo = repo  # type: ignore[attr-defined]
    client = TestClient(app)

    r = client.get("/api/v1/observability/events")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
