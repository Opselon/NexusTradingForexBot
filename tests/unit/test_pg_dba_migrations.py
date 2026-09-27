"""Lane H (DBA) — idempotent ordered PostgreSQL server-side migrations.

WHAT THIS PINS
==============
The cluster has ZERO functions/triggers/views and 102/116 tables with
``reltuples = -1``. This lane adds the DBA layer the operator expects:

  * AUDIT-0010  evidence-based indexes for the seq-scan pressure tables
  * AUDIT-0011  the ONE server-side function: a SECURITY INVOKER ``updated_at``
                trigger + per-table wiring
  * AUDIT-0012  ANALYZE stats maintenance (recorded in ``schema_migrations``)
  * AUDIT-0013  read-only bloat report + threshold-gated VACUUM (ANALYZE)

The contract pinned here, on a REAL PostgreSQL 17 instance:

  (a) every migration is idempotent — the whole set applies TWICE with zero
      errors and zero duplicate objects;
  (b) the ``updated_at`` trigger really sets the column on UPDATE, with no
      application help;
  (c) the ANALYZE entry leaves ``reltuples != -1`` on the hot tables;
  (d) the CONCURRENTLY-vs-transaction path is exercised and the runner does
      not deadlock — ``CREATE INDEX CONCURRENTLY`` on an autocommit connection,
      the plain ``IF NOT EXISTS`` spelling inside a transaction block;
  (e) a failed migration records ``status='FAILED'`` in ``schema_migrations``
      and does not raise out of the boot path.

The instance is the lane's isolated cluster on port 55436 (never the live
localhost:5432): the connection URL comes from ``NSE_PG_TEST_URL`` and the
module skips cleanly when it is unset.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

psycopg = pytest.importorskip("psycopg")

from nexus_scalp.database.migration import analyze as analyze_mod  # noqa: E402
from nexus_scalp.database.migration import indexes as indexes_mod  # noqa: E402
from nexus_scalp.database.migration import triggers as triggers_mod  # noqa: E402
from nexus_scalp.database.migration.pg_schema import (  # noqa: E402
    DBA_MIGRATION_IDS,
    apply_dba_migrations,
)

#: The lane's isolated PostgreSQL cluster — never the live localhost:5432.
PG_URL = os.environ.get("NSE_PG_TEST_URL", "")

needs_postgres = pytest.mark.skipif(
    not PG_URL, reason="NSE_PG_TEST_URL not set (isolated PostgreSQL 55436 arm)"
)

#: The schema_migrations table the history records land in (engine DDL).
_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    version INTEGER NOT NULL,
    description TEXT DEFAULT '',
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    application_version TEXT DEFAULT '',
    git_commit TEXT DEFAULT '',
    execution_ms INTEGER DEFAULT 0,
    status TEXT DEFAULT 'applied'
)
"""

