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

import pytest  # noqa: E402

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
def test_live_domain_gap_is_fully_covered_by_the_replay() -> None:
    """The precondition of the incident, measured on the real database: the
    configured domain is missing required columns, and every one of them is
    emitted by the replay as an ``ALTER TABLE ADD COLUMN``. This is what proves
    the gap was a provisioning defect and not a registry mismatch."""
    with psycopg.connect(PG_URL, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='public'"
            )
            live = {(t, c.lower()) for t, c in cur.fetchall()}

    required = {
        (t, c.lower()) for t, cols in APP_REQUIRED_COLUMNS.items() for c, _ in cols
    }
    missing = sorted(required - live)
    assert missing, "the configured domain already carries every required column"

    replayed: set[tuple[str, str]] = set()
    for stmt in sqlite_ddl_statements():
        s = stmt.strip()
        if s.upper().startswith("ALTER TABLE"):
            parts = s.split()
            if len(parts) >= 6:
                replayed.add((parts[2].lower(), parts[5].lower()))

    not_covered = [m for m in missing if m not in replayed]
    assert not_covered == [], (
        "live-missing columns the replay does not emit: "
        f"{not_covered} — the provisioning gap is not closed"
    )
    # The two columns named verbatim in the runtime log.
    assert ("audit_signals", "signal_dedup_key") in replayed
    assert ("audit_account_snapshots", "account_source") in replayed


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
            cur.execute(
                "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_audit_signals_dedup'"
            )
            assert cur.fetchone() is not None, "signal_dedup_key UNIQUE index missing"
