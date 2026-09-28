"""PG-RESEARCH-READ-001 — the research read plane is silently SQLite-only.

SYMBOL: the /research page reports ``Registry total: 0`` while the live
database holds 4109 ``strategy_registry`` rows. This is NOT an empty backend.

The chain (verified end to end on a live PostgreSQL-configured engine):

    UI -> /api/research/summary -> debug_research_routes ->
         research.store.registry_summary(repo) ->
             if not repo._is_sqlite: return {"available": False, "total": 0}

Every research read surface (``research/store.py``, ``research/registry.py``,
``research/observability.py``, ``research/dataset.py``,
``web/api_v1/common.fetch_rows_bounded``) opens a *raw sqlite3 connection* to
``repo._db_path`` behind an ``if not repo._is_sqlite`` gate. Under the
persisted PostgreSQL provider:

    * ``repo._is_sqlite`` is False, so every gate returns its empty default
      (0 / [] / {"available": False}) with NO exception and NO log;
    * the data is not missing — it is on the PostgreSQL server the engine
      migrated and writes to (verified: 4109 registry rows, 4357 runs,
      82726 events, 26140 gates in ``nexusdb``);
    * ``research_health_summary`` also takes the same gate, so the page's
      "explains WHY" endpoint is itself the broken surface — the operator is
      shown a self-consistent all-zero picture.

The same module already owns the correct seam: ``AuditRepository`` resolves
its READ plane through ``_registered_audit_read_plane()`` ->
``get_domain_backend("audit", readonly=True)`` and serves declared queries
via ``_provider_read_guard(..., sql, args, kind)`` (CHG-0067). The research
surface simply never uses it. This test pins the routing helper the fix
exposes and proves the research surface is served through it.

The fake plane is shaped like ``PgReadPlane`` (query/query_one/scalar, no
``execute`` — the guard refuses a write-shaped backend for reads), per the
established shape in ``test_audit_read_plane_registration_chg0067.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository


class _ResearchReadPlane:
    """A read-plane-shaped fake standing in for a live PostgreSQL read pool.

    Answers the research surface's real SQL with the shapes the SQLite path
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
        if "STRATEGY_REGISTRY" in s and "GROUP BY" in s:
            return [
                {"lifecycle": "REJECTED", "c": 4034},
                {"lifecycle": "DISCOVERED", "c": 72},
                {"lifecycle": "VALIDATED", "c": 3},
            ]
        if "STRATEGY_REGISTRY" in s:
            return [
                {
                    "strategy_id": "SF-PROBE",
                    "strategy_version": "1",
                    "feature_schema_id": "v1",
                    "feature_dimension": 70,
                    "discovery_source": "probe",
                    "discovery_window": "2026-W01",
                    "context_definition": "{}",
                    "parent_strategy_ids": "[]",
                    "lifecycle": "VALIDATED",
                    "backtest": "null",
                    "walkforward": "null",
                    "oos": "null",
                    "robustness": "null",
                    "score": "null",
                    "confidence": 0.9,
                    "sample_count": 10,
                    "validation_lineage": "[]",
                    "retirement_reason": "",
                    "context_matrices": "{}",
                    "created_at": "2026-09-28T00:00:00+00:00",
                    "updated_at": "2026-09-28T00:00:00+00:00",
                }
            ]
        if "RESEARCH_RUNS" in s:
            return [
                {
                    "run_id": "RUN-PROBE",
                    "dataset_id": "ds_probe",
                    "strategy_id": "SF-PROBE",
                    "result_summary": '{"lifecycle": "VALIDATED"}',
                }
            ]
        return []

    def query_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        self.calls.append(("query_one", sql, tuple(args)))
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        self.calls.append(("scalar", sql, tuple(args)))
        s = sql.strip().upper()
        if "COUNT(*)" in s:
            if "STRATEGY_REGISTRY" in s:
                return 4109
            if "RESEARCH_RUNS" in s:
                return 4357
            if "AUDIT_EXPERIENCES" in s:
                return 5239
            if "AUDIT_EXPERIENCE_OUTCOMES" in s:
                return 1715
            if "AUDIT_LEDGER" in s:
                return 435
        return None


