"""Database driver contract — the persistence abstraction boundary.

Every relational provider is reached through this interface.  Business and
domain logic never branches on provider: they receive a
:class:`DatabaseDriver` instance and call its portable methods.

Portability rules enforced by this boundary:
  * placeholders are provider-native (qmark for SQLite, %s for PostgreSQL) —
    callers pass :meth:`DatabaseDriver.qmarks` when building statements;
  * upserts go through :meth:`DatabaseDriver.upsert` (INSERT OR REPLACE vs
    ON CONFLICT ... DO UPDATE);
  * identity is retrieved via :meth:`DatabaseDriver.last_insert_rowid` (or
    RETURNING on PostgreSQL where supported);
  * schema DDL stays provider-portable (INTEGER identity -> BIGSERIAL is
    handled by the PostgreSQL driver's DDL translation used by the migrator).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any

from nexus_scalp.database.config import DatabaseConfig


class DatabaseDriver(ABC):
    """Abstract persistence driver (SQLite / PostgreSQL)."""

    name: str = "abstract"
    #: DB-API paramstyle this driver speaks (qmark | format | pyformat).
    paramstyle: str = "qmark"

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        self._last_auto_conn: Any = None

    # -- helpers ----------------------------------------------------------

    def qmarks(self, count: int) -> str:
        """Provider-native placeholder sequence for `count` params."""
        if self.paramstyle == "qmark":
            return ",".join("?" for _ in range(count))
        if self.paramstyle == "format":
            return ",".join("%s" for _ in range(count))
        return ",".join(f"%s{i}" for i in range(count))  # pyformat

    def quote_ident(self, ident: str) -> str:
        """Validate and quote a simple SQL identifier."""
        if not isinstance(ident, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ident) is None:
            raise ValueError("invalid SQL identifier")
        return f'"{ident}"'

    def transaction(self, conn: Any = None):
        """Context manager for an atomic unit of work (savepoint-friendly).

        SQLite: `with conn:` semantics (commit on success / rollback on
        exception).  PostgreSQL: BEGIN/COMMIT with rollback on exception.
        """
        return _DriverTransaction(self, conn)

    # -- connections (provider specific; see driver implementations) ------

    @abstractmethod
    def connect(self, timeout: float = 10.0) -> Any:
        """Open a new connection."""

    # -- setup ------------------------------------------------------------

    @abstractmethod
    def ensure_directory(self) -> None:
        """Create any filesystem prerequisite (SQLite parent dir)."""
        raise NotImplementedError

    @abstractmethod
    def configure_connection(self, conn: Any) -> None:
        """Per-connection provider tuning (SQLite PRAGMAs / PG session)."""
        raise NotImplementedError

    # -- DDL --------------------------------------------------------------

    @abstractmethod
    def create_table(self, table: str, ddl: str) -> None:
        """Execute a CREATE TABLE statement."""
        raise NotImplementedError

    @abstractmethod
    def table_columns(self, table: str, conn: Any = None) -> list[dict[str, Any]]:
        """Column layout: [{name, type, notnull, pk, dflt_value}, ...]."""

    @abstractmethod
    def table_exists(self, table: str, conn: Any = None) -> bool:
        """True when the table exists."""

    @abstractmethod
    def list_tables(self, conn: Any = None) -> list[str]:
        """User table names (no sqlite_* / system catalogs)."""

    # -- DML --------------------------------------------------------------

    @abstractmethod
    def execute(self, sql: str, args: Sequence[Any] = (), conn: Any = None) -> Any:
        """Execute a statement; returns the cursor."""

    @abstractmethod
    def executemany(self, sql: str, seq: Iterable[Sequence[Any]], conn: Any = None) -> None:
        """Execute a statement for many parameter sets."""

    @abstractmethod
    def query(self, sql: str, args: Sequence[Any] = (), conn: Any = None) -> list[dict[str, Any]]:
        """SELECT rows as dicts."""

    @abstractmethod
    def query_one(
        self, sql: str, args: Sequence[Any] = (), conn: Any = None
    ) -> dict[str, Any] | None:
        """First row or None."""

    @abstractmethod
    def scalar(self, sql: str, args: Sequence[Any] = (), conn: Any = None) -> Any:
        """First column of the first row."""

    @abstractmethod
    def last_insert_rowid(self, conn: Any = None) -> int:
        """Identity of the last inserted row."""

    @abstractmethod
    def upsert(self, table: str, row: dict[str, Any], conn: Any = None) -> None:
        """Portable upsert (REPLACE vs ON CONFLICT DO UPDATE)."""

    @abstractmethod
    def insert_ignore(self, table: str, row: dict[str, Any], conn: Any = None) -> None:
        """Portable insert-or-ignore (OR IGNORE vs ON CONFLICT DO NOTHING)."""

    # -- transactions -----------------------------------------------------

    @abstractmethod
    def begin(self, conn: Any = None) -> None:
        """Start a transaction."""

    @abstractmethod
    def commit(self, conn: Any = None) -> None:
        """Commit the transaction."""

    # -- metadata / health ------------------------------------------------

    @abstractmethod
    def database_version(self, conn: Any = None) -> str:
        """Server/engine version string."""

    @abstractmethod
    def database_size_bytes(self) -> int | None:
        """Physical size when meaningful (None when not applicable)."""
        raise NotImplementedError

    @abstractmethod
    def table_count(self, conn: Any = None) -> int:
        """Number of user tables."""

    @abstractmethod
    def row_count(self, table: str, conn: Any = None) -> int:
        """Row count of a table."""

    @abstractmethod
    def ping(self, conn: Any = None) -> bool:
        """SELECT 1 connectivity check."""

    def integrity_check(self, conn: Any = None) -> list[str]:
        """Schema/data integrity problems ([] = healthy)."""
        return []

    @abstractmethod
    def close(self) -> None:
        """Release driver resources (shared connections)."""

    # -- dialect helpers used by the migrator ------------------------------

    def portable_type_for(self, sqlite_type: str) -> str:
        """Translate a logical type name to this provider's DDL type."""
        return sqlite_type

    def identity_ddl(self) -> str:
        """DDL fragment for an auto-incrementing integer primary key."""
        return "INTEGER PRIMARY KEY AUTOINCREMENT"


class _DriverTransaction:
    """Context-managed transaction over a driver + optional connection."""

    def __init__(self, driver: DatabaseDriver, conn: Any = None) -> None:
        self._driver = driver
        self._conn = conn
        self._owns = conn is None

    def __enter__(self) -> Any:
        if self._owns:
            self._conn = self._driver.connect()
        self._driver.begin(self._conn)
        return self._conn

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if exc_type is None:
                self._driver.commit(self._conn)
            else:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
        finally:
            if self._owns:
                try:
                    self._conn.close()
                except Exception:
                    pass
