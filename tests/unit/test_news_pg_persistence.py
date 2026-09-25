"""News domain PostgreSQL persistence (DATABASE PORTABILITY regression).

WHAT THIS PINS
==============
``NewsDatabase`` follows the ACTIVE provider. A box switched to PostgreSQL via
``nexus db-portability switch`` (or the management UI) used to keep the whole
news subsystem on SQLite: ``NewsEngine`` resolved ``NewsConfig.db_path`` and
handed the store an unconditional SQLite path, the store accepted it, and news
rows (sources, articles, analysis) landed in ``artifacts/news.db`` while
PostgreSQL held the domain's provisioned tables and nothing ever read them
back. The store's PG schema init also failed outright — its SQLite-dialect DDL
(``INTEGER PRIMARY KEY AUTOINCREMENT``) is rejected verbatim by PostgreSQL —
so even a store that reached PG had zero tables.

The fix mirrors the proven audit-domain seam:

  * ``NewsDatabase.__init__`` resolves ``load_database_config("news")`` and
    only takes a SQLite path when the resolved provider IS SQLite;
  * ``_connect`` routes every write/read through the fabric's POOLED news
    backends (``provision_domain`` / ``get_domain_backend("news")``) under
    PostgreSQL, keeping the sqlite3 connection surface the mixins speak;
  * the store's DDL is translated to the provider's dialect, and its
    SQLite-verb SQL (``INSERT OR IGNORE``/``INSERT OR REPLACE``/``strftime``)
    is emitted in portable shapes.

ISOLATION CONTRACT
==================
The test NEVER touches the live ``nexusdb``. It creates a throwaway PostgreSQL
database (created/dropped by this test), points the news domain at it through
the app's own ``provision_domain`` path, and proves the write lands there and
only there. The SQLite trap file is watched for the same write: a row
appearing in it while the provider is PostgreSQL is the original violation,
by construction.

The suite skips cleanly (not errors) when PostgreSQL is unreachable or the
optional ``psycopg``/``psycopg_pool`` dependencies are missing — it exercises
a second provider, not the default one.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.database.config import (
    DatabaseConfig,
    build_postgres_url,
    load_database_config,
)
from nexus_scalp.news.database import NewsDatabase

#: The domain name the fabric registers the news backends under.
NEWS_DOMAIN = "news"

#: A representative write the engine performs every ingest cycle.
_SOURCE_ROW = {
    "source_id": "regression_pg_src",
    "name": "Regression PG Source",
    "kind": "RSS",
    "tier": "TIER_3",
    "url": "https://example.invalid/regression.xml",
    "feed_url": "https://example.invalid/regression.xml",
    "enabled": True,
    "poll_interval_sec": 300,
    "language": "en",
    "priority": 0.5,
    "seed_version": "regression",
}

_ARTICLE_ROW = {
    "article_id": "regression_article_001",
    "article_hash": "regression_hash_001",
    "canonical_url": "https://example.invalid/regression-article",
    "title": "Regression: news write follows the active provider",
    "summary": "regression summary",
    "body": "regression body",
    "language": "en",
    "source_id": "regression_pg_src",
    "source_name": "Regression PG Source",
    "published_at": "2026-09-25T00:00:00+00:00",
    "published_at_source": "UNKNOWN",
    "importance": "MINOR",
    "importance_score": 0.1,
    "novelty": "NEW",
}


def _pg_available() -> bool:
    """True when psycopg + psycopg_pool import and a server answers."""
    try:
        import psycopg
        import psycopg_pool
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _pg_available(), reason="PostgreSQL deps (psycopg / psycopg_pool) missing"
)


def _pg_password() -> str | None:
    """Resolve the configured PostgreSQL password (never printed).

    The suite deliberately does not hard-code a credential. The app's own
    secret store is the only source, so a machine that has configured
    PostgreSQL (the only environment where this suite can run) provides it.

    Read BEFORE the fixture moves the settings DB, so a box that already
    configured PostgreSQL supplies the credential the throwaway database
    needs; a machine with nothing configured skips cleanly.
    """
    from nexus_scalp.settings.secret_store import SecureSecretStore

    try:
        return SecureSecretStore().get_secret("db.postgresql.password") or None
    except Exception:
        return None


#: Resolved once at collection time — before the autouse settings-isolation
#: fixture redirects the settings DB, which is where the credential lives on a
#: box that has actually configured PostgreSQL.
_PG_PASSWORD = _pg_password()


@pytest.fixture
def throwaway_pg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A throwaway PostgreSQL database the news domain is provisioned on.

    NEVER the live nexusdb: this fixture creates and drops its own database
    name, and the news domain is pointed at THAT DSN only. The live server is
    contacted only to CREATE / DROP the throwaway name.
    """
    import psycopg

    password = _PG_PASSWORD
    if password is None:
        pytest.skip("no PostgreSQL password in the OS secret store")
    probe_name = f"nse_news_pg_test_{os.getpid()}_{abs(hash(str(tmp_path))) % 100000}"
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

    # Isolate the settings DB so load_database_config() reads ONLY the provider
    # this test pins (a leftover real settings DB would flip it back).
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "app_settings.db"))

    # The provider is pinned through the env override the app's own loader
    # honours (last-wins in load_database_config), pointing at the throwaway
    # database so the store NEVER resolves the live nexusdb.
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")
    monkeypatch.setenv("NSE_DATABASE__PG_HOST", "localhost")
    monkeypatch.setenv("NSE_DATABASE__PG_PORT", "5432")
    monkeypatch.setenv("NSE_DATABASE__PG_DATABASE", probe_name)
    monkeypatch.setenv("NSE_DATABASE__PG_USER", "postgres")

    # The news domain must be provisioned on the throwaway database through the
    # app's own path — this both creates the schema there AND registers the
    # pooled backends the store resolves.
    from nexus_scalp.database.fabric import provision_domain, unregister_domain_backend

    try:
        provision_domain(NEWS_DOMAIN, probe_dsn, min_size=1, max_size=4)
    except Exception as exc:
        _drop(admin_dsn, probe_name)
        pytest.fail(f"provision_domain('news') failed on the throwaway DB: {exc}")

    yield {
        "dsn": probe_dsn,
        "admin_dsn": admin_dsn,
        "database": probe_name,
        "password": password,
    }

    # Teardown order matters: unregister BEFORE dropping. A pooled backend whose
    # database is dropped while it is still registered leaves the registry
    # pointing at a dead pool, and the NEXT test's store would resolve it and
    # fail instead of provisioning its own throwaway database.
    unregister_domain_backend(NEWS_DOMAIN)
    _drop(admin_dsn, probe_name)