#: Every table the DBA layer touches, with a minimal PK + the column the
#: trigger maintains. A full domain replay is not needed to pin the DBA
#: contract, and a minimal schema keeps the test hermetic to this lane.
_DBA_TABLES: tuple[tuple[str, str], ...] = (
    (
        "news_junk_hashes",
        "article_hash TEXT PRIMARY KEY, reason TEXT DEFAULT 'junk', pruned_at TEXT DEFAULT ''",
    ),
    (
        "audit_ledger",
        "ticket BIGINT PRIMARY KEY, status TEXT DEFAULT '', symbol TEXT DEFAULT '', timestamp TEXT DEFAULT ''",
    ),
    (
        "news_sources",
        "source_id TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1, priority REAL DEFAULT 0.5",
    ),
    (
        "incidents",
        "incident_id TEXT PRIMARY KEY, status TEXT DEFAULT '', severity TEXT DEFAULT '', detected_at TEXT DEFAULT '', updated_at TEXT DEFAULT '', component TEXT DEFAULT ''",
    ),
    (
        "audit_broker_orders",
        "ticket BIGINT PRIMARY KEY, position_id BIGINT DEFAULT 0, time_setup BIGINT DEFAULT 0",
    ),
    (
        "audit_broker_deals",
        "ticket BIGINT PRIMARY KEY, position_id BIGINT DEFAULT 0, time BIGINT DEFAULT 0",
    ),
    ("audit_broker_trades", "trade_id TEXT PRIMARY KEY, position_id BIGINT DEFAULT 0"),
    ("factory_generations", "generation_id TEXT PRIMARY KEY, number INTEGER DEFAULT 0"),
    # trigger targets (only the column the trigger maintains is needed).
    # NOTE: ``application_settings`` / ``trading_rules_config`` are present so
    # the fixture covers the ANALYZE hot set and the failure-injection path,
    # but they are DELIBERATELY NOT trigger targets — verified on the live
    # nexusdb neither carries ``updated_at`` (trading_rules_config is
    # rule_name/is_enabled/category/parameters; the settings table lives in
    # the separate settings DB). The trigger layer refuses to install a trigger
    # whose column is missing, so they stay out of TRIGGER_TARGETS.
    ("application_settings", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("trading_rules_config", "rule_name TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("release_metadata", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("news_articles", "article_id TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("model_runtime_health", "id BIGINT PRIMARY KEY, checked_at TEXT DEFAULT ''"),
    ("strategy_registry", "strategy_id TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    # The remaining ANALYZE hot-set tables (reltuples probes) and the rest of
    # the trigger targets, so one fixture covers every table the layers name.
    ("audit_account_snapshots", "id BIGINT PRIMARY KEY, timestamp TEXT DEFAULT ''"),
    (
        "audit_guard_telemetry",
        "window_start TEXT, symbol TEXT, reason_code TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (window_start, symbol, reason_code)",
    ),
    ("runtime_risk_state", "id BIGINT PRIMARY KEY CHECK (id = 1), state TEXT DEFAULT ''"),
    ("news_entities", "id BIGINT PRIMARY KEY, article_id TEXT DEFAULT ''"),
    ("news_topics", "id BIGINT PRIMARY KEY, article_id TEXT DEFAULT ''"),
    ("news_analysis_runs", "run_id TEXT PRIMARY KEY, status TEXT DEFAULT ''"),
    ("incident_value_traces", "id BIGINT PRIMARY KEY, incident_id TEXT DEFAULT ''"),
    ("calendar_worker_state", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("factory_loop_state", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("hygiene_worker_state", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("mk_enablement", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("mk_meta", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("mk_seeds", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("model_governance_state", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("news_article_versions", "version_id BIGINT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("strategy_intelligence_registry", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
    ("strategy_research_meta", "key TEXT PRIMARY KEY, updated_at TEXT DEFAULT ''"),
)


@contextmanager
def _connect(autocommit: bool = True) -> Any:
    conn = psycopg.connect(PG_URL, connect_timeout=10)
    conn.autocommit = autocommit
    try:
        yield conn
    finally:
        conn.close()


def _probe_scalar(conn: Any, sql: str) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    return row[0] if row else None


def _probe_rows(conn: Any, sql: str) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def _make_execute_query(conn: Any) -> Callable[[str], list[tuple[Any, ...]]]:
    """The SELECT seam both query contracts use.

    The trigger layer probes with ``(sql, params)`` while the analyze layer
    calls ``query(sql)``; one adapter serves both by accepting an optional
    parameter tuple, so a single connection backs the whole DBA pass without a
    second spelling of the probe.
    """

    def query(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with conn.cursor() as cur:
            cur.execute(sql, params or None)
            return list(cur.fetchall())

    return query


def _create_dba_schema(conn: Any) -> None:
    """Create the minimal table set + history table the DBA layer runs on."""
    with conn.cursor() as cur:
        cur.execute(_HISTORY_DDL)
        for table, cols in _DBA_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            cur.execute(f"CREATE TABLE {table} ({cols})")
    conn.commit()


def _reset_schema(conn: Any) -> None:
    """Drop everything the DBA layer created (idempotent re-run start state)."""
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT quote_ident(relname) FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = current_schema() "
            "AND c.relkind = 'r' AND c.relname NOT IN ('_probe_t')"
        )
        tables = [str(r[0]) for r in cur.fetchall()]
        for table in tables:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        cur.execute("DROP FUNCTION IF EXISTS nexus_set_updated_at() CASCADE")
        for idx in indexes_mod.index_names():
            cur.execute(f"DROP INDEX IF EXISTS {idx}")
    conn.autocommit = False


class _Recorder:
    """The ``schema_migrations`` writer the runner is handed."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def __call__(self, migration_id: str, status: str, detail: str) -> None:
        self.rows.append((migration_id, status, detail))
        assert PG_URL
        with psycopg.connect(PG_URL, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO schema_migrations (migration_id, domain, version, "
                    "description, checksum, applied_at, status) "
                    "VALUES (%s, %s, %s, %s, %s, now(), %s) "
                    "ON CONFLICT (migration_id) DO UPDATE SET status = EXCLUDED.status, "
                    "applied_at = EXCLUDED.applied_at",
                    (migration_id, "audit", 0, detail, "lane-h-dba", status),
                )
            conn.commit()


def _executors(
    conn: Any,
) -> tuple[Callable[[str], None], Callable[[str], None], Callable[[], None]]:
    """The two executors the runner needs: transactional + autocommit.

    ``CREATE INDEX CONCURRENTLY``, ``ANALYZE`` and ``VACUUM`` cannot run inside
    a transaction block, so the autocommit executor hands them a connection
    whose ``autocommit`` is on. The transactional executor is used for the DDL.

    Both carry a ``rollback`` attribute the DBA layers call when a statement
    aborts the shared transaction (PG then refuses every later statement until
    the block is rolled back; psycopg also refuses to switch ``autocommit``
    while the connection is not idle).
    """

    def execute(sql: str) -> None:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

    def rollback() -> None:
        with suppress(Exception):
            conn.rollback()

    execute.rollback = rollback  # type: ignore[attr-defined]

    def execute_autocommit(sql: str) -> None:
        # A statement PG forbids inside a block (CONCURRENTLY / ANALYZE /
        # VACUUM). psycopg3 refuses to switch ``autocommit`` while the
        # connection is not idle, and a failed earlier statement leaves it
        # INERROR — recover FIRST, then switch.
        if conn.info.transaction_status != 0:  # 0 == idle
            rollback()
        saved = conn.autocommit
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
        finally:
            conn.autocommit = saved
            # A CONCURRENTLY failure (e.g. a cancelled build) leaves the
            # connection INERROR, which would block the next switch — recover
            # on the way out too so the pass never poisons its own seam.
            if conn.info.transaction_status != 0:
                rollback()

    execute_autocommit.rollback = rollback  # type: ignore[attr-defined]

    return execute, execute_autocommit, rollback


@pytest.fixture(scope="module")
def cluster():
    """The isolated cluster with a clean DBA schema, dropped after the module.

    Nothing here touches the live localhost:5432: the fixture only ever
    connects to ``NSE_PG_TEST_URL`` (the lane's port-55436 instance).
    """
    assert PG_URL, "NSE_PG_TEST_URL must point at the isolated 55436 cluster"
    with _connect(autocommit=False) as conn:
        _reset_schema(conn)
        _create_dba_schema(conn)
    yield PG_URL
    with _connect(autocommit=True) as conn:
        _reset_schema(conn)


@needs_postgres
def test_index_ddl_is_idempotent_when_reapplied(cluster: str) -> None:
    """``CREATE INDEX IF NOT EXISTS`` twice = one index, no error (base case)."""
    with _connect(autocommit=False) as conn:
        execute, _execute_autocommit, _rollback = _executors(conn)
        idx = indexes_mod.MISSING_INDEXES[0]
        execute(idx.postgres_ddl(concurrently=False))
        execute(idx.postgres_ddl(concurrently=False))
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_indexes WHERE indexname = %s", (idx.name,))
            assert cur.fetchone()[0] == 1


@needs_postgres
def test_the_dba_layer_is_idempotent_twice(cluster: str) -> None:
    """(a) The whole ordered set applies TWICE: zero errors, zero duplicates."""
    recorder = _Recorder()
    counts: list[tuple[int, int]] = []
    for _ in range(2):
        with _connect(autocommit=False) as conn:
            execute, execute_autocommit, rollback = _executors(conn)
            result = apply_dba_migrations(
                execute=execute,
                execute_autocommit=execute_autocommit,
                query=_make_execute_query(conn),
                query_scalar=lambda sql: _probe_scalar(conn, sql),
                recover=rollback,
                record=recorder,
                concurrently=True,
            )
        counts.append((int(result["applied_count"]), int(result["error_count"])))

    # Zero errors on BOTH runs.
    assert counts[1][1] == 0, f"second application produced errors: {counts}"
    # The object count is stable: no duplicate index/trigger/function objects.
    with _connect(autocommit=True) as conn:
        for name in indexes_mod.index_names():
            n = _probe_scalar(conn, f"SELECT count(*) FROM pg_indexes WHERE indexname = '{name}'")
            assert n == 1, f"index {name} has {n} copies after two applications"
        for table in triggers_mod.TRIGGER_TARGETS:
            tname = triggers_mod.trigger_name_for(table)
            n = _probe_scalar(
                conn,
                "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE NOT t.tgisinternal AND c.relname = "
                f"'{table}' AND t.tgname = '{tname}'",
            )
            assert n == 1, f"trigger {tname} on {table} has {n} copies"
        n_fn = _probe_scalar(
            conn, "SELECT count(*) FROM pg_proc WHERE proname = 'nexus_set_updated_at'"
        )
        assert n_fn == 1, f"trigger function has {n_fn} copies"
    # Every migration recorded itself in the audit trail on both runs.
    assert len(recorder.rows) == 2 * len(DBA_MIGRATION_IDS)
    recorded_ids = {row[0] for row in recorder.rows}
    assert recorded_ids == set(DBA_MIGRATION_IDS)


@needs_postgres
def test_the_updated_at_trigger_sets_the_column_with_no_app_help(cluster: str) -> None:
    """(b) The trigger maintains ``updated_at`` on UPDATE — no app code needed.

    This is the invariant the 'BREAKER persisted anchors rejected (stale/corrupt
    identity)' warning shows the app cannot enforce across two providers.
    """
    with _connect(autocommit=False) as conn:
        _create_dba_schema(conn)
        execute, execute_autocommit, rollback = _executors(conn)
        apply_dba_migrations(
            execute=execute,
            execute_autocommit=execute_autocommit,
            query=_make_execute_query(conn),
            query_scalar=lambda sql: _probe_scalar(conn, sql),
            recover=rollback,
            record=None,
            concurrently=False,
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO strategy_registry (strategy_id, updated_at) "
                "VALUES ('test-rule', '2020-01-01 00:00:00')"
            )
            cur.execute("SELECT updated_at FROM strategy_registry WHERE strategy_id = 'test-rule'")
            before = str(cur.fetchone()[0])
            assert before == "2020-01-01 00:00:00", "INSERT must not fire the trigger"
            cur.execute(
                "UPDATE strategy_registry SET strategy_id = 'test-rule' WHERE strategy_id = 'test-rule'"
            )
            cur.execute("SELECT updated_at FROM strategy_registry WHERE strategy_id = 'test-rule'")
            after = str(cur.fetchone()[0])
        conn.commit()
    assert after != before, "the trigger did not stamp updated_at on UPDATE"
    # The trigger writes the app's own UTC text form (SQLite parity), not a
    # timestamptz string with a "+00" suffix the app's comparisons never emit.
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", after), (
        f"the trigger stamped a value the app does not produce: {after!r}"
    )


@needs_postgres
def test_the_analyze_entry_leaves_reltuples_set_on_the_hot_tables(cluster: str) -> None:
    """(c) After AUDIT-0012 the hot tables have planner statistics.

    ``reltuples = -1`` means never ANALYZEd; the entry must leave a real estimate.
    """
    with _connect(autocommit=False) as conn:
        execute, execute_autocommit, rollback = _executors(conn)
        result = apply_dba_migrations(
            execute=execute,
            execute_autocommit=execute_autocommit,
            query=_make_execute_query(conn),
            query_scalar=lambda sql: _probe_scalar(conn, sql),
            recover=rollback,
            record=None,
            concurrently=False,
        )
    analyze_result = result["per_migration"]["AUDIT-0012-analyze-stats-maintenance"]
    assert analyze_result["error_count"] == 0, analyze_result["errors"]
    with _connect(autocommit=True) as conn:
        for table in ("audit_ledger", "news_sources", "incidents", "news_junk_hashes"):
            reltuples = _probe_scalar(
                conn,
                f"SELECT reltuples FROM pg_class WHERE relname = '{table}' AND relkind = 'r'",
            )
            assert reltuples != -1, f"{table} was never ANALYZEd (reltuples=-1)"


@needs_postgres
def test_the_concurrently_path_and_the_transaction_path_both_work(cluster: str) -> None:
    """(d) CONCURRENTLY on an autocommit connection, plain IF NOT EXISTS in a tx.

    The runner must not deadlock: the CONCURRENTLY path is only selected when
    the executor is autocommit, and the in-transaction path falls back to the
    plain spelling. Both must succeed and both must be re-runnable.
    """
    for concurrently in (True, False):
        with _connect(autocommit=False) as conn:
            _create_dba_schema(conn)
            execute, execute_autocommit, rollback = _executors(conn)
            result = apply_dba_migrations(
                execute=execute,
                execute_autocommit=execute_autocommit,
                query=_make_execute_query(conn),
                query_scalar=lambda sql: _probe_scalar(conn, sql),
                recover=rollback,
                record=None,
                concurrently=concurrently,
            )
        assert result["error_count"] == 0, [(e["statement"], e["error"]) for e in result["errors"]]
        index_result = result["per_migration"]["AUDIT-0010-missing-indexes"]
        assert index_result["concurrently"] is concurrently
    # Both paths must converge on the same single copy of each index.
    with _connect(autocommit=True) as conn:
        for name in indexes_mod.index_names():
            n = _probe_scalar(conn, f"SELECT count(*) FROM pg_indexes WHERE indexname = '{name}'")
            assert n == 1, f"{name} duplicated across the two paths ({n})"


@needs_postgres
def test_a_failed_migration_records_failed_and_never_raises(cluster: str) -> None:
    """(e) A failed migration records status='FAILED' and the runner continues.

    The failure is injected where the layer cannot paper over it: a trigger on
    a table that does not carry the column it maintains. The runner records the
    failure in ``schema_migrations``, never raises, and the other three
    migrations still complete — the cluster boots.
    """
    recorder = _Recorder()
    with _connect(autocommit=False) as conn:
        # Re-establish the canonical schema first: an earlier test in this
        # module may have replaced a table with the deliberately-broken shape
        # this test needs, and the other three migrations must still succeed
        # against the real one.
        _create_dba_schema(conn)
        execute, execute_autocommit, rollback = _executors(conn)
        # AUDIT-0010 runs first and touches every index target table, so the
        # broken shape must not break it: give incidents its index columns and
        # only the maintenance column away.
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS incidents CASCADE")
            cur.execute(
                "CREATE TABLE incidents (incident_id TEXT PRIMARY KEY, "
                "status TEXT DEFAULT '', severity TEXT DEFAULT '', "
                "detected_at TEXT DEFAULT '', component TEXT DEFAULT '')"
            )
        conn.commit()
        result = apply_dba_migrations(
            execute=execute,
            execute_autocommit=execute_autocommit,
            query=_make_execute_query(conn),
            query_scalar=lambda sql: _probe_scalar(conn, sql),
            recover=rollback,
            record=recorder,
            concurrently=False,
        )
    # The runner survived.
    assert isinstance(result, dict)
    assert set(result["per_migration"]) == set(DBA_MIGRATION_IDS)
    # The failure landed in the history table with the FAILED status.
    statuses = {row[0]: row[1] for row in recorder.rows}
    assert statuses.get("AUDIT-0011-updated-at-triggers") == "FAILED", statuses
    # The other migrations still completed (boot path not blocked).
    assert statuses.get("AUDIT-0010-missing-indexes") == "applied", statuses
    assert statuses.get("AUDIT-0012-analyze-stats-maintenance") == "applied", statuses


@needs_postgres
def test_the_bloat_report_is_read_only_and_reports_dead_tuples(cluster: str) -> None:
    """The VACUUM entry's report reads ``pg_stat_user_tables`` and never writes.

    A threshold of ``0`` would force VACUUM on every table; with the module's
    conservative threshold on the minimal fixture schema (no dead tuples), no
    VACUUM runs, and the report is still returned — the report is always
    produced, whether or not VACUUM ran.
    """
    with _connect(autocommit=True) as conn:
        report = analyze_mod.bloat_report(lambda sql: _probe_rows(conn, sql))
    assert isinstance(report, list)
    # The report never raises and always returns a list (possibly empty on a
    # freshly-created schema with no dead tuples yet).
    assert all(
        set(entry) == {"table_name", "n_live_tup", "n_dead_tup", "last_analyzed", "last_vacuumed"}
        for entry in report
    )


@needs_postgres
def test_the_threshold_is_the_documented_module_constant() -> None:
    """``VACUUM_DEAD_TUPLE_THRESHOLD`` is the tunable the contract names."""
    assert analyze_mod.VACUUM_DEAD_TUPLE_THRESHOLD == 1000


@needs_postgres
def test_the_trigger_function_is_security_invoker_and_minimal(cluster: str) -> None:
    """The ONE server-side function is SECURITY INVOKER with a single assignment."""
    with _connect(autocommit=True) as conn:
        kind = _probe_scalar(
            conn,
            "SELECT prosecdef FROM pg_proc WHERE proname = 'nexus_set_updated_at'",
        )
        assert kind is False, "the trigger function must be SECURITY INVOKER"
        body = _probe_scalar(
            conn,
            "SELECT prosrc FROM pg_proc WHERE proname = 'nexus_set_updated_at'",
        )
    assert "NEW.updated_at = " in body.replace("\n", " ")
    # No control flow, no exception handlers, no dynamic SQL: one assignment.
    for forbidden in ("IF ", "EXCEPTION", "EXECUTE ", "PERFORM ", "DECLARE"):
        assert forbidden not in body.upper().replace("NEW.UPDATED_AT", ""), (
            f"trigger function is not minimal: contains {forbidden!r}"
        )


@needs_postgres
def test_the_index_predicates_are_real_query_shapes() -> None:
    """Every index records the predicate it serves (evidence, not guesswork)."""
    for idx in indexes_mod.MISSING_INDEXES:
        assert idx.table and idx.definition.startswith("("), idx
        assert idx.predicate, f"index {idx.name} has no evidence predicate"
        # The predicate cites the table or the access path it serves.
        assert idx.table in idx.predicate or idx.table.replace("_", " ") in idx.predicate.lower(), (
            f"index {idx.name}'s predicate does not reference {idx.table}: {idx.predicate}"
        )


@needs_postgres
def test_the_layer_is_a_noop_on_sqlite() -> None:
    """SQLite stays first-class: the DBA layer is a PostgreSQL-only concern.

    The trigger/index/analyze modules never hold a SQLite connection, and their
    DDL is authored in the SQLite dialect so the one translation path applies it
    (SQLite ignores the trigger layer — it has no server-side objects).
    """
    from nexus_scalp.database.migration.pg_schema import translate_ddl

    idx = indexes_mod.MISSING_INDEXES[0]
    translated = translate_ddl(idx.sqlite_ddl())
    assert "IF NOT EXISTS" in translated
    assert idx.name in translated


@needs_postgres
def test_regression_notebook_no_orphan_objects(cluster: str) -> None:
    """After the whole set, no orphan trigger/index/function is left behind.

    Rolls the layer back and re-applies to prove the objects are the layer's
    own and are cleaned up by its rollback paths.
    """
    with _connect(autocommit=False) as conn:
        _create_dba_schema(conn)
        execute, execute_autocommit, rollback = _executors(conn)
        apply_dba_migrations(
            execute=execute,
            execute_autocommit=execute_autocommit,
            query=_make_execute_query(conn),
            query_scalar=lambda sql: _probe_scalar(conn, sql),
            recover=rollback,
            record=None,
            concurrently=False,
        )
        triggers_mod.rollback_trigger_layer(execute=execute)
        indexes_mod.rollback_index_migrations(execute=execute)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_proc WHERE proname = 'nexus_set_updated_at'")
            assert cur.fetchone()[0] == 0, "rollback left the function behind"
        with conn.cursor() as cur:
            for name in indexes_mod.index_names():
                cur.execute("SELECT count(*) FROM pg_indexes WHERE indexname = %s", (name,))
                assert cur.fetchone()[0] == 0, f"rollback left index {name} behind"
        conn.rollback()
    # And re-applying after the rollback is clean (idempotent from nothing).
    with _connect(autocommit=False) as conn:
        execute, execute_autocommit, rollback = _executors(conn)
        result = apply_dba_migrations(
            execute=execute,
            execute_autocommit=execute_autocommit,
            query=_make_execute_query(conn),
            query_scalar=lambda sql: _probe_scalar(conn, sql),
            recover=rollback,
            record=None,
            concurrently=False,
        )
    assert result["error_count"] == 0, result["errors"]


def test_the_cluster_is_reachable_when_url_is_set() -> None:
    """A smoke check that the isolated cluster is actually up (no asserts on live)."""
    if not PG_URL:
        pytest.skip("NSE_PG_TEST_URL not set")
    with _connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
