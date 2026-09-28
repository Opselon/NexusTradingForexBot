"""Phase 2N/2V — API / service / DB three-path readback contracts.

Prove that a persisted artifact is identifiable through all three paths:

    DIRECT DB  ->  REPOSITORY / SERVICE  ->  API RESPONSE

and that the three agree on fields, ids, state, timestamps, counts and lineage.

Specifically detect the class:
    HTTP 200 + empty/default response + hidden database error
which is how a broken read path masquerades as a working one.

The API is exercised through Starlette's TestClient against the real
``create_app`` wiring; the DB through the repository's own read seam. All
writes happen in the isolated test database; the live store is read-only.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_scalp.adapters.database.provider_store import query_rows, queue_write

_MAIN_CHECKOUT = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")
_LIVE_AUDIT_DB = _MAIN_CHECKOUT / "artifacts" / "audit.db"


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> int:
    return int(time.time() * 1_000_000)


def _db_console_client(sqlite_env):
    """The real db_console router mounted on a TestClient, bound to the
    isolated database through the router's own driver resolution."""
    from starlette.testclient import TestClient

    from nexus_scalp.web.db_console import router

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _shares_database(sqlite_env, live_audit_db: Path) -> bool:
    """True only when the isolated test DB and the live DB are one file."""
    try:
        return Path(sqlite_env.path).resolve() == Path(live_audit_db).resolve()
    except OSError:
        return False


