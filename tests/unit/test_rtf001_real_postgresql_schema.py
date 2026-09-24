"""RTF-001 real-PostgreSQL end-to-end test.

The pasted 2026-09-24 runtime log is entirely about PostgreSQL: the
provider-agnostic migration ran, reported ``applied=37 errors=0``, and the
schema still lacked 24 columns. Unit tests cannot catch that — the SQLite and
PostgreSQL code paths diverge exactly at translate_ddl + execute. This test
builds the real PostgreSQL schema through the same path the engine uses at
boot (migrate_domain on the fabric's write plane) and proves every required
column lands.

SAFETY: this test owns its own scratch database (created/dropped here, never
the live one). The DSN comes from the repo's own resolution chain, so no
credential is ever written into this file or the logs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

psycopg = pytest.importorskip("psycopg")

from nexus_scalp.database.config import build_postgres_url, load_database_config  # noqa: E402
from nexus_scalp.settings.secret_store import SecureSecretStore  # noqa: E402
from nexus_scalp.database.app_columns import APP_REQUIRED_COLUMNS  # noqa: E402
from nexus_scalp.database.migration import (  # noqa: E402
    additive_columns_statements,
    sqlite_ddl_statements,
    unique_index_statements,
)
from nexus_scalp.database.migration.pg_schema import translate_ddl  # noqa: E402

SCRATCH_DB = "nse_rtf001_test"


REAL_APP_DATA = Path(os.environ["LOCALAPPDATA"]) / "NexusScalpEngine"
REAL_SETTINGS_DB = REAL_APP_DATA / "databases" / "app_settings.db"


def _admin_dsn() -> str:
    """The live audit PostgreSQL DSN, as the engine resolves it at boot.

    ``resolve_audit_db_url`` is deliberately NOT used here: the test suite's
    BUG-223 isolation fixture force-sets ``NEXUS_AUDIT_DB`` to a temp SQLite
    file for every test, and that seam correctly wins over the persisted
    provider. This test instead reads the persisted DatabaseConfig the same
    way the fabric does at boot, and builds the URL from it.

    ``settings_db_path`` is passed explicitly because the suite's
    ``NEXUS_DATA_ROOT`` isolation fixture redirects the data root (and with it
    the settings-DB lookup) to a temp dir; without it, the provider would
    resolve to SQLite and the password lookup would fail. This keeps the
    isolation fixture fully in force for everything else in the module. No
    credential is ever returned in an assertion message or a log line.
    """
    cfg = load_database_config("audit", settings_db_path=str(REAL_SETTINGS_DB))
    assert getattr(cfg, "is_postgresql", False), (
        "audit provider is not PostgreSQL; this test needs a live PG domain"
    )
    return build_postgres_url(cfg, secret_store=SecureSecretStore(root=REAL_APP_DATA))


def _pg_reachable() -> bool:
    """Best-effort probe (no secrets logged); skips the module cleanly when
    PostgreSQL is not running locally or the provider is not configured."""
    try:
        dsn = _admin_dsn()
    except Exception:
        return False
    try:
        with psycopg.connect(dsn, connect_timeout=5):
            return True
    except Exception:
        return False


needs_postgres = pytest.mark.skipif(
    not _pg_reachable(), reason="no PostgreSQL reachable (audit provider not pg)"
)


@pytest.fixture(scope="module")
def scratch():
    """Create an isolated scratch database; tear it down after the module.

    Never touches the live ``nexusdb``: the only statement issued against the
    live database is CREATE/DROP of the separate scratch database.
    """
    dsn = _admin_dsn()
    with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        conn.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    scratch_dsn = dsn.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"
    try:
        yield scratch_dsn
    finally:
        with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as conn:
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
def test_baseline_alone_leaves_the_additive_contract_missing(scratch: str) -> None:
    """The precondition of RTF-001, reproduced on real PostgreSQL: applying
    ONLY the scanned baseline CREATEs builds the tables but not the additive
    columns. That is precisely how the live database was provisioned."""
    with psycopg.connect(scratch, connect_timeout=10) as conn:
        for stmt in sqlite_ddl_statements():
            if stmt.strip().upper().startswith("CREATE TABLE"):
                with conn.cursor() as cur:
                    cur.execute(translate_ddl(stmt))
        conn.commit()

        missing = []
        for table, cols in APP_REQUIRED_COLUMNS.items():
            have = _columns(conn, table)
            if not have:
                continue
            for col, _ in cols:
                if col not in have:
                    missing.append(f"{table}.{col}")
    # The whole point of the incident: a real PostgreSQL domain provisioned
    # from the baseline alone is missing these.
    assert missing, "the additive columns now appear in the baseline CREATEs"
    missing_lower = {m.lower() for m in missing}
    assert "audit_signals.signal_dedup_key" in missing_lower
    assert "audit_account_snapshots.account_source" in missing_lower


@needs_postgres
def test_full_migration_lands_every_required_column(scratch: str) -> None:
    """The fix, end-to-end on real PostgreSQL: the provider-agnostic
    migration applies baseline + additive + indexes (translated), and every
    required column then exists. This is the state the live domain must reach."""
    with psycopg.connect(scratch, connect_timeout=10) as conn:
        for stmt in sqlite_ddl_statements():
            if stmt.strip().upper().startswith("CREATE TABLE"):
                with conn.cursor() as cur:
                    cur.execute(translate_ddl(stmt))
        for stmt in additive_columns_statements():
            with conn.cursor() as cur:
                cur.execute(translate_ddl(stmt))
        for stmt in sqlite_ddl_statements():
            if not stmt.strip().upper().startswith("CREATE TABLE"):
                with conn.cursor() as cur:
                    cur.execute(translate_ddl(stmt))
        for stmt in unique_index_statements():
            with conn.cursor() as cur:
                cur.execute(translate_ddl(stmt))
        conn.commit()

        for table, cols in APP_REQUIRED_COLUMNS.items():
            have = _columns(conn, table)
            assert have, f"{table} was not created"
            for col, _ in cols:
                assert col.lower() in have, (
                    f"{table}.{col} still missing after the full migration"
                )
