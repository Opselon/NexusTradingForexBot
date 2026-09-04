"""SQLite database driver — the default/local/small-dataset mode.

Wraps the stdlib :mod:`sqlite3` module behind the common driver contract
(see :mod:`nexus_scalp.database.drivers.base`) so persistence consumers can
switch providers without changing business logic.

SQLite-specific behaviors are isolated HERE, by contract:

  * WAL journaling + performance PRAGMAs (setup only);
  * ``AUTOINCREMENT`` / rowid-based integer identity;
  * ``INSERT OR IGNORE`` / ``INSERT OR REPLACE`` are emulated via the
    portable :meth:`DatabaseDriver.upsert` (SQLite keeps its native form
    internally);
  * ``datetime('now')`` defaults are NOT used: the app writes explicit UTC
    timestamps, so schema stays provider-portable;
  * shared in-memory URI support (``file::memory:?cache=shared``) — the
    repository layer keeps ONE persistent connection for it.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers._sql_guard import assert_safe_sql
from nexus_scalp.database.drivers.base import DatabaseDriver

#: Case-insensitive map: SQLite type name -> portable logical type.
SQLITE_TYPE_MAP: dict[str, str] = {
    "INTEGER": "INTEGER",
    "INT": "INTEGER",
    "BIGINT": "BIGINT",
    "REAL": "DOUBLE",
    "FLOAT": "DOUBLE",
    "DOUBLE": "DOUBLE",
    "TEXT": "TEXT",
    "VARCHAR": "TEXT",
    "CHAR": "TEXT",
    "BLOB": "BLOB",
    "BOOLEAN": "BOOLEAN",
    "NUMERIC": "NUMERIC",
    "DATETIME": "TIMESTAMP",
    "TIMESTAMP": "TIMESTAMP",
    "DATE": "DATE",
    "JSON": "TEXT",
}


def sqlite_type_to_portable(declared: str) -> str:
    """Normalize a SQLite column type to the portable logical type."""
    name = (declared or "TEXT").strip().upper()
    if "(" in name:
        name = name.split("(", 1)[0]
    return SQLITE_TYPE_MAP.get(name, name or "TEXT")


class SQLiteDriver(DatabaseDriver):
    """Provider driver for SQLite (stdlib sqlite3)."""

    name = "sqlite"
    #: SQLite accepts any placeholder style; qmark is the canonical form.
    paramstyle = "qmark"

    def __init__(self, config: DatabaseConfig) -> None:
        super().__init__(config)
        self._shared_conn: sqlite3.Connection | None = None

    # -- identity ---------------------------------------------------------

    @property
    def connect_path(self) -> str:
        """Path/URI passed to sqlite3.connect (file::memory:?cache=shared)."""
        return self.config.sqlite_connect_path

    @property
    def is_in_memory(self) -> bool:
        return self.connect_path.startswith("file::") or self.connect_path == ":memory:"

    # -- connections ------------------------------------------------------

    def connect(self, timeout: float = 10.0) -> sqlite3.Connection:
        path = self.connect_path
        conn = sqlite3.connect(path, timeout=timeout, uri=path.startswith("file::"))
        conn.row_factory = sqlite3.Row
        return conn

    def connect_shared(self, timeout: float = 10.0) -> sqlite3.Connection:
        """Persistent connection for shared in-memory databases.  The shared
        cache is dropped when the last connection closes, so callers MUST
        reuse this connection (worker threads) and close via
        :meth:`close_shared`."""
        if self._shared_conn is None:
            self._shared_conn = self.connect(timeout=timeout)
        return self._shared_conn

    def close_shared(self) -> None:
        if self._shared_conn is not None:
            try:
                self._shared_conn.close()
            finally:
                self._shared_conn = None

    # -- setup ------------------------------------------------------------

    def ensure_directory(self) -> None:
        """Create the parent directory of the database file (file-backed)."""
        if self.is_in_memory:
            return
        path = self.connect_path
        if path.startswith("file::"):
            return
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    def configure_connection(self, conn: sqlite3.Connection) -> None:
        """Apply the SQLite performance PRAGMAs (WAL, synchronous, temp)."""
        if self.is_in_memory:
            conn.execute("PRAGMA journal_mode = MEMORY;")
        else:
            conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")

    # -- DDL --------------------------------------------------------------

    def create_table(self, table: str, ddl: str) -> None:
        """Execute a CREATE TABLE statement (idempotent at call-site)."""
        conn = self.connect_shared() if self.is_in_memory else self.connect()
        try:
            conn.execute(ddl)
            conn.commit()
        finally:
            if not self.is_in_memory:
                conn.close()

    def table_columns(self, table: str, conn: Any = None) -> list[dict[str, Any]]:
        """Introspect column layout via pragma_table_info (parameterized).

        Uses ``SELECT * FROM pragma_table_info(?)`` with a bound parameter
        instead of string-interpolating ``PRAGMA table_info(...)`` so the
        table name never enters the SQL text (CodeQL py/sql-injection).
        ``quote_ident`` is kept as a strict allow-list validator so callers
        still get ``ValueError`` on malformed identifiers.
        """
        self.quote_ident(table)  # validate shape → ValueError on bad ident
        own = conn is None
        c = conn or self.connect()
        try:
            rows = c.execute("SELECT * FROM pragma_table_info(?)", (table,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            if own:
                c.close()

    def table_exists(self, table: str, conn: Any = None) -> bool:
        own = conn is None
        c = conn or self.connect()
        try:
            row = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            return row is not None
        finally:
            if own:
                c.close()

    def list_tables(self, conn: Any = None) -> list[str]:
        own = conn is None
        c = conn or self.connect()
        try:
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            if own:
                c.close()

    # -- DML --------------------------------------------------------------

    def execute(self, sql: str, args: Sequence[Any] = (), conn: Any = None) -> Any:
        """Execute a statement with qmark placeholders; returns the cursor.

        Converts ``INSERT OR IGNORE`` → ``INSERT OR IGNORE`` (native) and
        ``ON CONFLICT`` → ``INSERT OR IGNORE`` is NOT needed here — callers
        use :meth:`upsert` for portable upserts; raw execute passes through.
        """
        own = conn is None
        c = conn or (self.connect_shared() if self.is_in_memory else self.connect())
        try:
            return c.execute(sql, tuple(args))
        finally:
            if own and not self.is_in_memory:
                c.close()

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]], conn: Any = None) -> None:
        own = conn is None
        c = conn or (self.connect_shared() if self.is_in_memory else self.connect())
        try:
            c.executemany(sql, seq)
        finally:
            if own and not self.is_in_memory:
                c.close()

    def query(self, sql: str, args: Sequence[Any] = (), conn: Any = None) -> list[dict[str, Any]]:
        """Run a SELECT and return rows as dicts (row_factory applied)."""
        own = conn is None
        c = conn or self.connect()
        try:
            cur = c.execute(assert_safe_sql(sql), tuple(args))
            return [dict(r) for r in cur.fetchall()]
        finally:
            if own:
                c.close()

    def query_one(
        self, sql: str, args: Sequence[Any] = (), conn: Any = None
    ) -> dict[str, Any] | None:
        own = conn is None
        c = conn or self.connect()
        try:
            cur = c.execute(sql, tuple(args))
            row = cur.fetchone()
            return dict(row) if row is not None else None
        finally:
            if own:
                c.close()

    def scalar(self, sql: str, args: Sequence[Any] = (), conn: Any = None) -> Any:
        own = conn is None
        c = conn or self.connect()
        try:
            cur = c.execute(sql, tuple(args))
            row = cur.fetchone()
            return row[0] if row is not None else None
        finally:
            if own:
                c.close()

    def last_insert_rowid(self, conn: Any = None) -> int:
        c = conn or (self.connect_shared() if self.is_in_memory else self.connect())
        own = c is not conn
        try:
            return int(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        finally:
            if own and not self.is_in_memory:
                c.close()

    def upsert(self, table: str, row: dict[str, Any], conn: Any = None) -> None:
        """Portable upsert: SQLite uses INSERT OR REPLACE (native)."""
        cols = list(row.keys())
        qmarks = ",".join("?" for _ in cols)
        table_sql = self.quote_ident(table)
        columns_sql = ",".join(self.quote_ident(col) for col in cols)
        sql = f"INSERT OR REPLACE INTO {table_sql} ({columns_sql}) VALUES ({qmarks})"
        self.execute(sql, [row[c] for c in cols], conn=conn)

    def insert_ignore(self, table: str, row: dict[str, Any], conn: Any = None) -> None:
        cols = list(row.keys())
        qmarks = ",".join("?" for _ in cols)
        table_sql = self.quote_ident(table)
        columns_sql = ",".join(self.quote_ident(col) for col in cols)
        sql = f"INSERT OR IGNORE INTO {table_sql} ({columns_sql}) VALUES ({qmarks})"
        self.execute(sql, [row[c] for c in cols], conn=conn)

    # -- transactions / locking ------------------------------------------

    def begin(self, conn: Any = None) -> None:
        c = conn or self.connect()
        c.execute("BEGIN")
        if conn is None and self.is_in_memory:
            self._last_auto_conn = c  # keep alive for shared memory

    def commit(self, conn: Any = None) -> None:
        c = conn or getattr(self, "_last_auto_conn", None)
        if c is not None:
            c.commit()

    # -- metadata ---------------------------------------------------------

    def database_version(self, conn: Any = None) -> str:
        return self.scalar("SELECT sqlite_version()", conn=conn) or "unknown"

    def database_size_bytes(self) -> int | None:
        if self.is_in_memory:
            return None
        try:
            return os.path.getsize(self.connect_path)
        except OSError:
            return None

    def table_count(self, conn: Any = None) -> int:
        return int(
            self.scalar(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'",
                conn=conn,
            )
            or 0
        )

    def row_count(self, table: str, conn: Any = None) -> int:
        return int(self.scalar(f"SELECT COUNT(*) FROM {self.quote_ident(table)}", conn=conn) or 0)

    def ping(self, conn: Any = None) -> bool:
        try:
            return self.scalar("SELECT 1", conn=conn) == 1
        except Exception:
            return False

    def integrity_check(self, conn: Any = None) -> list[str]:
        """PRAGMA integrity_check; returns non-ok rows (empty = healthy)."""
        own = conn is None
        c = conn or self.connect()
        try:
            rows = c.execute("PRAGMA integrity_check").fetchall()
            return [r[0] for r in rows if r[0] != "ok"]
        finally:
            if own:
                c.close()

    def close(self) -> None:
        self.close_shared()