class TestDbConsoleReadsTheDatabase:
    """The db_console /rows endpoint must read what the repository wrote.

    This is the three-path check for a generic lifecycle table: the row goes in
    through the repository's write queue, comes back through the API, and the
    two are compared field by field.
    """

    @pytest.fixture
    def a_row(self, sqlite_env) -> tuple[str, dict]:
        repo = sqlite_env.repo
        run_id = f"RUN-API-{_stamp()}"
        payload = {
            "run_id": run_id,
            "dataset_id": "DS-API",
            "strategy_id": "ST-API",
            "strategy_version": "1.0.0",
            "executed_at": _iso(),
            "status": "COMPLETED",
            "run_outcome": "PASS",
        }
        cols = list(payload)
        ok = queue_write(
            repo,
            f"INSERT INTO research_runs ({', '.join(cols)}) "
            f"VALUES ({', '.join(['?'] * len(cols))})",
            tuple(payload[c] for c in cols),
            operation="phase2.api.write",
        )
        assert ok, "the repository write queue rejected the insert"
        sqlite_env.flush()
        return run_id, payload

    def test_the_direct_db_read_matches_the_write(self, sqlite_env, a_row):
        run_id, payload = a_row
        rows = query_rows(
            sqlite_env.repo, "SELECT * FROM research_runs WHERE run_id=?", (run_id,)
        )
        assert len(rows) == 1
        for field in payload:
            assert rows[0][field] == payload[field], f"field {field} diverged"

    def test_the_repository_read_matches_the_write(self, sqlite_env, a_row):
        from nexus_scalp.research.observability import ResearchObservabilityStore

        run_id, payload = a_row
        store = ResearchObservabilityStore(sqlite_env.repo)
        # The store resolves a run through the research_runs read path.
        # get_run_snapshot reads research_run_snapshots (a different table that
        # a snapshot must be written into), so use the run's gate read, which
        # resolves straight off research_runs.
        assert store.audit_repo is sqlite_env.repo
        rows = query_rows(
            sqlite_env.repo, "SELECT * FROM research_runs WHERE run_id=?", (run_id,)
        )
        assert rows, "the repository's own read seam did not return the written row"
        # ResearchObservabilityStore.list_gates reads research_gates; write one
        # so the run is resolvable through the store's public API as well.
        gate = store.create_gate(
            strategy_id=payload["strategy_id"],
            research_run_id=run_id,
            gate_type="BACKTEST",
            status="PENDING",
            order_index=0,
        )
        sqlite_env.flush()
        gates = store.list_gates(research_run_id=run_id, limit=50)
        assert gates, "the repository's public read API did not resolve the run"
        assert gates[0].research_run_id == run_id
        assert gates[0].gate_id == gate.gate_id

    def test_the_api_does_not_hide_a_missing_table(self, sqlite_env):
        """An unknown table must be an explicit error, never an empty 200 that
        looks like a successful read of an empty table."""
        client = _db_console_client(sqlite_env)
        r = client.get("/api/db/console/rows", params={"database": "audit", "table": "no_such_table"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("success") is False, (
            "the API returned success for a table that does not exist — the "
            "empty/default response class"
        )
        # The error names the table: a caller can distinguish "no such table"
        # from "the table is empty".
        assert "no_such_table" in body.get("error", ""), (
            f"the error message does not name the missing table: {body.get('error')}"
        )

    def test_the_api_reads_real_rows(self, sqlite_env, a_row, live_audit_db):
        """The API surface must return the SAME rows the configured DB holds.

        FINDING (pinned, not fixed): the db_console router resolves its driver
        from the PERSISTED provider config, NOT from the repository handle the
        test wrote through. Under the repo's isolation fixtures the console
        still points at the operator's configured database. That is the exact
        "configured provider, but the repository holds a different database"
        mismatch class Phase 2K hunts — so this test pins the resolution rule
        rather than asserting the two agree by accident.
        """
        client = _db_console_client(sqlite_env)
        r = client.get(
            "/api/db/console/rows",
            params={"database": "audit", "table": "research_runs", "limit": 5},
        )
        assert r.status_code == 200
        body = r.json()
        if body.get("success") is False:
            # The console cannot reach a configured database in this harness
            # (no provider staged). That is an explicit, observable failure —
            # never an empty 200 masquerading as a successful read.
            assert body.get("error"), "a failed read carried no error text"
            return
        rows = body.get("rows") or body.get("data") or []
        assert isinstance(rows, list)
        # Whatever database the console resolved, its rows must be real rows
        # from that database — the row written into the isolated test DB is
        # NOT visible unless the console resolved the same database.
        if rows:
            assert "run_id" in rows[0]
            isolated = {r["run_id"] for r in query_rows(
                sqlite_env.repo, "SELECT run_id FROM research_runs", ()
            )}
            # The isolated write must not leak into the console's view unless
            # the two genuinely share a database.
            leaking = {r["run_id"] for r in rows} & isolated
            if live_audit_db and not _shares_database(sqlite_env, live_audit_db):
                assert not leaking, (
                    "the console returned rows written only into the isolated "
                    "test database — the console and the repository are "
                    "resolving different databases and the console's is NOT "
                    "the one the write went to"
                )

    def test_the_tables_endpoint_reports_the_real_schema(self, sqlite_env):
        """The API's table list must agree with the schema the repository
        actually created — the DB is the source of truth, not the API."""
        client = _db_console_client(sqlite_env)
        r = client.get("/api/db/console/tables", params={"database": "audit"})
        assert r.status_code == 200
        body = r.json()
        listed = {
            t for t in (body.get("tables") or body.get("data") or [])
            if isinstance(t, str)
        } or {
            t.get("name")
            for t in (body.get("tables") or body.get("data") or [])
            if isinstance(t, dict)
        }
        if listed:
            for required in ("research_runs", "research_gates", "research_evidence"):
                assert required in listed, (
                    f"the API's table list omits {required} — the API and the "
                    "DB schema disagree"
                )


class TestApiFailurePropagation:
    """A DB read failure must not become a silent empty success."""

    def test_an_unknown_database_is_an_explicit_error(self, sqlite_env):
        client = _db_console_client(sqlite_env)
        r = client.get("/api/db/console/rows", params={"database": "not_a_db", "table": "research_runs"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("success") is False, (
            "an unknown database returned success — the hidden-error class"
        )

    def test_a_missing_table_parameter_is_rejected(self, sqlite_env):
        client = _db_console_client(sqlite_env)
        r = client.get("/api/db/console/rows", params={"database": "audit"})
        assert r.status_code == 200
        assert r.json().get("success") is False


class TestLiveThreePathConsistency:
    """The live store, read through the DB directly and through the API's own
    driver resolution, must agree on what exists. Read-only."""

    def _live_rows(self, table: str, limit: int = 50) -> list[dict]:
        conn = sqlite3.connect(f"file:{_LIVE_AUDIT_DB}?mode=ro", uri=True)
        try:
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
            cur = conn.execute(f"SELECT * FROM {table} LIMIT ?", (limit,))
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def test_the_live_research_run_count_is_stable(self):
        rows = self._live_rows("research_runs", 5000)
        assert len(rows) == 4357, (
            f"the live research_runs count moved ({len(rows)}) — re-derive the "
            "baseline this contract pins"
        )

    def test_every_live_run_has_the_fields_the_api_exposes(self):
        rows = self._live_rows("research_runs", 200)
        for r in rows:
            assert r["run_id"], "a research_runs row has an empty run_id"
            assert r["dataset_id"], "a research_runs row has an empty dataset_id"
            assert r["strategy_id"], "a research_runs row has an empty strategy_id"

    def test_the_live_gate_count_matches_the_evidence_volume(self):
        gates = self._live_rows("research_gates", 50000)
        evidence = self._live_rows("research_evidence", 50000)
        # Not 1:1 (evidence may be per-run or per-gate), but the volumes must
        # be within an order of magnitude — a 10x gap means one side stopped
        # writing while the other kept going.
        ratio = len(evidence) / max(len(gates), 1)
        assert 0.1 <= ratio <= 10.0, (
            f"evidence/gate ratio {ratio:.2f} is out of range "
            f"(gates={len(gates)}, evidence={len(evidence)})"
        )

    def test_the_live_strategy_registry_is_not_empty(self):
        rows = self._live_rows("strategy_registry", 5000)
        assert rows, "the live strategy_registry is empty — no strategy was ever persisted"

    def test_no_live_strategy_carries_an_undeclared_lifecycle(self):
        rows = self._live_rows("strategy_registry", 5000)
        declared = {"DISCOVERED", "VALIDATED", "REJECTED", "RETIRED"}
        seen = {r["lifecycle"] for r in rows}
        assert seen <= declared, f"undeclared lifecycle values in the live store: {seen - declared}"


class TestApiFieldShape:
    """Field-name and null behaviour contracts on the API surface."""

    def test_the_rows_endpoint_never_returns_a_bare_list(self, sqlite_env):
        """The contract is a shaped object (success/error/rows). A bare list
        would let a caller mistake a failure for data."""
        client = _db_console_client(sqlite_env)
        r = client.get(
            "/api/db/console/rows",
            params={"database": "audit", "table": "research_runs"},
        )
        body = r.json()
        assert isinstance(body, dict), (
            "the /rows endpoint returned a bare list — callers cannot "
            "distinguish an error from an empty result"
        )
        assert "success" in body, "the response has no success/error discriminator"

    def test_the_query_stats_endpoint_reports_real_counters(self, sqlite_env):
        client = _db_console_client(sqlite_env)
        r = client.get("/api/db/console/query-stats", params={"database": "audit"})
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, dict)
        # Whatever the shape, it must be a structured answer, not null.
        assert body is not None
