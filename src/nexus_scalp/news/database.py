"""Dedicated News database (PHASE 12) — facade over cohesive mixins.

A SEPARATE SQLite database (``artifacts/news.db``) so news rows, article
bodies, analysis payloads and AI traces NEVER mix with the core trading
ledger tables. Trading/accounting data survives complete News subsystem
deletion.

Modularization (Agent-5, CHG-0032-A1 program): the public identity of the
news store is unchanged — ``NewsDatabase`` with the same methods, SQL and
constructor surface — while method clusters live in cohesive,
verbatim-extracted siblings:

    db_schema.py    DDL + idempotent schema init (SchemaMixin)
    db_articles.py  sources/articles/versions/dedup hashes (ArticlesMixin)
    db_analysis.py  analysis/impacts/consensus/AI/trade-links (AnalysisMixin)
    db_queries.py   read/ops surface + connection close (QueriesMixin)

Connection ownership stays single-source: ``_connect``/``_now``/``__init__``
live HERE; the mixins are plain stateless method carriers (no __init__, no
extra state). Existing imports keep working unchanged:

    from nexus_scalp.news.database import NewsDatabase

Schema design principles (unchanged):
    * normalised but practical (13 tables, no blind table creation),
    * deterministic article identity (article_hash UNIQUE) for dedup,
    * append-only versioning (news_article_versions),
    * analysis/impact/consensus history (news_analysis, news_impacts,
      news_consensus, news_analysis_runs),
    * trade linkage (news_trade_links),
    * worker state + health (news_worker_state, news_health),
    * rebuildability: derived state is recomputable from raw articles.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.database")

#: The fabric domain name for the news store (``DatabaseDomain.NEWS.value``).
#: The store routes its pooled reads/writes through this domain's backends.
NEWS_DOMAIN = "news"


def _translate_sql_for_pg(sql: str) -> str:
    """Rewrite one SQLite-dialect statement to PostgreSQL.

    The news store's SQL is authored in the SQLite dialect. Every statement is
    portable except two shapes, both rewritten here:

      * ``?`` placeholders -> ``%s`` (the driver's own translation, reused so
        there is one placeholder rule in the codebase);
      * ``INSERT OR IGNORE INTO ...`` -> ``INSERT INTO ... ON CONFLICT DO
        NOTHING``. Every news table declares the UNIQUE/PK its ``OR IGNORE``
        statements rely on (``article_hash`` UNIQUE, ``article_id`` PK,
        ``source_id`` PK...), so the rewrite preserves the conflict semantics
        exactly — the same semantic-preserving port the audit write plane
        performs for its producers.

    ``INSERT OR REPLACE`` is not emitted in the write path this connection
    serves (``upsert_*`` statements carry their own ``ON CONFLICT`` clause);
    a stray one is passed through untouched rather than mis-translated.
    """
    from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver

    out = PostgreSQLDriver.translate_sql(sql)
    stripped = out.lstrip()
    upper = stripped.upper()
    if upper.startswith("INSERT OR IGNORE"):
        rest = stripped[len("INSERT OR IGNORE") :]
        out = "INSERT" + rest
        if "ON CONFLICT" not in out.upper():
            out = out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return out


class _DictRow(dict):
    """A dict that ALSO supports ``row["col"]`` and ``row[0]`` access.

    ``sqlite3.Row`` answers BOTH name and index lookups, and the news mixins
    use both (``row["article_hash"]`` and the positional unpack in
    ``impact_timeline``). psycopg cursors return plain tuples, so this wrapper
    keeps both access styles working on PostgreSQL without touching a single
    call site. The tuple is held alongside the dict (no ``__slots__`` — dict
    subclasses need a real instance ``__dict__`` for the parallel values).
    """

    def __init__(self, names: list[str], values: tuple[Any, ...]) -> None:
        super().__init__(zip(names, values, strict=False))
        self._values = values

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        if isinstance(key, str):
            return super().__getitem__(key)
        return self._values[key]

    def get(self, key: Any, default: Any = None) -> Any:  # type: ignore[override]
        if isinstance(key, str):
            return super().get(key, default)
        try:
            return self._values[key]
        except (IndexError, TypeError):
            return default


class _NewsCursor:
    """psycopg cursor facade answering dicts like ``sqlite3.Row``."""

    __slots__ = ("_cursor", "_names")

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor
        # ``description`` is only populated once a statement has produced a
        # result set, and it changes per statement; the column names are
        # resolved lazily at fetch time so they always describe the LAST
        # executed statement (a cursor is reused across executes by the
        # store's ``with conn:`` blocks).
        self._names: list[str] = []

    def _resolve_names(self) -> list[str]:
        desc = getattr(self._cursor, "description", None) or []
        self._names = [getattr(d, "name", "") for d in desc]
        return self._names

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", -1) or -1)

    @property
    def description(self) -> Any:
        return getattr(self._cursor, "description", None)

    @property
    def lastrowid(self) -> int | None:
        # psycopg v3 reports the sequence value only via RETURNING; the news
        # store never reads an autoincrement id back (its identity columns are
        # app-minted UUIDs/hashes), so there is nothing to expose here.
        return None

    def _row(self, row: Any) -> Any:
        if row is None:
            return None
        if isinstance(row, dict):
            return _DictRow(list(row.keys()), tuple(row.values()))
        return _DictRow(self._resolve_names(), tuple(row))

    def fetchone(self) -> Any:
        return self._row(self._cursor.fetchone())

    def fetchall(self) -> list[Any]:
        return [self._row(r) for r in self._cursor.fetchall()]

    def fetchmany(self, size: int) -> list[Any]:
        return [self._row(r) for r in self._cursor.fetchmany(size)]

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._cursor.close()


class _PooledNewsConnection:
    """sqlite3-shaped connection over the news domain's pooled write backend.

    The mixins speak ``conn.execute(sql, args)``, ``conn.commit()`` and
    ``conn.close()`` with ``with conn:`` transactions and dict rows. This
    wrapper presents exactly that surface while every statement travels the
    fabric's pooled PostgreSQL write plane:

      * ``?`` placeholders and SQLite ``INSERT OR IGNORE``/``INSERT OR REPLACE``
        shapes are rewritten to PostgreSQL before they reach the plane, the
        same translation the audit domain's write path performs;
      * ``with conn:`` commits on a clean exit and rolls back on an exception,
        mirroring the sqlite3 semantics every mixin relies on;
      * a cursor's rows are returned as ``sqlite3.Row``-style dicts (the
        mixins all do ``dict(row)`` / ``row["col"]``), and a
        ``SELECT COUNT(*) AS c`` alias keeps working because the column name
        is carried by psycopg's cursor ``description``.

    The connection is checked out from the pool once, reused for the life of
    the context, and returned on ``close()``.
    """

    __slots__ = ("_backend", "_closed", "_conn", "_ctx", "_cursor")

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._conn: Any = None
        self._ctx: Any = None
        self._cursor: Any = None
        self._closed = False

    # -- checkout -----------------------------------------------------------

    def _checkout(self) -> Any:
        if self._conn is None:
            ctx = self._backend.connection()
            self._conn = ctx.__enter__()
            self._ctx = ctx
        return self._conn

    # -- sqlite3 surface ----------------------------------------------------

    def execute(self, sql: str, args: Sequence[Any] | None = None) -> Any:
        conn = self._checkout()
        if self._cursor is None:
            self._cursor = _NewsCursor(conn.cursor())
        self._cursor._cursor.execute(
            _translate_sql_for_pg(sql), tuple(args) if args is not None else ()
        )
        return self._cursor

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        conn = self._checkout()
        if self._cursor is None:
            self._cursor = _NewsCursor(conn.cursor())
        self._cursor._cursor.executemany(_translate_sql_for_pg(sql), [tuple(r) for r in seq])

    def commit(self) -> None:
        if self._conn is not None:
            self._conn.commit()

    def rollback(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.rollback()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            if self._cursor is not None:
                self._cursor.close()
        ctx = self._ctx
        if ctx is not None:
            with contextlib.suppress(Exception):
                ctx.__exit__(None, None, None)
        elif self._conn is not None:
            with contextlib.suppress(Exception):
                self._backend.pool.putconn(self._conn)

    @property
    def row_factory(self) -> Any:
        # The SQLite branch sets this on a real sqlite3 connection; the pooled
        # connection always yields dict rows (see _NewsCursor), so the no-op
        # keeps a shared ``_connect`` caller path harmless.
        return None

    @row_factory.setter
    def row_factory(self, _value: Any) -> None:
        return

    def __enter__(self) -> _PooledNewsConnection:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()


# --- schema DDL single source lives in db_schema.py; re-exported here as a
# documented compatibility surface (name-stable: _SCHEMA_SQL/_INDEX_SQL) ---
# --- extracted cluster mixins: verbatim method carriers (single source) ---
from nexus_scalp.news.db_analysis import AnalysisMixin  # noqa: E402
from nexus_scalp.news.db_articles import ArticlesMixin  # noqa: E402
from nexus_scalp.news.db_queries import QueriesMixin  # noqa: E402
from nexus_scalp.news.db_schema import (  # noqa: E402
    _INDEX_SQL,  # noqa: F401 — documented compatibility re-export
    _SCHEMA_SQL,  # noqa: F401 — documented compatibility re-export
    SchemaMixin,
)


class _NewsDatabaseCore:
    """Connection + identity core: __init__, _connect, _now (verbatim)."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        config: DatabaseConfig | None = None,
    ) -> None:
        """Provider-aware news store (DATABASE PORTABILITY).

        ``db_path`` remains for backward compatibility (SQLite).  ``config``
        selects the provider explicitly.  With NEITHER given the store follows
        the ACTIVE provider — ``load_database_config("news")`` — instead of
        unconditionally opening a SQLite file: a box switched to PostgreSQL
        via ``nexus db-portability switch`` previously kept writing news into
        ``artifacts/news.db`` while PostgreSQL had the domain's tables and the
        store read ``provider=sqlite`` (a P0 PG-migration violation, same class
        as the AI-provider decision ledger).

        SQLite is the default provider and stays fully working; the SQLite file
        path is honored when the resolved provider is SQLite, and an explicit
        ``db_path`` never forces SQLite when the active provider is PostgreSQL.
        """
        if config is not None:
            self._config = config
        else:
            resolved = load_database_config("news")
            if resolved.is_postgresql:
                # A SQLite path is only meaningful for the SQLite provider; do
                # not let an incidental positional argument downgrade the box's
                # chosen provider (the caller may not know the box is on PG).
                self._config = resolved
            else:
                self._config = DatabaseConfig.for_sqlite(
                    "news", path=str(db_path) if db_path else resolved.sqlite_path
                )
        self.db_path = Path(self._config.sqlite_connect_path) if self._config.is_sqlite else None
        self._driver = get_driver(self._config)
        if self._config.is_sqlite and self.db_path is not None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # SchemaMixin (extracted cluster) provides initialize_schema at runtime.
        self.initialize_schema()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _connect(self, timeout: float = 5.0) -> Any:
        """Portable connection (SQLite native; PostgreSQL fabric-backed).

        Under the default SQLite provider nothing changes: one driver-backed
        ``sqlite3`` connection per call, exactly as before.

        Under PostgreSQL the domain is provisioned once (``_ensure_provisioned``)
        and every call checks a connection out of the fabric's POOLED write
        backend for the news domain — the same pooled path the audit domain
        uses — instead of opening a raw driver connection. The returned object
        keeps the sqlite3 ``execute``/``commit``/``close`` surface the mixins
        speak, so not one of the ~70 call sites changes.
        """
        if self._config.is_sqlite:
            conn = self._driver.connect(timeout=timeout)
            conn.row_factory = sqlite3.Row
            return conn
        backend = self._ensure_provisioned()
        return _PooledNewsConnection(backend)

    def _ensure_provisioned(self) -> Any:
        """Resolve the news domain's pooled WRITE backend, provisioning once.

        Mirrors the audit domain's proven seam: an already-registered backend
        wins (a ``nexus db-portability connect`` / a sibling store already
        provisioned the domain); otherwise the domain is provisioned from the
        resolved DSN, which also creates the schema on PostgreSQL.

        Never raises into a construction path: a provider that cannot be
        reached degrades to the documented SQLite-free fallback rather than
        taking the subsystem down.
        """
        from nexus_scalp.database.fabric import get_domain_backend, provision_domain

        backend = get_domain_backend(NEWS_DOMAIN, readonly=False)
        if backend is not None:
            return backend
        dsn = self._resolved_dsn()
        if not dsn:
            return None
        try:
            return provision_domain(NEWS_DOMAIN, dsn, min_size=1, max_size=4)
        except Exception as exc:
            logger.error("[NEWS_DB] news domain provisioning failed: %s", exc)
            return None

    def _resolved_dsn(self) -> str:
        """The PostgreSQL DSN for this store's config (password injected)."""
        from nexus_scalp.database.config import build_postgres_url
        from nexus_scalp.settings.secret_store import SecureSecretStore

        return build_postgres_url(self._config, SecureSecretStore())

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------


class NewsDatabase(
    _NewsDatabaseCore,
    SchemaMixin,
    ArticlesMixin,
    AnalysisMixin,
    QueriesMixin,
):
    """Dedicated SQLite persistence for the News Intelligence subsystem.

    The trading ``AuditRepository`` is intentionally NOT used: news must be
    independently initialised, backed up, migrated, queried and deleted
    without touching trading history.
    """


__all__ = ["NewsDatabase"]
