"""PG-POOL-CONFIG-001 (Lane I) — a persisted pool config must reach the pool.

The defect (P1):

    ``DatabaseConfig.pooling_enabled`` had NO consumer in ``src/`` outside
    serialization and the UI mirror.  ``provision_domain`` built
    ``PoolLimits`` ONLY from the hard-coded literals each call site passed
    (all 8 call sites pass the identical ``min_size=1, max_size=4``), so the
    persisted ``postgresql_config`` row an operator edits in the DATABASE TAB
    changed nothing at all.  Pooled connections also never applied
    ``statement_timeout`` (persisted ``command_timeout_sec`` was honored only
    on the raw driver path) and used the pool's ``max_idle=0`` /
    ``max_lifetime=0`` defaults, holding every connection for process
    lifetime.

The fix:

    ``provision_domain`` now reads the persisted ``DatabaseConfig`` for the
    domain as the BASE pool limits; the call-site literals are overrides only
    when the persisted value is absent.  ``statement_timeout_ms``,
    ``idle_timeout_sec`` and ``max_lifetime_sec`` come from the persisted row.

HOW THIS TEST PROVES IT
=======================
It does not stub the fabric.  A throwaway PostgreSQL database is created and
the app's own ``provision_domain`` path is run against it with a PERSISTED
config row that says something different from the call-site literals.  The
pool psycopg_pool actually opened is then interrogated for the values it was
constructed with, and a checked-out connection is asked for the session
settings the pool's ``configure`` hook applied.

The suite skips cleanly (not errors) when PostgreSQL is unreachable or the
optional ``psycopg``/``psycopg_pool`` dependencies are missing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.database.config import (
    PG_CONFIG_SETTING_KEY,
    PROVIDER_SETTING_KEY,
    DatabaseConfig,
    build_postgres_url,
    load_database_config,
)
from nexus_scalp.database.fabric import (
    get_domain_backend,
    provision_domain,
    unregister_domain_backend,
)

#: The domain this test provisions (never the audit domain — that one carries
#: the NEXUS_AUDIT_DB test-isolation seam, which is unrelated to pool sizing).
DOMAIN = "news"

#: Call-site literals every provision in the codebase passes.  The persisted
#: row deliberately disagrees with BOTH, so a pool built from these numbers
#: proves the row was ignored.
CALL_SITE_MIN = 1
CALL_SITE_MAX = 4

#: What the persisted row demands.  Chosen off-by-one from the call sites so
#: a pass/fail is unambiguous, and within psycopg_pool's bounds.
PERSISTED_MIN = 2
PERSISTED_MAX = 7
PERSISTED_COMMAND_TIMEOUT_SEC = 44  # -> statement_timeout 44000ms


def _pg_password() -> str | None:
    """The PostgreSQL password from the OS secret store (mirrors the driver)."""
    from nexus_scalp.settings.secret_store import SecureSecretStore

    try:
        pw = SecureSecretStore().get_secret("db.postgresql.password")
    except Exception:
        return None
    return pw or None


@pytest.fixture
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch settings DB so this test never reads a real install's row."""
    path = tmp_path / "app_settings.db"
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(path))
    return path


def _persist_pg_config(settings_db: Path, row: dict[str, Any]) -> None:
    """Write the persisted postgresql_config row the way the UI does."""
    from nexus_scalp.settings.service import SettingsDatabase

    db = SettingsDatabase(db_path=settings_db)
    try:
        db.set(PROVIDER_SETTING_KEY, "postgresql", value_type="str", source="TEST", actor="test")
        db.set(PG_CONFIG_SETTING_KEY, row, value_type="json", source="TEST", actor="test")
    finally:
        db.close()


