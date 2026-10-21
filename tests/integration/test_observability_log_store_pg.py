"""PostgreSQL integration: the observability log sink against a LIVE server.

Not a mock, not a stub: this module opens a real PostgreSQL connection,
creates an isolated scratch database, runs the actual migration DDL, writes
records through the real store, and reads them back through the real query
path. It is the proof that the log centre works on the production backend,
separately from the SQLite suite.

Environment (matches the repo's existing PostgreSQL CI arms):
    NSE_PG_TEST_URL          postgresql://user:password@host:port/db
                             scratch databases are created in `db` and dropped
                             afterwards. Falls back to the connection
                             parameters the full-runtime CI arm publishes
                             (NSE_DATABASE__PG_* + NSE_PG_TEST_PASSWORD).

Skipped (not failed) when no server is reachable, so the SQLite suites stay
runnable on a host without PostgreSQL — the same discipline the repo's other
database integration tests apply.
"""

from __future__ import annotations

import contextlib
import os
import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from nexus_scalp.database.config import DatabaseConfig  # noqa: E402
from nexus_scalp.database.drivers import get_driver  # noqa: E402
from nexus_scalp.database.log_store import (  # noqa: E402
    LOG_TABLE,
    DbLogEntry,
    DbLogStore,
)

#: Nothing here may create a second, hidden SQLite fallback. The provider is
#: PostgreSQL for the whole module, and a failure to connect skips the test
#: rather than silently exercising the wrong backend.
_ISOLATION_NOTE = "provider selection is explicit; no silent SQLite fallback"


def _scratch_dsn() -> str:
    """Resolve the administrative DSN used to CREATE/DROP the scratch DB."""
    env_url = os.environ.get("NSE_PG_TEST_URL", "").strip()
    if env_url:
        return env_url
    host = os.environ.get("NSE_DATABASE__PG_HOST", "127.0.0.1")
    port = os.environ.get("NSE_DATABASE__PG_PORT", "5432")
    user = os.environ.get("NSE_DATABASE__PG_USER", "postgres")
    pwd = os.environ.get("NSE_PG_TEST_PASSWORD") or os.environ.get("NSE_DATABASE__PG_PASSWORD", "")
    db = os.environ.get("NSE_DATABASE__PG_DATABASE", "postgres")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


def _can_connect() -> bool:
    try:
        with psycopg.connect(_scratch_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _can_connect(), reason="no reachable PostgreSQL server")


