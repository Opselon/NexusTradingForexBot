"""DEEP-OPT L4 — PostgreSQL arm (contract sections 40/41/42).

The SQLite battery proves the producer and the derived read on the engine the
live ledger uses. This module proves the SAME contract on real PostgreSQL,
using the suite's own throwaway cluster (`phase2_pg`) — never the operator's.

What it establishes:
  * the mirror body is NOT written on the PostgreSQL path either (the writer is
    provider-agnostic, so this is the witness that the fix is not SQLite-only);
  * the derived read resolves the body from ``research_gates`` through the
    PostgreSQL READ plane, which rewrites ``?`` placeholders to ``%s`` on the
    execution path — so a qmark-only fix would fail here rather than pass
    silently;
  * a legacy row still carrying a real body reads untouched on PostgreSQL.

Schema: the fixture mirrors the LIVE PostgreSQL column shape, read from the
live ``nexusdb`` information_schema. It deliberately does NOT use the
SQLite→PG DDL port: the migrator carries SQLite's ``datetime('now')`` defaults
through, which PostgreSQL rejects, so porting would test the migrator rather
than this fix.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_scalp.research.evidence import (
    EvidenceArtifact,
    EvidenceKind,
    GateStatus,
)
from nexus_scalp.research.observability import (
    _EVIDENCE_CONTENT_DERIVED,
    ResearchObservabilityStore,
)

pytestmark = pytest.mark.skipif(
    not Path("C:/Program Files/PostgreSQL/17/bin").is_dir(),
    reason="the phase2 throwaway PostgreSQL cluster is a Windows-only local "
    "fixture (its initdb path is absolute); CI has no such binary, so the "
    "PostgreSQL arm runs where the cluster exists and skips elsewhere",
)

_STAMP = int(time.time() * 1_000_000)


def _iso() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def pg_research(pg_conn):
    """Research tables on the throwaway PG, mirroring the LIVE PG column shape.

    Types/defaults were read from the live ``nexusdb`` information_schema, so
    this arm exercises the provider's real representation rather than a
    ported SQLite approximation (the migrator leaves SQLite's
    ``datetime('now')`` defaults un-portable, which PostgreSQL rejects).
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS research_runs (run_id text PRIMARY KEY, "
            "dataset_id text, strategy_id text, strategy_version text, "
            "executed_at text, config text, build_identity text, result_summary text);"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS research_gates ("
            "id bigserial PRIMARY KEY, gate_id text NOT NULL, strategy_id text NOT NULL,"
            " research_run_id text NOT NULL, gate_type text NOT NULL,"
            " status text DEFAULT 'PENDING', started_at text DEFAULT '',"
            " completed_at text DEFAULT '', duration_ms double precision DEFAULT 0.0,"
            " configuration_version text DEFAULT '', dataset_version text DEFAULT '',"
            " engine_version text DEFAULT '', result text DEFAULT '{}',"
            " failure_reason text DEFAULT '', failure_class text DEFAULT 'UNKNOWN',"
            " evidence_id text DEFAULT '', retryable bigint DEFAULT 0,"
            " order_index bigint DEFAULT 0);"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS research_evidence ("
            "id bigserial PRIMARY KEY, evidence_id text NOT NULL UNIQUE,"
            " strategy_id text NOT NULL, research_run_id text NOT NULL,"
            " gate_id text DEFAULT '', kind text NOT NULL, content text DEFAULT '{}',"
            " content_hash text DEFAULT '', dataset_version text DEFAULT '',"
            " engine_version text DEFAULT '', created_at text);"
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_id ON research_evidence (evidence_id);"
        )
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_gates_id ON research_gates (gate_id);")
    return pg_conn


@pytest.fixture
def pg_store(pg_research):
    """A ResearchObservabilityStore bound to the PostgreSQL planes.

    Uses the production ``provision_domain`` seam so the write path resolves
    the same registered backend the live server uses (the store's
    ``queue_write`` looks the domain up in the fabric registry, not on the
    repository object).
    """
    from nexus_scalp.database.fabric import (
        get_domain_backend,
        register_domain_backend,
        register_domain_read_backend,
    )
    from nexus_scalp.database.fabric.pg_planes import PgReadPlane, PgWritePlane, PoolLimits
    from tests.contracts.phase2.conftest import _PHASE2_PG_URL

    # Register the planes directly rather than via provision_domain: that seam
    # also runs the domain's schema migrations, which exhaust this cluster's
    # tiny pool (max_worker_processes=2). The tables already exist in pg_research.
    limits = PoolLimits(min_size=1, max_size=2)
    write_plane = PgWritePlane(_PHASE2_PG_URL, limits)
    read_plane = PgReadPlane(_PHASE2_PG_URL, limits)
    write_plane.open()
    read_plane.open()
    register_domain_backend("audit", write_plane)
    register_domain_read_backend("audit", read_plane)
    read_backend = get_domain_backend("audit", readonly=True)

    class _Repo:
        _is_sqlite = False

        def research_read_plane(self):
            return read_backend

        def audit_read_plane(self):
            return read_backend

        def _bump_provider_read_counter(self, name):
            return 0

        def _bump_provider_read_operation(self, operation):
            return 0

    store = ResearchObservabilityStore(_Repo())  # type: ignore[arg-type]
    yield store
    write_plane.close()
    read_plane.close()


class TestPostgresqlEvidencePath:
    """40/41: the same minimal-persistence contract on real PostgreSQL."""

    def test_pg_schema_has_the_columns_the_derivation_reads(self, pg_research):
        with pg_research.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='research_evidence' ORDER BY column_name;"
            )
            cols = {r[0] for r in cur.fetchall()}
        assert {"evidence_id", "gate_id", "content", "content_hash"} <= cols

    def test_derived_read_resolves_from_the_gate_on_postgresql(self, pg_research, pg_store):
        """The read goes through the PG plane (qmark -> %s), not a sqlite file."""
        run_id, sid, gate_id = f"RUN-PG-{_STAMP}", f"SF-PG-{_STAMP}", f"G-PG-{_STAMP}"
        body = {"total_trades": 41, "net_pnl_usd": 4100.5, "equity_curve_r": [0.1, 0.2]}

        with pg_research.cursor() as cur:
            cur.execute(
                "INSERT INTO research_runs (run_id, dataset_id, strategy_id, "
                "strategy_version, executed_at, config, build_identity, result_summary) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s);",
                (run_id, "DS-PG", sid, "1.0.0", _iso(), "{}", "b-pg", "{}"),
            )
            cur.execute(
                "INSERT INTO research_gates (gate_id, strategy_id, research_run_id, "
                "gate_type, status, started_at, completed_at, result, "
                "order_index) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s);",
                (
                    gate_id,
                    sid,
                    run_id,
                    "BACKTEST",
                    "PASSED",
                    _iso(),
                    _iso(),
                    json.dumps(body),
                    0,
                ),
            )
            # The producer's NEW shape: marker, never the body.
            cur.execute(
                "INSERT INTO research_evidence (evidence_id, strategy_id, research_run_id,"
                " gate_id, kind, content, content_hash, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s);",
                (
                    f"EV-PG-{_STAMP}",
                    sid,
                    run_id,
                    gate_id,
                    "BACKTEST_RESULT",
                    _EVIDENCE_CONTENT_DERIVED,
                    "",
                    _iso(),
                ),
            )

        ev = pg_store.get_evidence(f"EV-PG-{_STAMP}")
        assert ev is not None, "derived read must not fail on PostgreSQL"
        assert ev["content"] == body, "body must be derived from the PG gate row"

    def test_pg_stores_the_marker_not_the_body(self, pg_research, pg_store):
        """39 on PostgreSQL: the persisted column holds the marker, not a copy."""
        run_id, sid, gate_id = f"RUN-PG2-{_STAMP}", f"SF-PG2-{_STAMP}", f"G-PG2-{_STAMP}"
        body = {"total_trades": 7, "equity_curve_r": [0.5, -0.25]}

        with pg_research.cursor() as cur:
            cur.execute(
                "INSERT INTO research_runs (run_id, dataset_id, strategy_id, "
                "strategy_version, executed_at, config, build_identity, result_summary) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s);",
                (run_id, "DS-PG2", sid, "1.0.0", _iso(), "{}", "b-pg2", "{}"),
            )
            cur.execute(
                "INSERT INTO research_gates (gate_id, strategy_id, research_run_id, "
                "gate_type, status, started_at, completed_at, result, "
                "order_index) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s);",
                (
                    gate_id,
                    sid,
                    run_id,
                    "OOS",
                    "PASSED",
                    _iso(),
                    _iso(),
                    json.dumps(body),
                    1,
                ),
            )

        pg_store.finish_gate(
            gate_id,
            status=GateStatus.PASSED,
            result=body,
            evidence=EvidenceArtifact.create(
                sid, run_id, EvidenceKind.OOS_RESULT, body, gate_id=gate_id
            ),
        )

        with pg_research.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM research_gates WHERE gate_id=%s;", (gate_id,))
            gate_present = cur.fetchone()[0]
            cur.execute("SELECT content FROM research_evidence WHERE gate_id=%s;", (gate_id,))
            row = cur.fetchone()
        assert gate_present == 1, "the seeded gate row must be visible to the store"
        assert row is not None, "finish_gate must create the evidence row on PostgreSQL"
        assert row[0] == _EVIDENCE_CONTENT_DERIVED, (
            "the PostgreSQL writer must persist the derivation marker, never the body"
        )

    def test_legacy_pg_body_row_still_reads_untouched(self, pg_research, pg_store):
        """42: an unmigrated PostgreSQL row keeps serving its real body."""
        body = {"total_trades": 3, "legacy": True}
        with pg_research.cursor() as cur:
            cur.execute(
                "INSERT INTO research_gates (gate_id, strategy_id, research_run_id, "
                "gate_type, status, started_at, completed_at, result, "
                "order_index) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s);",
                (
                    f"G-LEG-{_STAMP}",
                    f"SF-LEG-{_STAMP}",
                    f"RUN-LEG-{_STAMP}",
                    "BACKTEST",
                    "PASSED",
                    _iso(),
                    _iso(),
                    json.dumps({"different": "gate outcome"}),
                    0,
                ),
            )
            cur.execute(
                "INSERT INTO research_evidence (evidence_id, strategy_id, research_run_id,"
                " gate_id, kind, content, content_hash, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s);",
                (
                    f"EV-LEG-{_STAMP}",
                    f"SF-LEG-{_STAMP}",
                    f"RUN-LEG-{_STAMP}",
                    f"G-LEG-{_STAMP}",
                    "BACKTEST_RESULT",
                    json.dumps(body),
                    "",
                    _iso(),
                ),
            )

        ev = pg_store.get_evidence(f"EV-LEG-{_STAMP}")
        assert ev is not None and ev["content"] == body, (
            "rollback rule 86: the legacy reader must survive on PostgreSQL too"
        )
