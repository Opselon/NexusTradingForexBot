"""Regression: research observability must read through the provider plane.

PG-RESEARCH-OBS-001 (2026-09-28). The live engine (persisted
``database.provider = postgresql``) reported::

    [RESEARCH_OBS] heatmap failed error='unable to open database file'
    [RESEARCH_OBS] family analytics failed error='unable to open database file'

Root cause: ``AuditRepository._db_path`` is a *provider location string*, not
a filesystem path. Under a non-SQLite provider it holds the DSN
``postgresql://localhost:5432/nexusdb`` (populated by ``_provider_db_path``,
D9 of PG-READ-PLANE-001) so that observability consumers that build a ``Path``
from it keep working. ``research/observability.py:_connect`` handed that
string straight to ``sqlite3.connect()``, which treats the URI as a literal
filename and dies with ``unable to open database file``.

Every other read in the file already guarded on ``audit_repo._is_sqlite``,
but the guard only *silently returned the empty default* — so under a pooled
provider the whole observability surface was either erroring or reporting
``0`` / ``[]`` while the database held tens of thousands of rows.

The fix routes every read through ``_query``/``_query_one``/``_scalar``,
which use a SQLite connection on SQLite and the registered fabric READ plane
(``AuditRepository._provider_read_guard``) on a pooled provider. This suite
pins both halves of the contract plus the provider-portability law itself.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.observability import ResearchObservabilityStore

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


class _FakeReadPlane:
    """Minimal stand-in for the fabric's registered READ plane."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.scalars: dict[str, Any] = {}
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.queries.append((sql, tuple(args)))
        return list(self.rows)

    def query_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        self.queries.append((sql, tuple(args)))
        return self.rows[0] if self.rows else None

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        self.queries.append((sql, tuple(args)))
        return self.scalars.get(sql)


def _seed_sqlite(repo: AuditRepository) -> None:
    """Insert one row into every table the observability surface reads."""
    conn = sqlite3.connect(repo._db_path)
    try:

        def ins(table: str, **vals: Any) -> None:
            info = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
            for col in info:
                if col not in vals and info[col][3] and not info[col][5]:
                    vals[col] = "{}"
            cols = [c for c in info if c in vals]
            ph = ",".join(["?"] * len(cols))
            conn.execute(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})",
                [vals[c] for c in cols],
            )

        ins(
            "research_gates",
            gate_id="G1",
            strategy_id="S-1",
            research_run_id="R-1",
            gate_type="OOS",
            status="FAILED",
            order_index=1,
            failure_reason="oos expectancy negative",
            result="{}",
        )
        ins(
            "research_runs",
            run_id="R-1",
            strategy_id="S-1",
            result_summary='{"lifecycle": "REJECTED", "reason": "OOS"}',
            executed_at="2026-01-01T00:00:00Z",
        )
        ins(
            "strategy_registry",
            strategy_id="S-1",
            context_definition='{"fingerprint": "FAM-1"}',
            lifecycle="REJECTED",
            score='{"final_score": 0.42}',
            sample_count=3,
            updated_at="2026-01-01T00:00:00Z",
        )
        ins(
            "research_events",
            id=1,
            event_id="E1",
            strategy_id="S-1",
            research_run_id="R-1",
            gate_id="G1",
            event_type="gate_failed",
            message="m",
            payload="{}",
            occurred_at="2026-01-01T00:00:00Z",
        )
        ins(
            "research_evidence",
            evidence_id="EV1",
            strategy_id="S-1",
            research_run_id="R-1",
            gate_type="OOS",
            failure_class="PRICE",
            payload="{}",
            created_at="2026-01-01T00:00:00Z",
        )
        ins(
            "research_worker_heartbeat",
            scope="research",
            last_beat_at="2026-09-28T00:00:00+00:00",
            cycle_count=1,
            status="RUNNING",
        )
        ins(
            "research_run_snapshots",
            research_run_id="R-1",
            strategy_id="S-1",
            strategy_version="1",
            strategy_definition_hash="h",
            strategy_configuration="{}",
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def sqlite_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> ResearchObservabilityStore:
    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(db_path))
    repo = AuditRepository(db_url=f"sqlite:///{db_path}")
    _seed_sqlite(repo)
    return ResearchObservabilityStore(repo)


def _non_sqlite_repo() -> AuditRepository:
    """A repository whose ``_db_path`` holds the provider DSN, not a file."""
    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_path = "postgresql://localhost:5432/nexusdb"
    repo._db_url = "postgresql://localhost:5432/nexusdb"
    return repo


# ---------------------------------------------------------------------------
# 1. The exact production defect: a DSN in _db_path must not reach sqlite3
# ---------------------------------------------------------------------------


def test_dsn_in_db_path_reproduces_the_live_error() -> None:
    """The reported log line, reproduced from the live attribute value."""
    with pytest.raises(sqlite3.OperationalError) as exc_info:
        sqlite3.connect("postgresql://localhost:5432/nexusdb", timeout=5.0)
    assert "unable to open database file" in str(exc_info.value)