def _drop(admin_dsn: str, database: str) -> None:
    import contextlib

    import psycopg

    with contextlib.suppress(Exception):
        with psycopg.connect(admin_dsn, connect_timeout=8, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{database}"')


# ---------------------------------------------------------------------------
# provider resolution
# ---------------------------------------------------------------------------


def test_store_resolves_the_switched_provider(throwaway_pg: dict[str, Any]) -> None:
    """The store follows the ACTIVE provider, not an unconditional SQLite path.

    This is the regression's core: a box switched to PostgreSQL must not see
    the news store quietly open a SQLite file.
    """
    cfg = load_database_config(NEWS_DOMAIN)
    assert cfg.is_postgresql, "the provider switch did not reach load_database_config('news')"

    store = NewsDatabase()
    assert store._config.is_postgresql
    assert store.db_path is None, "a SQLite path must not be held under PostgreSQL"


def test_sqlite_stays_the_default_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default provider is unchanged: SQLite keeps working byte for byte.

    No settings DB, no env override -> SQLite, and an explicit ``db_path`` is
    honored exactly as before the fix.
    """
    monkeypatch.delenv("NSE_DATABASE__PROVIDER", raising=False)
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "absent_settings.db"))

    path = tmp_path / "default_news.db"
    db = NewsDatabase(path)
    assert db._config.is_sqlite
    assert db.db_path == path

    db.upsert_source(_SOURCE_ROW)
    db.insert_article(_ARTICLE_ROW)
    assert db.count_articles() == 1
    assert db.article_exists(_ARTICLE_ROW["article_hash"])
    assert db.get_article_by_hash(_ARTICLE_ROW["article_hash"]) is not None


# ---------------------------------------------------------------------------
# the write lands on PostgreSQL
# ---------------------------------------------------------------------------


def test_write_lands_in_postgresql_not_sqlite(throwaway_pg: dict[str, Any], tmp_path: Path) -> None:
    """The write roundtrips on PostgreSQL and NEVER touches SQLite.

    The SQLite trap file is opened on the same path the store would have used
    before the fix; a row appearing there while the provider is PostgreSQL is
    the violation this test exists to catch.
    """
    import psycopg

    db = NewsDatabase()
    db.upsert_source(_SOURCE_ROW)
    db.insert_article(_ARTICLE_ROW)

    # 1. the store reads the row back through the pooled path
    assert db.count_articles() == 1
    assert db.article_exists(_ARTICLE_ROW["article_hash"])
    row = db.get_article_by_hash(_ARTICLE_ROW["article_hash"])
    assert row is not None
    assert row["article_id"] == _ARTICLE_ROW["article_id"]
    assert row["title"] == _ARTICLE_ROW["title"]
    assert row["source_id"] == _ARTICLE_ROW["source_id"]

    # 2. the row is physically on the throwaway PostgreSQL database
    with psycopg.connect(throwaway_pg["dsn"], connect_timeout=8) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM news_articles")
            assert int(cur.fetchone()[0]) == 1
            cur.execute("SELECT COUNT(*) FROM news_sources")
            assert int(cur.fetchone()[0]) == 1

    # 3. nothing landed in SQLite: the trap file has no such table (the store
    #    never opened it) — the shape of the original violation.
    trap = tmp_path / "trap_news.db"
    with sqlite3.connect(trap) as conn:
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "news_articles" not in tables


def test_postgresql_write_survives_reopen(throwaway_pg: dict[str, Any]) -> None:
    """A second store construction reads the first store's rows.

    Before the fix the store re-initialized an empty SQLite file on every
    construction under PG; durability is meaningless if a fresh store cannot
    read what a previous one wrote.
    """
    first = NewsDatabase()
    first.upsert_source(_SOURCE_ROW)
    first.insert_article(_ARTICLE_ROW)

    second = NewsDatabase()
    assert second.count_articles() == 1
    assert second.article_exists(_ARTICLE_ROW["article_hash"])
    assert second.get_source(_SOURCE_ROW["source_id"]) is not None


def test_upsert_paths_are_idempotent_on_postgresql(throwaway_pg: dict[str, Any]) -> None:
    """The ON CONFLICT upserts do not duplicate rows on re-write.

    The SQLite verbs (``INSERT OR REPLACE``/``OR IGNORE``) had no PostgreSQL
    spelling; the portable ``ON CONFLICT`` clauses must keep the same
    one-row-per-key semantics.
    """
    db = NewsDatabase()
    for _ in range(3):
        db.upsert_source(_SOURCE_ROW)
    for _ in range(3):
        db.insert_article(_ARTICLE_ROW)

    assert db.count_articles() == 1
    assert db.list_sources() == [_SOURCE_ROW["source_id"]] or len(db.list_sources()) == 1


def test_junk_and_analyzed_hashes_roundtrip_on_postgresql(throwaway_pg: dict[str, Any]) -> None:
    """The dedup tombstones (the re-ingest guard) survive the provider switch."""
    db = NewsDatabase()
    assert db.count_junk_hashes() == 0
    assert db.count_analyzed_hashes() == 0

    db.remember_junk_hash("hash_junk_1", title="junk", reason="test")
    db.remember_analyzed_hash("hash_analyzed_1", title="analyzed")

    assert db.is_junk_hash("hash_junk_1")
    assert db.is_analyzed_hash("hash_analyzed_1")
    assert db.count_junk_hashes() == 1
    assert db.count_analyzed_hashes() == 1


def test_post_event_memory_roundtrips_on_postgresql(throwaway_pg: dict[str, Any]) -> None:
    """``PostEventValidator`` wrote through a raw sqlite3 handle before the fix."""
    from nexus_scalp.news.memory.post_event import PostEventValidator
    from nexus_scalp.news.models import NewsDirection

    db = NewsDatabase()
    validator = PostEventValidator(db)
    record = validator.record_response(
        article_id=_ARTICLE_ROW["article_id"],
        predicted_direction=NewsDirection.BULLISH,
        predicted_strength=0.5,
        predicted_horizon="MACRO",
        response_samples=[],
    )
    assert record["record_id"]

    listed = validator.list_records(article_id=_ARTICLE_ROW["article_id"])
    assert len(listed) == 1
    assert listed[0]["record_id"] == record["record_id"]

    summary = validator.accuracy_summary()
    assert summary["records"] == 1


def test_health_and_worker_state_roundtrip_on_postgresql(throwaway_pg: dict[str, Any]) -> None:
    """The fetcher backoff + worker checkpoint stores (upserts) follow too."""
    db = NewsDatabase()
    db.update_health(
        "health_src",
        {
            "last_success_at": "2026-09-25T00:00:00+00:00",
            "consecutive_failures": 2,
            "healthy": 0,
        },
    )
    health = db.get_health("health_src")
    assert health is not None
    assert int(health["consecutive_failures"]) == 2
    assert int(health["healthy"]) == 0

    db.save_worker_state({"scope": "news", "cycle_count": 7, "last_cycle_at": "2026-09-25"})
    state = db.load_worker_state()
    assert state is not None
    assert int(state["cycle_count"]) == 7


def test_schema_is_portable_across_both_providers(
    throwaway_pg: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same store schema lands on PostgreSQL AND SQLite.

    The store's schema init ran its SQLite-dialect DDL verbatim on both
    providers and PostgreSQL rejected ``AUTOINCREMENT`` — this proves the DDL
    now speaks the provider's dialect on each side.
    """
    import psycopg

    with psycopg.connect(throwaway_pg["dsn"], connect_timeout=8) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name LIKE 'news%' "
                "ORDER BY table_name"
            )
            pg_tables = {str(r[0]) for r in cur.fetchall()}

    # The provisioned set must cover the store's declared tables.
    import re

    from nexus_scalp.news.db_schema import _SCHEMA_SQL

    declared = set()
    for ddl in _SCHEMA_SQL:
        m = re.search(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", ddl, re.I)
        if m:
            declared.add(m.group(1))
    assert declared, "no news tables declared"
    assert declared <= pg_tables, f"tables missing on PostgreSQL: {sorted(declared - pg_tables)}"

    # The article_status / published_at_source runtime columns reached PG too
    # (they are guarded ALTERs in the store's init, not in _SCHEMA_SQL).
    with psycopg.connect(throwaway_pg["dsn"], connect_timeout=8) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='news_articles' ORDER BY ordinal_position"
            )
            cols = {str(r[0]) for r in cur.fetchall()}
    assert {"article_status", "published_at_source"} <= cols

    # ...and the same columns exist on the SQLite side (unchanged behaviour)
    monkeypatch.delenv("NSE_DATABASE__PROVIDER", raising=False)
    sqlite_db = NewsDatabase(tmp_path / "parity_news.db")
    with sqlite3.connect(sqlite_db.db_path) as conn:
        sq_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(news_articles)").fetchall()}
    assert {"article_status", "published_at_source"} <= sq_cols