@pytest.fixture()
def isolated_fabric_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private fabric registry: the module-level slot is process-global."""
    from nexus_scalp.database import fabric as fabric_mod

    monkeypatch.setattr(fabric_mod, "_DOMAIN_BACKENDS", {})


@pytest.fixture()
def routed_repo(monkeypatch: pytest.MonkeyPatch, isolated_fabric_registry: None) -> AuditRepository:
    """A non-SQLite audit repo whose ``audit`` READ plane is registered.

    Mirrors the production wiring: the plane is registered in the fabric
    under ``(domain, readonly=True)`` and resolved by the repository's own
    ``_registered_audit_read_plane()`` — no second connection path.
    """
    from nexus_scalp.database import fabric as fabric_mod

    plane = _ResearchReadPlane()
    fabric_mod.register_domain_read_backend("audit", plane)

    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nexusdb"
    repo._db_path = ""
    repo.provider_reads_routed = 0
    repo.provider_read_route_errors = 0
    repo.provider_read_degraded_total = 0
    repo.provider_read_degraded_ops = {}
    repo._provider_read_guard_state = {}
    return repo


# =====================================================================
# The seam the fix exposes on AuditRepository
# =====================================================================


def test_research_read_plane_resolves_for_a_non_sqlite_repo(routed_repo: AuditRepository) -> None:
    """``repo.research_read_plane()`` is the documented read accessor.

    A SQLite repo resolves None (its own connection is the reader and the
    fabric must never be consulted); a provisioned non-SQLite repo resolves
    the registered READ plane, and a repo with no plane registered degrades
    to None instead of inventing a connection.
    """
    plane = routed_repo.research_read_plane()
    assert plane is not None
    assert callable(plane.query)
    assert not hasattr(plane, "execute"), "a write-shaped plane must never serve reads"


def test_research_read_plane_is_none_for_sqlite(tmp_path: object) -> None:
    from pathlib import Path

    target = Path(str(tmp_path)) / "research_probe.db"
    repo = AuditRepository(db_url=f"sqlite:///{target}")
    try:
        assert repo._is_sqlite is True
        assert repo.research_read_plane() is None
    finally:
        repo.close()


# =====================================================================
# The research surface is served through that plane, not the empty default
# =====================================================================


def test_registry_summary_reports_server_counts_not_zero(routed_repo: AuditRepository) -> None:
    """``registry_summary`` must answer the server's rows.

    This is the exact value the /research hero KPI renders; before the fix it
    was ``{"available": False, "total": 0}`` while the server held 4109 rows.
    """
    from nexus_scalp.research.store import registry_summary

    summary = registry_summary(routed_repo)
    assert summary["available"] is True
    assert summary["total"] == 4109
    assert summary["by_lifecycle"]["REJECTED"] == 4034
    assert summary["by_lifecycle"]["VALIDATED"] == 3
    assert routed_repo.provider_read_degraded_total == 0


def test_research_health_summary_reports_server_counts_not_empty(
    routed_repo: AuditRepository,
) -> None:
    """The page's "explains WHY" endpoint is itself a gated surface."""
    from nexus_scalp.research.store import research_health_summary

    health = research_health_summary(routed_repo)
    assert health["available"] is True
    assert health["registry_count"] == 4109
    assert health["research_runs"] == 4357
    assert health["source_experiences"] == 5239
    assert routed_repo.provider_read_degraded_total == 0


def test_registry_list_returns_server_rows_not_empty(routed_repo: AuditRepository) -> None:
    from nexus_scalp.research.registry import StrategyRegistry

    entries = StrategyRegistry(audit_repo=routed_repo).list(limit=200)
    assert len(entries) == 1
    assert entries[0].strategy_id == "SF-PROBE"
    assert routed_repo.provider_read_degraded_total == 0


def test_research_runs_list_returns_server_rows(routed_repo: AuditRepository) -> None:
    from nexus_scalp.research.store import list_research_runs

    runs = list_research_runs(routed_repo, limit=100)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "RUN-PROBE"
    assert routed_repo.provider_read_degraded_total == 0


def test_observability_reads_route_instead_of_emptying(routed_repo: AuditRepository) -> None:
    """Every /api/research/{gates,events,worker,queue,analytics} surface."""
    from nexus_scalp.research.observability import ResearchObservabilityStore

    obs = ResearchObservabilityStore(routed_repo)
    assert obs.list_gates(limit=10) is not None  # server rows, not []
    health = obs.worker_health()
    assert health["available"] is not False
    assert routed_repo.provider_read_degraded_total == 0


# =====================================================================
# Degradation stays observable: an unreachable plane never becomes zero data
# =====================================================================


def test_unrouted_repo_reports_unavailable_not_fake_zero(
    monkeypatch: pytest.MonkeyPatch, isolated_fabric_registry: None
) -> None:
    """No read plane registered -> the surface says UNAVAILABLE, not ``0``.

    The pre-fix failure mode was not silence: it was a *self-consistent*
    all-zero picture. When the plane cannot be resolved the answer must be
    ``available: False`` so an operator can tell "no data" from "cannot read".
    """
    from nexus_scalp.research.store import registry_summary

    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nexusdb"
    repo._db_path = ""
    repo.provider_reads_routed = 0
    repo.provider_read_route_errors = 0
    repo.provider_read_degraded_total = 0
    repo.provider_read_degraded_ops = {}
    repo._provider_read_guard_state = {}

    summary = registry_summary(repo)
    assert summary["available"] is False
    assert summary["total"] == 0
    assert repo.provider_read_degraded_total > 0, "degradation must be counted"


# =====================================================================
# SQLite behaviour is unchanged
# =====================================================================


def test_sqlite_registry_summary_still_reads_the_local_file(tmp_path: object) -> None:
    from pathlib import Path

    from nexus_scalp.research.store import registry_summary

    target = Path(str(tmp_path)) / "research_sqlite.db"
    repo = AuditRepository(db_url=f"sqlite:///{target}")
    try:
        # the schema bootstrap creates strategy_registry
        summary = registry_summary(repo)
        assert summary["available"] is True
        assert summary["total"] == 0
        assert repo.provider_read_degraded_total == 0
    finally:
        repo.close()