def test_connect_refuses_a_non_sqlite_provider() -> None:
    """``_connect`` fails loudly instead of becoming an invisible DB error."""
    from nexus_scalp.research.observability import _connect

    repo = _non_sqlite_repo()
    with pytest.raises(RuntimeError, match="non-SQLite provider"):
        _connect(repo)


# ---------------------------------------------------------------------------
# 2. Provider portability: the same store works on both providers
# ---------------------------------------------------------------------------


def test_sqlite_reads_return_real_rows(sqlite_store: ResearchObservabilityStore) -> None:
    assert sqlite_store.audit_repo._is_sqlite is True

    heat = sqlite_store.gate_failure_heatmap()
    assert heat["total_failures"] == 1
    assert heat["by_gate"] == {"OOS": 1}
    assert heat["rejection_reasons"] == {"OOS": 1}

    families = sqlite_store.family_analytics()
    assert families["families"]["FAM-1"]["candidates"] == 1
    assert families["families"]["FAM-1"]["rejected"] == 1
    assert families["families"]["FAM-1"]["avg_score"] == 0.42

    counts = sqlite_store.history_counts()
    assert counts["events_live"] == 1
    assert counts["evidence_live"] == 1

    snap = sqlite_store.queue_snapshot()
    assert snap["available"] is True

    health = sqlite_store.worker_health()
    assert health["available"] is True

    events = sqlite_store.list_events()
    assert [e["event_id"] for e in events] == ["E1"]

    assert sqlite_store.get_evidence("EV1") is not None
    assert sqlite_store.get_gate("G1") is not None
    assert sqlite_store.get_run_snapshot("R-1") is not None

    trace = sqlite_store.trace("S-1")
    assert trace["registry"] is not None
    assert len(trace["runs"]) == 1


def test_pooled_provider_reads_through_the_registered_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under a pooled provider every read is served by the READ plane."""
    repo = _non_sqlite_repo()
    plane = _FakeReadPlane()
    monkeypatch.setattr(
        AuditRepository, "_provider_read_guard", lambda self, op, default, **kw: _route(plane, kw)
    )
    store = ResearchObservabilityStore(repo)

    plane.rows = [{"gate_type": "OOS", "c": 7}]
    assert store.gate_failure_heatmap()["total_failures"] == 7
    plane.rows = []
    plane.scalars["SELECT COUNT(*) FROM research_events"] = 99
    assert store.history_counts()["events_live"] == 99

    # The DSN never reaches sqlite3 on this path.
    plane.rows = []
    assert store.list_events() == []
    plane.rows = [{"context_definition": "{}", "lifecycle": "VALIDATED", "score": "{}"}]
    assert store.family_analytics()["families"]["UNKNOWN"]["validated"] == 1


def _route(plane: _FakeReadPlane, kw: dict[str, Any]) -> Any:
    """Mirror ``AuditRepository._route_provider_read`` for the fake plane."""
    kind = kw.get("kind", "")
    if kind == "rows":
        return list(plane.query(kw.get("sql", ""), tuple(kw.get("args", ()))))
    if kind == "row":
        return plane.query_one(kw.get("sql", ""), tuple(kw.get("args", ())))
    if kind == "count":
        return int(plane.scalar(kw.get("sql", ""), tuple(kw.get("args", ()))) or 0)
    return plane.scalar(kw.get("sql", ""), tuple(kw.get("args", ())))


def test_pooled_provider_without_a_plane_degrades_observably(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No registered plane: the read degrades observably, never silently.

    The surface returns its documented empty default AND the guard records a
    degradation, so an operator can tell 'cannot read' from 'no data'.
    """
    repo = _non_sqlite_repo()
    calls: list[str] = []

    def _guard(self: AuditRepository, op: str, default: Any, **kw: Any) -> Any:
        calls.append(op)
        return default()

    monkeypatch.setattr(AuditRepository, "_provider_read_guard", _guard)
    store = ResearchObservabilityStore(repo)

    assert store.gate_failure_heatmap() == {
        "by_gate": {},
        "rejection_reasons": {},
        "total_failures": 0,
    }
    assert store.family_analytics()["families"] == {}
    assert store.history_counts()["events_live"] == 0
    # Every read went through the guard rather than opening a SQLite file.
    assert calls, "no degradation was recorded for an unreadable domain"


# ---------------------------------------------------------------------------
# 3. Source pin: no raw sqlite3.connect over a borrowed _db_path
# ---------------------------------------------------------------------------


def test_no_unguarded_sqlite_connect_over_the_provider_path() -> None:
    """``_connect`` may only be reached on the SQLite branch of the helpers.

    This is the static guarantee that the class of bug stays fixed: the DSN
    in ``_db_path`` can never become a SQLite filename again.
    """
    import ast
    import pathlib

    import nexus_scalp.research.observability as mod

    tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))

    # Collect the functions that legitimately open a SQLite connection.
    sqlite_only = {"_connect", "_query", "_scalar"}
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in sqlite_only:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                    if sub.func.attr == "connect":
                        offenders.append(node.name)

    assert not offenders, f"raw sqlite3.connect outside the SQLite helpers: {offenders}"
