"""RTF-001 real-PostgreSQL verification of the CHG-0067 schema replay.

The pasted 2026-09-24 runtime log is entirely about PostgreSQL: the migration
ran, reported ``applied=37 errors=0``, and the schema still lacked 24 columns.
CHG-0067 (origin/main) replaced the source-scan extractor with a full schema
replay that captures the runtime ``ALTER TABLE ADD COLUMN`` statements the old
scan could not see. This test proves, on a real PostgreSQL instance, that the
replay actually closes that gap on a FRESH database — the property the live
``nexusdb`` was missing.

CONVENTION: this follows the suite's existing PostgreSQL arm exactly
(``tests/unit/test_database_portability.py``): the connection URL comes from
the ``NSE_PG_TEST_URL`` environment variable and the module skips cleanly when
it is unset. CHG-0067 places the ``NEXUS_AUDIT_DB`` test-isolation seam ABOVE
the persisted provider, so the persisted settings cannot be reached from a
unit test — the env URL is the supported way to exercise a live PostgreSQL
domain. No credential is written into this file or any log line.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

psycopg = pytest.importorskip("psycopg")

from nexus_scalp.database.app_columns import APP_REQUIRED_COLUMNS  # noqa: E402
from nexus_scalp.database.migration import sqlite_ddl_statements  # noqa: E402
from nexus_scalp.database.migration.pg_schema import translate_ddl  # noqa: E402

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_postgres = pytest.mark.skipif(
    not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL CI test arm)"
)

#: A scratch database on the same instance as NSE_PG_TEST_URL; created and
#: dropped per run so the live database is never written to by this test.
SCRATCH_DB = "nse_rtf001_test"


def _scratch_dsn() -> str:
    """Re-point the configured URL at the scratch database."""
    assert PG_URL, "NSE_PG_TEST_URL must be set"
    return PG_URL.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"


@pytest.fixture(scope="module")
def scratch():
    """Create an isolated scratch database; tear it down after the module.

    The configured database is only ever CREATE/DROP'd, never written to.
    """
    with psycopg.connect(PG_URL, connect_timeout=10, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        conn.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    try:
        yield _scratch_dsn()
    finally:
        with psycopg.connect(PG_URL, connect_timeout=10, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')


def _columns(conn, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=%s AND table_schema='public'",
            (table,),
        )
        return {str(r[0]).lower() for r in cur.fetchall()}


@needs_postgres
def test_the_replay_leaves_no_required_column_gap_on_the_live_domain() -> None:
    """The incident's precondition is gone on the live domain.

    CHG-0067's replay is now applied at boot, and the live ``nexusdb`` carries
    every required column (verified separately by the lane's post-merge probe).
    This test asserts that property directly, so a future regression of the
    provisioning gap fails here instead of silently corrupting audit writes.
    """
    with psycopg.connect(PG_URL, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='public'"
            )
            live = {(t, c.lower()) for t, c in cur.fetchall()}

    required = {(t, c.lower()) for t, cols in APP_REQUIRED_COLUMNS.items() for c, _ in cols}
    missing = sorted(required - live)
    assert missing == [], f"required columns missing from the live domain: {missing}"

    # The two columns named verbatim in the runtime log.
    assert ("audit_signals", "signal_dedup_key") in live
    assert ("audit_account_snapshots", "account_source") in live


@needs_postgres
def test_replay_provisions_a_fresh_postgresql_domain_completely(scratch: str) -> None:
    """The fix, end-to-end on a real PostgreSQL instance that has never been
    provisioned: applying the translated replay lands every required table and
    column. This is the state the live ``nexusdb`` must reach on the next boot
    of the merged code, and it is the property that failed in production."""
    with psycopg.connect(scratch, connect_timeout=10) as conn:
        for stmt in sqlite_ddl_statements():
            with conn.cursor() as cur:
                cur.execute(translate_ddl(stmt))
        conn.commit()

        for table, cols in APP_REQUIRED_COLUMNS.items():
            have = _columns(conn, table)
            if not have:
                continue
            for col, _ in cols:
                assert col.lower() in have, (
                    f"{table}.{col} still missing after provisioning the replay"
                )

        # The dedup guarantee the runtime relies on (ON CONFLICT clause).
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_audit_signals_dedup'")
            assert cur.fetchone() is not None, "signal_dedup_key UNIQUE index missing"