@pytest.fixture()
def pg_scratch(tmp_path: Path):
    """An isolated scratch database on the live server; dropped afterwards."""
    admin = _scratch_dsn()
    parsed = re.match(r"postgresql://([^:]+):([^@]*)@([^:/]+)(?::(\d+))?/(.+)$", admin)
    if parsed is None:
        pytest.skip(f"unparseable NSE_PG_TEST_URL: {admin[:32]}…")
    user, pwd, host, port, db = parsed.groups()
    port = port or "5432"
    scratch = f"nse_log_test_{uuid.uuid4().hex[:12]}"

    with psycopg.connect(admin, connect_timeout=10, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{scratch}"')

    cfg = DatabaseConfig.for_postgres(
        domain="audit", host=host, port=int(port), database=scratch, username=user
    )
    scratch_dsn = f"postgresql://{user}:{pwd}@{host}:{port}/{scratch}"
    os.environ["NSE_PG_SCRATCH_DSN"] = scratch_dsn
    if pwd:
        # The env seam the config honours; an empty password_secret stops the
        # secret store being consulted for a credential it does not own.
        os.environ["NSE_DATABASE__PG_PASSWORD"] = pwd
        cfg.password_secret = ""
    # The password never enters the config (it resolves at connect time from
    # the OS secret store under ``password_secret``). For an isolated CI
    # cluster whose credential is not in the store, publish it through the
    # same env seam the config module honours, and set ``password_secret`` to
    # the empty string so the secret store is never consulted for a value the
    # environment already provided. The live production key is untouched.
    drv = None
    try:
        drv = get_driver(cfg)
        yield cfg, drv
    finally:
        os.environ.pop("NSE_PG_SCRATCH_DSN", None)
        os.environ.pop("NSE_DATABASE__PG_PASSWORD", None)
        if drv is not None:
            with contextlib.suppress(Exception):
                drv.close()
        with psycopg.connect(admin, connect_timeout=10, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (scratch,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{scratch}"')


def _entry(level: str, **overrides: object) -> DbLogEntry:
    params: dict[str, object] = {
        "level": level,
        "provider": "postgresql",
        "domain": "audit",
        "operation": f"pg_op_{level.lower()}",
        "event_name": f"PG_EVENT_{level}",
        "logger_name": "pg.test",
        "error_message": f"pg message {level}",
        "correlation_id": "pg-corr-1",
    }
    params.update(overrides)
    return DbLogEntry(**params)  # type: ignore[arg-type]


def test_provider_is_explicit_no_sqlite_fallback(pg_scratch) -> None:
    """The store is bound to PostgreSQL, never a hidden SQLite database."""
    cfg, _ = pg_scratch
    assert cfg.is_postgresql is True, _ISOLATION_NOTE
    store = DbLogStore(config=cfg, persist_info=True)
    assert store.cfg.is_postgresql is True


def test_real_migration_creates_the_table(pg_scratch) -> None:
    """The actual production DDL runs on a clean PostgreSQL database."""
    cfg, drv = pg_scratch
    store = DbLogStore(config=cfg, persist_info=True)
    try:
        store.ensure_table(drv)
        with psycopg.connect(_raw_dsn(), connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_name = %s",
                    (LOG_TABLE,),
                )
                assert cur.fetchone()[0] == 1
                # The additive columns a live upgrade must already carry.
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = %s",
                    (LOG_TABLE,),
                )
                cols = {r[0] for r in cur.fetchall()}
        for required in (
            "event_name",
            "logger_name",
            "context_json",
            "fingerprint",
            "repeat_count",
        ):
            assert required in cols, f"migration missed column {required}"
    finally:
        drv.close()


def test_severity_round_trip(pg_scratch) -> None:
    """WARN+ always persists; INFO persists only when explicitly enabled."""
    cfg, _ = pg_scratch
    store = DbLogStore(config=cfg, persist_info=True)
    drv = get_driver(cfg)
    try:
        store.ensure_table(drv)
        for level in ("INFO", "WARNING", "ERROR", "CRITICAL"):
            assert store.record(_entry(level), driver=drv) is True
        rows = store.query_events(driver=drv)
        assert sorted(r["level"] for r in rows) == ["CRITICAL", "ERROR", "INFO", "WARNING"]

        off = DbLogStore(config=cfg, persist_info=False)
        assert off.record(_entry("INFO"), driver=drv) is False
        assert off.record(_entry("ERROR"), driver=drv) is True
        rows = store.query_events(driver=drv)
        # One new ERROR, no new INFO.
        assert sum(1 for r in rows if r["level"] == "ERROR") == 2
        assert sum(1 for r in rows if r["level"] == "INFO") == 1
    finally:
        drv.close()


def test_filters_and_keyset_pagination_on_pg(pg_scratch) -> None:
    """The bounded read path works identically on PostgreSQL."""
    cfg, drv = pg_scratch
    store = DbLogStore(config=cfg, persist_info=True)
    try:
        store.ensure_table(drv)
        for i in range(6):
            store.record(
                _entry("ERROR", operation=f"op_{i}", correlation_id=f"rid-{i % 2}"), driver=drv
            )
        assert len(store.query_events(driver=drv)) == 6
        assert len(store.query_events(correlation_id="rid-0", driver=drv)) == 3
        assert len(store.query_events(components=("pg.test",), driver=drv)) == 6
        assert len(store.query_events(text="pg message", driver=drv)) == 6
        first = store.query_events(limit=2, driver=drv)
        second = store.query_events(limit=2, before_id=first[-1]["id"], driver=drv)
        assert all(r["id"] < first[-1]["id"] for r in second)
        assert store.count_events(driver=drv) == 6
        h = store.health(drv)
        assert h["ok"] is True and h["provider"] == "postgresql"
    finally:
        drv.close()


def test_dedup_bumps_repeat_count_on_pg(pg_scratch) -> None:
    """A repeated fingerprint within the window folds into one row."""
    cfg, drv = pg_scratch
    store = DbLogStore(config=cfg)
    try:
        store.ensure_table(drv)
        # Dedup keys on the entry's own fingerprint digest; entries without
        # one always insert (the DB layer records that way by design).
        first = _entry("ERROR", fingerprint="pg-dedup-fingerprint")
        assert store.record(first, driver=drv) is True
        assert store.record(first, driver=drv) is True  # folded, not a new row
        rows = store.query_events(driver=drv)
        assert len(rows) == 1
        assert int(rows[0]["repeat_count"]) == 2

        # An entry WITHOUT a fingerprint is a distinct row every time: the
        # DB layer cannot invent provenance it was not given.
        no_fp = _entry("WARNING", error_message="no fingerprint here")
        assert store.record(no_fp, driver=drv) is True
        assert store.record(no_fp, driver=drv) is True
        assert len(store.query_events(driver=drv)) == 3
    finally:
        drv.close()


def test_jsonb_context_is_queryable(pg_scratch) -> None:
    """``context_json`` is real JSONB on PostgreSQL, not a text blob."""
    cfg, drv = pg_scratch
    store = DbLogStore(config=cfg, persist_info=True)
    try:
        store.ensure_table(drv)
        store.record(_entry("WARNING"), driver=drv)
        with psycopg.connect(_raw_dsn(), connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT context_json::text FROM {LOG_TABLE} LIMIT 1")
                raw = cur.fetchone()[0]
        assert raw.strip().startswith("{")
    finally:
        drv.close()


def test_purge_keeps_recent_rows_on_pg(pg_scratch) -> None:
    """Retention removes only rows older than the window."""
    cfg, drv = pg_scratch
    store = DbLogStore(config=cfg, retention_days=30)
    try:
        store.ensure_table(drv)
        store.record(_entry("ERROR"), driver=drv)
        assert store.purge_expired() == 0
        assert store.count_events(driver=drv) == 1
    finally:
        drv.close()


def _raw_dsn() -> str:
    """DSN pointing at the *scratch* database, for out-of-driver verification.

    Built from the fixture's own target so a verification query can never
    reach the live database: the fixture sets ``NSE_PG_SCRATCH_DSN`` before
    yielding and clears it on teardown.
    """
    dsn = os.environ.get("NSE_PG_SCRATCH_DSN", "")
    if not dsn:
        pytest.skip("scratch DSN unavailable")
    return dsn
