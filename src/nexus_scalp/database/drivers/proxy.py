"""Portable connection proxy — lets existing repository code (written against
sqlite3 connections) run unchanged against PostgreSQL.

Why this exists:
    NewsDatabase and CandleIntelligenceStore open ``sqlite3.Connection``
    objects and call ``conn.execute(...)`` directly (dozens of call sites).
    Rather than rewriting every call site into driver.query()/scalar(), the
    proxy presents the SAME connection surface on both providers:

      * SQLite:  the real sqlite3 connection (row_factory = sqlite3.Row) —
                 zero behavioral change;
      * PostgreSQL: a thin wrapper over a psycopg connection whose
                 ``execute()`` translates qmark ``?`` placeholders to ``%s``,
                 rewrites ``INSERT OR IGNORE`` -> ``ON CONFLICT DO NOTHING``
                 and ``INSERT OR REPLACE`` -> ``ON CONFLICT (<pk>) DO UPDATE``
                 (using the cached primary-key layout), and returns dict-style
                 rows from fetchone/fetchall exactly like sqlite3.Row.

Transaction semantics mirror sqlite3: ``with conn:`` commits on success and
rolls back on exception WITHOUT closing the connection.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from nexus_scalp.database.drivers.base import DatabaseDriver
from nexus_scalp.database.drivers.postgres_driver import (
    _translate_placeholders,
)

#: INSERT OR IGNORE -> ON CONFLICT DO NOTHING (works without a conflict target).
_PG_NO_TARGET = "ON CONFLICT DO NOTHING"


class PortableCursor:
    """Cursor facade: dict rows + rowcount, works for psycopg cursors."""

    def __init__(self, cursor: Any, description: Any = None) -> None:
        self._cursor = cursor
        self._names: list[str] = []
        desc = description if description is not None else getattr(cursor, "description", None)
        if desc is not None:
            self._names = [d.name for d in desc]

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", -1) or -1)

    @property
    def description(self) -> Any:
        return getattr(self._cursor, "description", None)

    def _to_dict(self, row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return row
        if self._names:
            return dict(zip(self._names, row, strict=False))
        return dict(row)

    def fetchone(self) -> dict[str, Any] | None:
        row = self._cursor.fetchone()
        return self._to_dict(row) if row is not None else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [self._to_dict(r) for r in self._cursor.fetchall()]

    def fetchmany(self, size: int) -> list[dict[str, Any]]:
        return [self._to_dict(r) for r in self._cursor.fetchmany(size)]

    def close(self) -> None:
        try:
            self._cursor.close()
        except Exception:
            pass


def _rewrite_insert_or(sql: str, pk_cache: dict[str, list[str]]) -> tuple[str, str]:
    """Rewrite SQLite INSERT OR IGNORE/REPLACE into PostgreSQL-safe SQL.

    Returns (sql, kind) where kind ∈ {"none", "ignore", "replace"} tells the
    caller whether a conflict clause must be appended.
    """
    upper = sql.upper().lstrip()
    if upper.startswith("INSERT OR IGNORE"):
        rest = sql[sql.upper().find("INSERT OR IGNORE") + len("INSERT OR IGNORE") :]
        return ("INSERT " + rest, "ignore")
    if upper.startswith("INSERT OR REPLACE"):
        rest = sql[sql.upper().find("INSERT OR REPLACE") + len("INSERT OR REPLACE") :]
        return ("INSERT " + rest, "replace")
    return (sql, "none")


class PortableConnection:
    """sqlite3-compatible connection surface over any DatabaseDriver."""

    def __init__(self, driver: DatabaseDriver, timeout: float = 5.0) -> None:
        self._driver = driver
        if driver.name == "sqlite":
            self._conn = driver.connect(timeout=timeout)
            self._pg = None
        else:
            self._conn = driver.connect(timeout=timeout)
            self._pg = self._conn
        self._pk_cache: dict[str, list[str]] = {}
        self._closed = False

    # -- sqlite3 surface -------------------------------------------------

    def execute(self, sql: str, args: Sequence[Any] | None = None) -> Any:
        if self._pg is None:
            return self._conn.execute(sql, tuple(args) if args is not None else ())
        psql = _translate_placeholders(sql)
        psql, kind = _rewrite_insert_or(psql, self._pk_cache)
        params = tuple(args) if args is not None else ()
        if kind == "ignore":
            cur = self._pg.execute(
                self._strip_semicolon(psql) + f" {_PG_NO_TARGET}", params or None
            )
        elif kind == "replace":
            cur = self._pg.execute(psql, params or None)
            # If the statement has no ON CONFLICT yet, add it.
            if "ON CONFLICT" not in psql.upper():
                cur = self._pg.execute(self._with_replace_conflict(psql, cur, params), None)
        else:
            cur = self._pg.execute(psql, params or None)
        return PortableCursor(cur)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        if self._pg is None:
            self._conn.executemany(sql, seq)
            return
        psql = _translate_placeholders(sql)
        psql, kind = _rewrite_insert_or(psql, self._pk_cache)
        if kind == "ignore":
            psql = self._strip_semicolon(psql) + f" {_PG_NO_TARGET}"
        with self._pg.cursor() as cur:
            cur.executemany(psql, seq)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self) -> None:
        if not self._closed:
            try:
                self._conn.close()
            finally:
                self._closed = True

    def __enter__(self) -> PortableConnection:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _strip_semicolon(sql: str) -> str:
        return sql.rstrip().rstrip(";")

    def _with_replace_conflict(self, sql: str, cur: Any, params: Any) -> str:
        """Append ON CONFLICT (<pk>) DO UPDATE for INSERT OR REPLACE.

        Column layout is introspected per table (cached) so the emulation
        matches SQLite REPLACE semantics for full-row statements.
        """
        import re

        m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", sql, re.I)
        if not m:
            return sql + " ON CONFLICT DO NOTHING"
        table = m.group(1)
        pks = self._pk_cache.get(table)
        if pks is None:
            cols = self._driver.table_columns(table)
            pks = [c["name"] for c in cols if c.get("pk")]
            self._pk_cache[table] = pks
        if not pks:
            return sql + " ON CONFLICT DO NOTHING"
        # column list of the INSERT
        im = re.search(r"INSERT\s+INTO\s+\w+\s*\(([^)]*)\)", sql, re.I)
        inserted = [c.strip().strip('"') for c in im.group(1).split(",")] if im else []
        updates = ",".join(f"{c} = EXCLUDED.{c}" for c in inserted if c not in pks)
        if not updates:
            return sql + f" ON CONFLICT ({','.join(pks)}) DO NOTHING"
        return sql + f" ON CONFLICT ({','.join(pks)}) DO UPDATE SET {updates}"


class SqliteLikeProxy:
    """Factory: build a PortableConnection for a driver/config."""


def connect_proxy(driver: DatabaseDriver) -> PortableConnection:
    """Shortcut used by stores: portable connection for the active driver."""
    return PortableConnection(driver)
