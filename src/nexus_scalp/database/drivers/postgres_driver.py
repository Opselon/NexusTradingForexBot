"""PostgreSQL database driver — the scalable/large-dataset/production mode.

Real PostgreSQL support via `psycopg` (v3).  This dependency is OPTIONAL
(``nexus[postgres]``): the application runs fully on SQLite without it, and
only code paths that actually connect to PostgreSQL import it (lazy import
inside the driver).

Driver responsibilities (portability contract):
  * translate ``?`` qmark placeholders to ``%s`` automatically so existing
    repository SQL works against both providers;
  * emulate ``INSERT OR IGNORE`` → ``ON CONFLICT DO NOTHING`` and
    ``INSERT OR REPLACE`` → ``ON CONFLICT (...) DO UPDATE`` via
    :meth:`upsert` / :meth:`insert_ignore`;
  * translate SQLite DDL types (INTEGER identity → BIGSERIAL, REAL → DOUBLE
    PRECISION, BLOB → BYTEA, ...) via :meth:`portable_type_for`;
  * never embed the password: it is injected at connect time from the
    secret store through :func:`nexus_scalp.database.config.build_postgres_url`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Sequence
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, build_postgres_url, mask_url_password
from nexus_scalp.database.drivers.base import DatabaseDriver

#: Case-insensitive map: SQLite/logical type -> PostgreSQL DDL type.
PG_TYPE_MAP: dict[str, str] = {
    "INTEGER": "INTEGER",
    "INT": "INTEGER",
    "BIGINT": "BIGINT",
    "SMALLINT": "SMALLINT",
    "REAL": "DOUBLE PRECISION",
    "FLOAT": "DOUBLE PRECISION",
    "DOUBLE": "DOUBLE PRECISION",
    "TEXT": "TEXT",
    "VARCHAR": "VARCHAR",
    "CHAR": "CHAR",
    "BLOB": "BYTEA",
    "BOOLEAN": "BOOLEAN",
    "NUMERIC": "NUMERIC",
    "DATETIME": "TIMESTAMPTZ",
    "TIMESTAMP": "TIMESTAMPTZ",
    "DATE": "DATE",
    "JSON": "JSONB",
}


def pg_type_for(declared: str) -> str:
    """Translate a logical/SQLite type name to PostgreSQL DDL."""
    name = (declared or "TEXT").strip().upper()
    if "(" in name:
        name, _params = name.split("(", 1)
    mapped = PG_TYPE_MAP.get(name)
    if mapped:
        return mapped
    # Pass through anything already PostgreSQL-shaped.
    if name in {"DOUBLE PRECISION", "TIMESTAMPTZ", "BYTEA", "JSONB", "SERIAL", "BIGSERIAL"}:
        return name
    return "TEXT"


def _translate_placeholders(sql: str) -> str:
    """Rewrite ``?`` qmark placeholders to ``%s`` (psycopg format style).

    Only replaces ``?`` OUTSIDE single-quoted string literals and
    double-quoted identifiers, so string content (URLs, JSON, regex) is
    never corrupted.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            # single-quoted literal: copy until closing quote ('' escape)
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
        elif ch == '"':
            # double-quoted identifier: copy verbatim
            j = sql.find('"', i + 1)
            if j == -1:
                j = n - 1
            out.append(sql[i : j + 1])
            i = j + 1
        elif ch == "?":
            out.append("%s")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class PostgreSQLDriver(DatabaseDriver):
    """Provider driver for PostgreSQL (psycopg v3)."""

    name = "postgresql"
    paramstyle = "format"

    #: psycopg module (lazy — optional dependency).  Loaded on first use.
    _psycopg = None

    def __init__(self, config: DatabaseConfig) -> None:
        super().__init__(config)
        self._default_conn: Any = None

    # -- dependency guard -------------------------------------------------

    @classmethod
    def _psycopg_module(cls) -> Any:
        if cls._psycopg is None:
            try:
                import psycopg  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - env dependent
                raise RuntimeError(
                    "PostgreSQL support is not installed. Run: "
                    "pip install 'nexus[postgres]'  (psycopg[binary]==3.2.*)"
                ) from exc
            cls._psycopg = psycopg
        return cls._psycopg

    @classmethod
    def available(cls) -> bool:
        try:
            import psycopg  # noqa: F401

            return True
        except ImportError:
            return False

    # -- SQL helpers ------------------------------------------------------

    @staticmethod
    def translate_sql(sql: str) -> str:
        """Provider-agnostic SQL → PostgreSQL (placeholders)."""
        return _translate_placeholders(sql)

    # -- connections ------------------------------------------------------

    def connect(self, timeout: float = 10.0) -> Any:
        psycopg = self._psycopg_module()
        cfg = self.config
        url = build_postgres_url(cfg)
        kwargs: dict[str, Any] = {"connect_timeout": int(timeout or cfg.connect_timeout_sec or 10)}
        if cfg.ssl_mode:
            kwargs["sslmode"] = cfg.ssl_mode
        if cfg.command_timeout_sec:
            kwargs["options"] = f"-c statement_timeout={cfg.command_timeout_sec * 1000}"
        # Log only the sanitized form (never the password).
        import structlog

        structlog.get_logger("nexus_scalp.database.drivers.postgres").debug(
            "postgres connect", url=mask_url_password(url)
        )
        return psycopg.connect(url, **kwargs)

    def closed(self) -> bool:  # pragma: no cover - thin passthrough
        return False

    # -- setup ------------------------------------------------------------

    def ensure_directory(self) -> None:
        return None  # server-side database; nothing local to create

    def configure_connection(self, conn: Any) -> None:
        if self.config.command_timeout_sec:
            with contextlib.suppress(Exception):
                conn.execute(
                    f"SET statement_timeout = {int(self.config.command_timeout_sec) * 1000}"
                )

    # -- DDL --------------------------------------------------------------

    def _info_columns(self, conn: Any = None) -> list[str]:
        """Column names of a table from information_schema (lowercase)."""
        # placeholder — replaced by table_columns below; kept for clarity
        return []

    def table_columns(self, table: str, conn: Any = None) -> list[dict[str, Any]]:
        """Column layout via information_schema + primary key info."""
        own = conn is None
        c = conn or self.connect()
        try:
            rows = c.execute(
                "SELECT column_name, data_type, is_nullable, column_default, "
                "  character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            ).fetchall()
            # primary key columns
            pk_rows = c.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = %s AND n.nspname = 'public') AND i.indisprimary",
                (table,),
            ).fetchall()
            pks = {r[0] for r in pk_rows}
            out: list[dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "name": r[0],
                        "type": r[1],
                        "notnull": r[2] == "NO",
                        "pk": r[0] in pks,
                        "dflt_value": r[3],
                    }
                )
            return out
        finally:
            if own:
                c.close()

    def table_exists(self, table: str, conn: Any = None) -> bool:
        own = conn is None
        c = conn or self.connect()
        try:
            row = c.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
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
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            if own:
                c.close()

    def create_table(self, table: str, ddl: str) -> None:
        """Execute a CREATE TABLE (DDL already ported by the migrator)."""
        conn = self.connect()
        try:
            conn.execute(ddl)
            conn.commit()
        finally:
            conn.close()

    # -- DML --------------------------------------------------------------

    def _maybe_commit_auto(self, conn: Any) -> None:
        """Auto-commit when the driver opened the connection itself."""
        with contextlib.suppress(Exception):
            if conn is not None:
                conn.commit()

    def execute(self, sql: str, args: Sequence[Any] = (), conn: Any = None) -> Any:
        active_tx = getattr(self, "_active_tx_conn", None)
        if conn is None and active_tx is not None:
            conn = active_tx  # join the open transaction (no autocommit)
        own = conn is None
        c = conn or self.connect()
        try:
            cur = c.execute(self.translate_sql(sql), tuple(args) if args else None)
            if own:
                c.commit()
            return cur
        finally:
            if own:
                c.close()

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]], conn: Any = None) -> None:
        active_tx = getattr(self, "_active_tx_conn", None)
        if conn is None and active_tx is not None:
            conn = active_tx
        own = conn is None
        c = conn or self.connect()
        try:
            with c.cursor() as cur:
                cur.executemany(self.translate_sql(sql), seq)
            if own:
                c.commit()
        finally:
            if own:
                c.close()

    def query(self, sql: str, args: Sequence[Any] = (), conn: Any = None) -> list[dict[str, Any]]:
        own = conn is None
        c = conn or self.connect()
        try:
            cur = c.execute(self.translate_sql(sql), tuple(args) if args else None)
            rows = cur.fetchall()
            names = [d.name for d in cur.description] if cur.description else []
            return [dict(zip(names, r, strict=False)) for r in rows]
        finally:
            if own:
                c.close()

    def query_one(
        self, sql: str, args: Sequence[Any] = (), conn: Any = None
    ) -> dict[str, Any] | None:
        own = conn is None
        c = conn or self.connect()
        try:
            cur = c.execute(self.translate_sql(sql), tuple(args) if args else None)
            row = cur.fetchone()
            if row is None:
                return None
            names = [d.name for d in cur.description] if cur.description else []
            return dict(zip(names, row, strict=False))
        finally:
            if own:
                c.close()

    def scalar(self, sql: str, args: Sequence[Any] = (), conn: Any = None) -> Any:
        own = conn is None
        c = conn or self.connect()
        try:
            row = c.execute(self.translate_sql(sql), tuple(args) if args else None).fetchone()
            return row[0] if row is not None else None
        finally:
            if own:
                c.close()

    def last_insert_rowid(self, conn: Any = None) -> int:
        """Session-scoped last sequence value (lastval()).

        NOTE: prefer INSERT ... RETURNING <id> when the callsite controls the
        statement (psycopg supports RETURNING natively).
        """
        try:
            return int(self.scalar("SELECT lastval()", conn=conn) or -1)
        except Exception:
            return -1

    def _conflict_target(
        self, table: str, conn: Any, cols: list[str]
    ) -> tuple[list[str], bool] | None:
        """Best conflict target for an upsert row.

        Returns (target_columns, covers_row): PK columns when they are all
        present in the row, else the unique columns present in the row, else
        None.  Cached per table (PK layout + unique columns).
        """
        cached = getattr(self, "_conflict_cache", None)
        if cached is None:
            cached = {}
            self._conflict_cache = cached
        if table not in cached:
            pks: list[str] = []
            uniques: list[str] = []
            with contextlib.suppress(Exception):
                for col in self.table_columns(table, conn=conn):
                    if col.get("pk"):
                        pks.append(str(col["name"]))
            with contextlib.suppress(Exception):
                rows = conn.execute(
                    "SELECT kcu.column_name FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON tc.constraint_name = kcu.constraint_name "
                    "WHERE tc.table_name = %s AND tc.constraint_type = 'UNIQUE' "
                    "ORDER BY kcu.ordinal_position",
                    (table,),
                ).fetchall()
                uniques = [r[0] for r in rows]
            cached[table] = (pks, uniques)
        pks, uniques = cached[table]
        if pks and all(p in cols for p in pks):
            return (pks, True)
        present_unique = [u for u in uniques if u in cols]
        if present_unique:
            return (present_unique, True)
        if pks:
            return (pks, False)
        return None

    def upsert(self, table: str, row: dict[str, Any], conn: Any = None) -> None:
        """ON CONFLICT (pk|unique, ...) DO UPDATE — portable REPLACE.

        The conflict target is resolved from the table's primary key (or its
        unique columns when no PK covers the inserted row), so SQLite
        INSERT OR REPLACE semantics carry over to PostgreSQL.
        """
        own = conn is None
        c = conn or self.connect()
        try:
            cols = list(row.keys())
            if not cols:
                return
            placeholders = ",".join("%s" for _ in cols)
            col_list = ",".join(cols)
            table_sql = self.quote_ident(table)
            col_list = ",".join(self.quote_ident(col) for col in cols)
            sql = f"INSERT INTO {table_sql} ({col_list}) VALUES ({placeholders})"
            hit = self._conflict_target(table, c, cols)
            if hit:
                target, _in_row = hit
                updates = ",".join(f"{cn} = EXCLUDED.{cn}" for cn in cols if cn not in target)
                if updates:
                    target_sql = ",".join(self.quote_ident(col) for col in target)
                    sql += f" ON CONFLICT ({target_sql}) DO UPDATE SET {updates}"
                else:
                    sql += " ON CONFLICT DO NOTHING"
            else:
                sql += " ON CONFLICT DO NOTHING"
            c.execute(sql, list(row.values()))
            if own:
                c.commit()
        finally:
            if own:
                c.close()

    def insert_ignore(self, table: str, row: dict[str, Any], conn: Any = None) -> None:
        own = conn is None
        c = conn or self.connect()
        try:
            cols = list(row.keys())
            placeholders = ",".join("%s" for _ in cols)
            table_sql = self.quote_ident(table)
            columns_sql = ",".join(self.quote_ident(col) for col in cols)
            sql = (
                f"INSERT INTO {table_sql} ({columns_sql}) VALUES ({placeholders}) "
                "ON CONFLICT DO NOTHING"
            )
            c.execute(sql, list(row.values()))
            if own:
                c.commit()
        finally:
            if own:
                c.close()

    # -- transactions -----------------------------------------------------

    def begin(self, conn: Any = None) -> None:
        c = conn or self.connect()
        if c is not None:
            self._active_tx_conn = c
        c.execute("BEGIN")

    def commit(self, conn: Any = None) -> None:
        c = conn or getattr(self, "_last_auto_conn", None)
        if c is not None:
            c.commit()
        self._active_tx_conn = None

    # -- metadata / health ------------------------------------------------

    def database_version(self, conn: Any = None) -> str:
        return str(self.scalar("SELECT version()", conn=conn) or "unknown")

    def database_size_bytes(self) -> int | None:
        try:
            return int(self.scalar("SELECT pg_database_size(current_database())") or 0)
        except Exception:
            return None

    def table_count(self, conn: Any = None) -> int:
        return int(
            self.scalar(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'",
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
        # PostgreSQL has no single-file integrity check; the migrator uses
        # row-count + checksum validation instead.
        return []

    def close(self) -> None:
        if self._default_conn is not None:
            try:
                self._default_conn.close()
            finally:
                self._default_conn = None

    # -- dialect helpers for the migrator ----------------------------------

    def portable_type_for(self, sqlite_type: str) -> str:
        """Translate SQLite/logical types into PostgreSQL DDL types."""
        return pg_type_for(sqlite_type)

    def identity_ddl(self) -> str:
        """BIGSERIAL: avoids the 2^31 ceiling of plain SERIAL/INTEGER."""
        return "BIGSERIAL PRIMARY KEY"
