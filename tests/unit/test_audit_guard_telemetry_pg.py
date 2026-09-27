"""Lane A — the audit_guard_telemetry counter upsert on real PostgreSQL.

THE LIVE FAILURE (99.9% of every failed write on the deployed cluster):

    n=18281  "column reference "count" is ambiguous"
             LINE 5: DO UPDATE SET count = count + 1

The statement is ``audit_repository._log_guard_telemetry``:

    INSERT INTO audit_guard_telemetry AS t (window_start, symbol, reason_code, count)
    VALUES (?, ?, ?, 1)
    ON CONFLICT(window_start, symbol, reason_code)
    DO UPDATE SET count = count + 1

PostgreSQL resolves the bare ``count`` in ``DO UPDATE SET`` against BOTH the
target row and ``excluded.count`` and refuses. SQLite resolves it to the target
table, which is why the statement was correct on SQLite and dead-lettered
~19 rows/second on the deployed provider.

THE FIX is the qualification ``t.count + 1``, using the alias the INSERT
already declares. It is valid on BOTH providers (SQLite resolves an alias
declared in the INSERT), so the statement string stays identical for every
provider — the best kind of portability fix.

NOTE on the accumulator direction: ``DO UPDATE SET count = t.count + 1`` reads
the EXISTING row's count and increments it. ``excluded.count`` would instead
read the PROPOSED row, which always carries the literal 1 — that would RESET
the counter to 2 on every conflict instead of accumulating. This suite pins
the accumulating semantics explicitly.

CONVENTION: follows the suite's PostgreSQL arm
(``tests/unit/test_database_portability.py``): the URL comes from
``NSE_PG_TEST_URL`` and the module skips cleanly when it is unset.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.adapters.database.audit_write_plane import (  # noqa: E402
    _count_placeholders,
    translate_sql,
)
from nexus_scalp.database.drivers.postgres_driver import (  # noqa: E402
    _translate_placeholders,
)

psycopg = pytest.importorskip("psycopg")

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_postgres = pytest.mark.skipif(
    not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL CI test arm)"
)

#: The producer's statement, verbatim from _log_guard_telemetry.
COUNTER = """
    INSERT INTO audit_guard_telemetry AS t (window_start, symbol, reason_code, count)
    VALUES (?, ?, ?, 1)
    ON CONFLICT(window_start, symbol, reason_code)
    DO UPDATE SET count = t.count + 1
"""

_SCHEMA = """
    CREATE TABLE audit_guard_telemetry (
        window_start TEXT NOT NULL,
        symbol       TEXT NOT NULL,
        reason_code  TEXT NOT NULL,
        count        BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (window_start, symbol, reason_code)
    )
"""


def _create_scratch_table(cur: Any) -> None:
    """A per-session scratch copy of the table, with the producer's PK intact.

    ``CREATE TEMP TABLE ... (LIKE ...) INCLUDING ALL`` copies the columns AND
    the PRIMARY KEY the ``ON CONFLICT(window_start, symbol, reason_code)``
    clause targets — the constraint the statement needs to resolve. Session
    scope + a rollback means the live domain is never written to.

    The base table is created with ``IF NOT EXISTS`` so the helper is
    re-runnable inside one session: the suite's two tests share a connection
    per test (not per session), and a plain ``CREATE TABLE`` raises
    ``DuplicateTable`` on the second — which would report a pass on the FIRST
    run of a fresh database and a hard failure on every run after it.
    """
    cur.execute(_SCHEMA.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
    cur.execute(
        "CREATE TEMP TABLE IF NOT EXISTS audit_guard_telemetry "
        "(LIKE audit_guard_telemetry INCLUDING ALL)"
    )


class TestGuardTelemetryStatementShape:
    """Provider-agnostic properties of the fixed statement (no server needed)."""

    def test_the_bare_ambiguous_form_is_gone(self) -> None:
        """The ``count = count + 1`` shape that PostgreSQL rejects."""
        flat = " ".join(COUNTER.split())
        assert "count = count + 1" not in flat
        assert "count = t.count + 1" in flat

    def test_arity_holds_after_translation(self) -> None:
        """3 real args; the ``1`` and the DO UPDATE expression are not parameters."""
        plane_sql = translate_sql(COUNTER)
        assert _count_placeholders(plane_sql) == 3
        # The driver's ? -> %s swap is 1:1 outside literals.
        assert _translate_placeholders(plane_sql).count("%s") == 3

    def test_the_alias_is_declared_by_the_insert(self) -> None:
        """``t.count`` resolves because ``AS t`` is declared in the INSERT.

        SQLite resolves an alias declared in the INSERT statement, so the
        qualified form stays valid on both providers — no provider split.
        """
        flat = " ".join(COUNTER.split())
        assert "INSERT INTO audit_guard_telemetry AS t" in flat

    def test_translation_leaves_the_statement_alone(self) -> None:
        """The statement is already portable — translate_sql must not rewrite it."""
        assert translate_sql(COUNTER) == COUNTER


@needs_postgres
class TestGuardTelemetryOnPostgreSQL:
    """The statement parses and accumulates on the real provider."""

    def test_counter_accumulates_across_repeated_events(self) -> None:
        window = "1999-01-01T00:00"
        with psycopg.connect(PG_URL, connect_timeout=10) as conn:
            try:
                with conn.cursor() as cur:
                    _create_scratch_table(cur)
                    stmt = _translate_placeholders(COUNTER)
                    cur.execute(stmt, (window, "TESTLANEA", "guard"))
                    cur.execute(
                        "SELECT count FROM audit_guard_telemetry "
                        "WHERE window_start=%s AND symbol=%s AND reason_code=%s",
                        (window, "TESTLANEA", "guard"),
                    )
                    assert cur.fetchone()[0] == 1

                    # The UPDATE arm — the one that was ambiguous on the cluster.
                    for expected in (2, 3, 4, 5):
                        cur.execute(stmt, (window, "TESTLANEA", "guard"))
                        cur.execute(
                            "SELECT count FROM audit_guard_telemetry "
                            "WHERE window_start=%s AND symbol=%s AND reason_code=%s",
                            (window, "TESTLANEA", "guard"),
                        )
                        assert cur.fetchone()[0] == expected
            finally:
                conn.rollback()

    def test_distinct_windows_do_not_collide(self) -> None:
        with psycopg.connect(PG_URL, connect_timeout=10) as conn:
            try:
                with conn.cursor() as cur:
                    _create_scratch_table(cur)
                    stmt = _translate_placeholders(COUNTER)
                    cur.execute(stmt, ("1999-01-01T00:00", "EURUSD", "A"))
                    cur.execute(stmt, ("1999-01-01T00:01", "EURUSD", "A"))
                    cur.execute(stmt, ("1999-01-01T00:00", "EURUSD", "A"))
                    cur.execute("SELECT COUNT(*) FROM audit_guard_telemetry")
                    assert cur.fetchone()[0] == 2
                    cur.execute(
                        "SELECT count FROM audit_guard_telemetry "
                        "WHERE window_start=%s AND symbol=%s AND reason_code=%s",
                        ("1999-01-01T00:00", "EURUSD", "A"),
                    )
                    assert cur.fetchone()[0] == 2
            finally:
                conn.rollback()