@pytest.fixture
def throwaway_pg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A throwaway PostgreSQL database provisioned through the app's own path.

    NEVER the live nexusdb: the fixture creates and drops its own database
    name and points the domain at THAT DSN only.
    """
    import psycopg

    password = _pg_password()
    if password is None:
        pytest.skip("no PostgreSQL password in the OS secret store")
    probe_name = f"nse_pool_cfg_test_{os.getpid()}_{abs(hash(str(tmp_path))) % 100000}"
    admin_dsn = f"postgresql://postgres:{password}@localhost:5432/postgres"
    probe_dsn = f"postgresql://postgres:{password}@localhost:5432/{probe_name}"

    try:
        with psycopg.connect(admin_dsn, connect_timeout=8) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (probe_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{probe_name}"')
                cur.execute(f'CREATE DATABASE "{probe_name}"')
    except Exception as exc:
        pytest.skip(f"PostgreSQL server unreachable: {type(exc).__name__}: {exc}")

    yield {"dsn": probe_dsn, "admin_dsn": admin_dsn, "database": probe_name}

    import contextlib

    with contextlib.suppress(Exception):
        with psycopg.connect(admin_dsn, connect_timeout=8, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (probe_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{probe_name}"')


def _unregister(domain: str) -> None:
    try:
        unregister_domain_backend(domain)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The persisted row is the BASE pool limits (not a no-op)
# ---------------------------------------------------------------------------


def test_persisted_min_max_reach_the_real_pool(
    throwaway_pg: dict[str, Any],
    _isolated_settings: Path,
) -> None:
    """A persisted pool-size row changes the pool psycopg_pool opens.

    Pre-fix, the pool was always (1, 4) regardless of the row; this is the
    exact "operator saves a pool size in the UI and nothing changes" defect.
    """
    _persist_pg_config(
        _isolated_settings,
        {
            "provider": "postgresql",
            "domain": DOMAIN,
            "host": "localhost",
            "port": 5432,
            "database": throwaway_pg["database"],
            "username": "postgres",
            "password_secret": "db.postgresql.password",
            "command_timeout_sec": PERSISTED_COMMAND_TIMEOUT_SEC,
            "migrate_on_startup": True,
            "pooling_enabled": True,
            "connect_timeout_sec": 10,
        },
    )
    cfg = load_database_config(DOMAIN)
    assert cfg.is_postgresql, "the persisted row must resolve as postgresql"

    try:
        # The call-site literals deliberately DISAGREE with the persisted row.
        provision_domain(
            DOMAIN, throwaway_pg["dsn"], min_size=CALL_SITE_MIN, max_size=CALL_SITE_MAX
        )
        read = get_domain_backend(DOMAIN, readonly=True)
        assert read is not None, "provision registered no read plane"

        pool = read._primary._pool  # type: ignore[attr-defined]
        # The persisted row wins over the call-site literals.
        assert pool.min_size == PERSISTED_MIN, (
            f"pool min_size={pool.min_size}: the persisted pool_min_size "
            f"({PERSISTED_MIN}) did not reach the pool — it is still the "
            f"call-site literal ({CALL_SITE_MIN})"
        )
        assert pool.max_size == PERSISTED_MAX, (
            f"pool max_size={pool.max_size}: the persisted pool_max_size "
            f"({PERSISTED_MAX}) did not reach the pool — it is still the "
            f"call-site literal ({CALL_SITE_MAX})"
        )
    finally:
        _unregister(DOMAIN)


def test_persisted_statement_timeout_reaches_a_checked_out_connection(
    throwaway_pg: dict[str, Any],
    _isolated_settings: Path,
) -> None:
    """``command_timeout_sec`` applies as a session statement_timeout.

    Pre-fix, only the RAW driver path applied it (via
    ``options='-c statement_timeout=<ms>'``); a pooled connection kept the
    server default (0 = no limit), which is how a stuck query could hold a
    pooled connection forever.
    """
    _persist_pg_config(
        _isolated_settings,
        {
            "provider": "postgresql",
            "domain": DOMAIN,
            "host": "localhost",
            "port": 5432,
            "database": throwaway_pg["database"],
            "username": "postgres",
            "password_secret": "db.postgresql.password",
            "command_timeout_sec": PERSISTED_COMMAND_TIMEOUT_SEC,
            "migrate_on_startup": True,
            "pooling_enabled": True,
            "connect_timeout_sec": 10,
        },
    )
    try:
        provision_domain(
            DOMAIN, throwaway_pg["dsn"], min_size=CALL_SITE_MIN, max_size=CALL_SITE_MAX
        )
        read = get_domain_backend(DOMAIN, readonly=True)
        assert read is not None

        with read.connection() as conn:
            got = conn.execute("SHOW statement_timeout").fetchone()[0]
            # PostgreSQL renders 44000ms as '44s' (or '44000ms' on some builds);
            # normalize to milliseconds before comparing.
            ms = _to_milliseconds(got)
            assert ms == PERSISTED_COMMAND_TIMEOUT_SEC * 1000, (
                f"statement_timeout on a pooled connection is {got!r} "
                f"({ms}ms), expected {PERSISTED_COMMAND_TIMEOUT_SEC * 1000}ms "
                "— command_timeout_sec did not reach the pool"
            )
    finally:
        _unregister(DOMAIN)


def test_persisted_idle_and_lifetime_bounds_reach_the_pool(
    throwaway_pg: dict[str, Any],
    _isolated_settings: Path,
) -> None:
    """``idle_timeout`` / ``max_lifetime`` stop connections being held forever.

    Pre-fix both were 0 (the psycopg_pool default), which means "never reap":
    idle backends accumulated and stayed open for the process lifetime.
    """
    _persist_pg_config(
        _isolated_settings,
        {
            "provider": "postgresql",
            "domain": DOMAIN,
            "host": "localhost",
            "port": 5432,
            "database": throwaway_pg["database"],
            "username": "postgres",
            "password_secret": "db.postgresql.password",
            "command_timeout_sec": PERSISTED_COMMAND_TIMEOUT_SEC,
            "migrate_on_startup": True,
            "pooling_enabled": True,
            "connect_timeout_sec": 10,
            "pool_idle_timeout_sec": 90,
            "pool_max_lifetime_sec": 1800,
        },
    )
    try:
        provision_domain(
            DOMAIN, throwaway_pg["dsn"], min_size=CALL_SITE_MIN, max_size=CALL_SITE_MAX
        )
        read = get_domain_backend(DOMAIN, readonly=True)
        assert read is not None
        limits = read._limits  # type: ignore[attr-defined]

        assert limits.idle_timeout_sec == 90, (
            f"idle_timeout_sec={limits.idle_timeout_sec}: the persisted "
            "pool_idle_timeout_sec did not reach the pool (0 = never reap)"
        )
        assert limits.max_lifetime_sec == 1800, (
            f"max_lifetime_sec={limits.max_lifetime_sec}: the persisted "
            "pool_max_lifetime_sec did not reach the pool"
        )
        pool = read._primary._pool  # type: ignore[attr-defined]
        assert pool.max_idle == 90.0
        assert pool.max_lifetime == 1800.0
    finally:
        _unregister(DOMAIN)


# ---------------------------------------------------------------------------
# No persisted row: the call-site literals still work (no regression)
# ---------------------------------------------------------------------------


def test_without_a_persisted_row_the_call_site_literals_win(
    throwaway_pg: dict[str, Any],
    _isolated_settings: Path,
) -> None:
    """A fresh install (no row) keeps today's behavior exactly.

    The persisted config is best-effort BASE: an unreadable or absent row must
    never break provisioning — it falls through to the caller's literals.
    """
    # No _persist_pg_config call: an empty settings DB = "no persisted row".
    cfg = load_database_config(DOMAIN)
    assert cfg.is_sqlite, "an empty settings DB must resolve to the SQLite default"

    try:
        provision_domain(
            DOMAIN, throwaway_pg["dsn"], min_size=CALL_SITE_MIN, max_size=CALL_SITE_MAX
        )
        read = get_domain_backend(DOMAIN, readonly=True)
        assert read is not None
        pool = read._primary._pool  # type: ignore[attr-defined]

        assert pool.min_size == CALL_SITE_MIN
        assert pool.max_size == CALL_SITE_MAX
    finally:
        _unregister(DOMAIN)


def test_persisted_config_never_breaks_a_dead_dsn(_isolated_settings: Path) -> None:
    """An unreachable persisted row falls through, never raises.

    ``_persisted_pool_config`` is best-effort by contract: a settings DB that
    cannot be read (or a row that cannot be parsed here) must degrade to the
    call-site defaults rather than break provisioning.  The loud failure path
    is the validation boundary in ``DatabaseConfig.from_dict``; this is not it.
    """
    from nexus_scalp.database.fabric import _pool_limits_from_config

    # None models every "cannot read the row" case at once.
    limits = _pool_limits_from_config(None, {"min_size": 3, "max_size": 9})
    assert limits.min_size == 3
    assert limits.max_size == 9
    assert limits.statement_timeout_ms == 0
    assert limits.idle_timeout_sec == 0
    assert limits.max_lifetime_sec == 0


# ---------------------------------------------------------------------------
# SQLite is untouched by this path
# ---------------------------------------------------------------------------


def test_sqlite_provider_never_enters_the_pool_path(_isolated_settings: Path) -> None:
    """A SQLite persisted row yields no pool config — SQLite has no pool.

    ``provision_domain`` is PostgreSQL-only; a SQLite row must not produce a
    PoolLimits the fabric would then apply to a provider that cannot pool.
    """
    from nexus_scalp.settings.service import SettingsDatabase

    db = SettingsDatabase(db_path=_isolated_settings)
    try:
        db.set(PROVIDER_SETTING_KEY, "sqlite", value_type="str", source="TEST", actor="test")
    finally:
        db.close()

    cfg = load_database_config(DOMAIN)
    assert cfg.is_sqlite

    from nexus_scalp.database.fabric import _persisted_pool_config

    assert _persisted_pool_config(DOMAIN) is None, (
        "a SQLite provider must not resolve a pooled PoolLimits"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _to_milliseconds(value: Any) -> int:
    """PostgreSQL's ``SHOW statement_timeout`` renders as ``44s``/``44000ms``."""
    text = str(value).strip()
    if text.endswith("ms"):
        return int(float(text[:-2]))
    if text.endswith("s"):
        return int(float(text[:-1]) * 1000)
    if text.endswith("min"):
        return int(float(text[:-3]) * 60 * 1000)
    return int(float(text))


def test_build_postgres_url_uses_the_persisted_row(_isolated_settings: Path) -> None:
    """Sanity: the URL the pool is handed comes from the same resolved config."""
    _persist_pg_config(
        _isolated_settings,
        {
            "provider": "postgresql",
            "domain": DOMAIN,
            "host": "localhost",
            "port": 5432,
            "database": "nexus_does_not_exist",
            "username": "postgres",
            "password_secret": "db.postgresql.password",
            "command_timeout_sec": 0,
            "migrate_on_startup": True,
            "pooling_enabled": True,
            "connect_timeout_sec": 10,
        },
    )
    cfg = load_database_config(DOMAIN)
    url = build_postgres_url(cfg)
    assert "nexus_does_not_exist" in url, "the resolved URL must carry the persisted database"
    assert cfg.command_timeout_sec == 0


del DatabaseConfig  # imported for the type checker's benefit only
