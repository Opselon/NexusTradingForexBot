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

import contextlib
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any

from nexus_scalp.database.config import DatabaseConfig


def _split_top_level(body: str) -> list[str]:
    """Split ``body`` on top-level commas (outside parens and quotes).

    Shared by the upsert-statement parsers that must read a column list without
    being fooled by a comma inside a value literal.
    """
    parts: list[str] = []
    depth = 0
    last = 0
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch in "'\"":
            j = i + 1
            while j < n:
                if body[j] == ch:
                    if j + 1 < n and body[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth > 0:
                depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[last:i])
            last = i + 1
        i += 1
    parts.append(body[last:])
    return parts


#: ``INSERT INTO <t> (<cols>) ...`` — the single shape ``execute_upsert`` and
#: the upsert-key resolver both need. Bounded and linear (no nested
#: quantifiers), so it cannot backtrack super-linearly.
_UPSERT_HEAD = re.compile(
    r"\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^()]*)\)", re.IGNORECASE
)


def parse_upsert_columns(sql: str) -> list[str] | None:
    """The column list of an ``INSERT INTO <t> (<cols>)`` statement, or None.

    The columns are the authority for the placeholder count and the ON CONFLICT
    target: the driver rebuilds the statement around them, so a caller's
    hand-authored shape can never desync the placeholders from the parameters.

    Returns None for any shape the parser is not confident about (a missing
    column list, an unbalanced paren, a statement longer than the bound) — the
    caller then executes its own statement through the generic path rather
    than risk malformed SQL.
    """
    if not isinstance(sql, str) or len(sql) > _MAX_UPSERT_PARSE_CHARS:
        return None
    m = _UPSERT_HEAD.match(sql)
    if m is None:
        return None
    cols = [c.strip().strip('"') for c in _split_top_level(m.group(2))]
    if not cols or any(not c for c in cols):
        return None
    return cols


#: Bind-parameter arity failures are reported through the driver's own
#: diagnostic channel with the same masking as every other statement, so a
#: future statement shape that loses its placeholders is diagnosable without
#: a debugger attached.
class BindArityError(ValueError):
    """The statement's bind placeholders do not match the parameter count."""

    def __init__(self, sql: str, placeholders: int, params: int, driver: str) -> None:
        self.sql = sql
        self.placeholders = placeholders
        self.params = params
        self.driver = driver
        super().__init__(
            f"{driver}: statement binds {placeholders} placeholder(s) but {params} "
            f"parameter(s) were passed"
        )


def _log_bind_arity_failure(
    operation: str, sql: str, placeholders: int, params: int, driver: str
) -> None:
    """Record a placeholder/parameter mismatch without ever logging values."""
    try:
        from nexus_scalp.database.query_logging import log_query_failure

        log_query_failure(
            operation=f"driver.{operation}",
            exc=BindArityError(sql, placeholders, params, driver),
            sql=sql,
            args=None,
            domain=driver,
            kind="write",
            extra={"placeholder_count": placeholders, "arg_count": params},
        )
    except Exception:
        pass


def check_bind_arity(sql: str, args: Any, *, driver: str, operation: str) -> None:
    """Prove the statement's placeholders match the parameters before execute.

    ``sql`` is the statement as the DRIVER is about to issue it (already
    translated to this driver's paramstyle), and ``args`` is the parameter
    sequence the caller passed. The live incident this guards is
    ``the query has 0 placeholders but 4 parameters were passed``: a statement
    whose placeholders were lost in translation reaches the driver with N
    values bound against zero bind points, and psycopg reports it at the
    server boundary. Checking here turns it into a loud, actionable driver
    error that names the operation and keeps both counts in the log — no
    bound value is ever rendered.

    Raises :class:`BindArityError` on a mismatch. Zero/zero is allowed: a
    statement with no placeholders legitimately takes no parameters.
    """
    placeholders = bind_placeholder_count(sql, driver)
    params = arg_count(args)
    if placeholders == params:
        return
    _log_bind_arity_failure(operation, sql, placeholders, params, driver)
    raise BindArityError(sql, placeholders, params, driver)


def bind_placeholder_count(sql: Any, driver: str = "") -> int:
    """Number of bind placeholders in ``sql`` for the given driver.

    Counts per paramstyle and never sums them: a legal statement uses ONE
    placeholder style only, and a statement mixing styles is itself the defect
    (see the query-logging helper's count for the same rule). The largest
    per-style count wins so a statement reported as qmark-only is not
    miscounted when the driver speaks ``format``.
    """
    if not isinstance(sql, str) or not sql:
        return 0
    try:
        positional = len(_POSITIONAL_PLACEHOLDERS.findall(sql))
        named = len(_NAMED_PLACEHOLDERS.findall(sql))
        dollar = len(_DOLLAR_PLACEHOLDERS.findall(sql))
        pg = max(positional, named, dollar)
        if pg:
            return pg
        return len(_QMARK_PLACEHOLDERS.findall(sql))
    except Exception:
        return 0


def arg_count(args: Any) -> int:
    """Number of bound values passed alongside a statement (never raises).

    Accepts a sequence of values or a sequence of parameter rows
    (``executemany`` shape); a scalar counts as one value, the same contract
    the query-logging helper applies.
    """
    try:
        if args is None:
            return 0
        if isinstance(args, (str, bytes, bytearray, dict)):
            return 1
        n = len(args)
        if n == 0:
            return 0
        first = args[0]
        if isinstance(first, (tuple, list)):
            return len(first)  # executemany: the first parameter row
        return n
    except Exception:
        return 0


#: Placeholder styles the arity check recognizes. ``%s`` / ``%(name)s`` /
#: ``$1`` are the PostgreSQL formats; ``?`` is SQLite qmark (SQLite also
#: accepts the named and dollar styles, so the PG counts win when present).
_POSITIONAL_PLACEHOLDERS = re.compile(r"%(?:[sbt]|\([A-Za-z_][A-Za-z0-9_$]*\)s)")
_NAMED_PLACEHOLDERS = re.compile(r":[A-Za-z_][A-Za-z0-9_$]*")
_DOLLAR_PLACEHOLDERS = re.compile(r"\$\d+")
_QMARK_PLACEHOLDERS = re.compile(r"\?")


#: Bound on the statement text the upsert parsers read (same rationale as the
#: PostgreSQL driver's shape parser: an oversized statement is not the shape
#: they rewrite, and the bound keeps the work linear).
_MAX_UPSERT_PARSE_CHARS = 16_384


def build_upsert_statement(
    table: str,
    columns: Sequence[str],
    *,
    conflict_target: Sequence[str],
    paramstyle: str,
) -> str:
    """Build a driver-native single-row upsert statement.

    The placeholders, the column list and the ON CONFLICT clause are all
    derived from ``columns`` in ONE place, so the statement the driver executes
    cannot carry a placeholder count that disagrees with the row it binds —
    the exact defect a hand-authored cross-provider string produced when its
    ``?`` markers were translated (or a ``:name`` list was flattened) by a
    different layer than the one that counted the parameters.

    ``paramstyle`` selects the placeholder text this driver speaks; the ON
    CONFLICT clause is ANSI UPSERT syntax both providers accept.
    """
    if not columns:
        raise ValueError("build_upsert_statement: empty column list")
    if not conflict_target:
        raise ValueError("build_upsert_statement: empty conflict target")
    quoted_table = _quote_ident(table)
    quoted_cols = ", ".join(_quote_ident(c) for c in columns)
    if paramstyle == "qmark":
        placeholders = ", ".join("?" for _ in columns)
    elif paramstyle in ("format", "pyformat"):
        placeholders = ", ".join("%s" for _ in columns)
    else:  # pragma: no cover - defensive, the drivers own the paramstyles
        placeholders = ", ".join(f":c{i}" for i, _c in enumerate(columns))
    target = ", ".join(_quote_ident(c) for c in conflict_target)
    updates = [c for c in columns if c not in set(conflict_target)]
    set_clause = ", ".join(f"{_quote_ident(c)} = EXCLUDED.{_quote_ident(c)}" for c in updates)
    sql = (
        f"INSERT INTO {quoted_table} ({quoted_cols}) VALUES ({placeholders}) ON CONFLICT ({target})"
    )
    if set_clause:
        sql += f" DO UPDATE SET {set_clause}"
    else:
        sql += " DO NOTHING"
    return sql


def _quote_ident(ident: str) -> str:
    """Validate and quote a simple SQL identifier (module-level helper)."""
    if not isinstance(ident, str):
        raise ValueError("invalid SQL identifier")
    m = _IDENT_SHAPE.fullmatch(ident)
    if m is None:
        raise ValueError("invalid SQL identifier")
    return f'"{m.group(0)}"'


def _table_of(sql: str) -> str | None:
    """The target table of an ``INSERT INTO <t> (...)`` statement."""
    if not isinstance(sql, str):
        return None
    m = _UPSERT_HEAD.match(sql)
    return m.group(1) if m else None


def statement_shape(sql: Any, fallback: str = "statement") -> str:
    """A short, value-free label identifying one execution path.

    Used wherever a diagnostic needs to name WHAT ran without ever rendering
    the statement's bound values: the driver name + verb + table is enough to
    attribute a failure, and it is the only thing logged on the arity path.
    """
    if not isinstance(sql, str) or not sql:
        return fallback
    try:
        m = _UPSERT_HEAD.match(sql)
        if m:
            verb = "insert" if sql.lstrip().upper().startswith("INSERT OR") else "upsert"
            return f"{verb}:{m.group(1)}"
        first = sql.strip().split(None, 1)[0].upper()
        return f"{first.lower()}:?"
    except Exception:
        return fallback


#: SEC (py/sql-injection): the only characters admitted into SQL identifier
#: text. ``quote_ident`` EXTRACTS this match rather than interpolating the
#: caller's string, so nothing outside the whitelist can reach a statement.
_IDENT_SHAPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class DatabaseDriver(ABC):
    """Abstract persistence driver (SQLite / PostgreSQL)."""

    name: str = "abstract"
    #: DB-API paramstyle this driver speaks (qmark | format | pyformat).
    paramstyle: str = "qmark"

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        self._last_auto_conn: Any = None
        #: HEALTH-DBREASON: the most recent exception swallowed by a boolean
        #: driver method (``ping`` / ``closed`` / ``exists``). A caller that
        #: only learns "False" cannot tell a refused connection from a rejected
        #: credential, so the failure is preserved for a health probe to read.
        #: Never carries a secret: the password is resolved out of band from
        #: the secret store and libpq reports the auth OUTCOME, not the value.
        self.last_failure: BaseException | None = None

    # -- helpers ----------------------------------------------------------

    #: Regex used to normalize a statement into a stable query name. Whitespace
    #: collapses and any literal is replaced by ``?`` so the SAME business query
    #: issued with different parameters aggregates under ONE name.
    _LITERAL = re.compile(r"'(?:[^']|'')*'")
    _WS = re.compile(r"\s+")
    _NUM = re.compile(r"\b\d+\b")

    #: Bounded query-name length so a pathological statement cannot flood the
    #: metrics map.
    _QUERY_NAME_MAX_LEN = 120

    def query_name(self, sql: str, fallback: str = "query") -> str:
        """Derive a stable, provider-agnostic name from a statement.

        The name is the statement's normalized SHAPE — whitespace collapsed,
        literals and numbers replaced by ``?`` — truncated to a bounded length.
        It is deliberately NOT a full ``pg_stat_statements`` fingerprint: the
        goal is to group "same business operation" so a workload profile can
        rank by total time / calls / mean latency / rows, while the caller's
        repository-level name remains the primary attribution key.
        """
        try:
            if not isinstance(sql, str) or not sql:
                return fallback
            text = self._WS.sub(" ", sql.strip())
            text = self._LITERAL.sub("?", text)
            text = self._NUM.sub("?", text)
            return text[: self._QUERY_NAME_MAX_LEN]
        except Exception:
            return fallback

    def qmarks(self, count: int) -> str:
        """Provider-native placeholder sequence for `count` params."""
        if self.paramstyle == "qmark":
            return ",".join("?" for _ in range(count))
        if self.paramstyle == "format":
            return ",".join("%s" for _ in range(count))
        return ",".join(f"%s{i}" for i in range(count))  # pyformat

    def quote_ident(self, ident: str) -> str:
        """Validate and quote a simple SQL identifier.

        Returns the whitelist-extracted identifier in double quotes. SEC
        (py/sql-injection): the accepted characters are pulled out of the input
        rather than interpolated whole, so the returned SQL text contains only
        characters the whitelist admits. Note this is defense-in-depth only —
        CodeQL does not treat regex extraction as a sanitizer-barrier, so the
        driver-execute dispositions at the console call sites remain the
        documented resolution for the flagged boundary.
        """
        if not isinstance(ident, str):
            raise ValueError("invalid SQL identifier")
        m = _IDENT_SHAPE.fullmatch(ident)
        if m is None:
            raise ValueError("invalid SQL identifier")
        return f'"{m.group(0)}"'

    def resolve_table_name(self, name: str, conn: Any = None) -> str:
        """Map a caller-supplied table name to the CATALOG's own table name.

        SEC (py/sql-injection): this is the taint boundary for identifiers. The
        returned string is an element of :meth:`list_tables` — a value read from
        the provider's own catalog — and never the caller's argument. A caller
        can therefore only NAME a table that already exists; it can never
        contribute characters to SQL text.

        ``quote_ident`` validates shape (and is kept for that), but a shape
        check cannot satisfy static taint analysis: a validator returns a value
        DERIVED from its input, so taint flows through it. Selecting the value
        out of a catalog does not derive from the input at all, which is what
        makes the result untainted by construction.

        Fail-closed: an unreadable catalog, an unknown name or a malformed one
        all raise ValueError. A caller that cannot prove the table is real must
        not build SQL from it.
        """
        # Shape first: reject junk (null bytes, quotes, spaces, traversal)
        # before any catalog work, and keep the historical ValueError contract.
        if not isinstance(name, str):
            raise ValueError("invalid SQL identifier")
        if _IDENT_SHAPE.fullmatch(name) is None:
            raise ValueError("invalid SQL identifier")
        try:
            known = self.list_tables(conn)
        except Exception as exc:  # fail closed, never fail open
            raise ValueError(f"cannot resolve table name: catalog unreadable: {exc}") from exc
        for candidate in known:
            # Identity comparison against catalog entries ONLY: `candidate` is
            # the catalog's string, so the value that escapes this function is
            # never the caller's input.
            if candidate == name:
                return str(candidate)
        raise ValueError("unknown table")

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

    def get_columns(self, table: str, conn: Any = None) -> list[str]:
        """Convenience method returning a list of column names for a table."""
        return [c["name"] for c in self.table_columns(table, conn=conn)]

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

    def query_readonly(
        self, sql: str, args: Sequence[Any] = (), conn: Any = None
    ) -> list[dict[str, Any]]:
        """SELECT rows as dicts with read-only safety checks where supported."""
        return self.query(sql, args, conn=conn)

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

    def execute_upsert(
        self,
        sql: str,
        params: Sequence[Any],
        *,
        table: str | None = None,
        conflict_target: Sequence[str] | None = None,
        conn: Any = None,
    ) -> Any:
        """Execute a single-row upsert whose SQL the DRIVER generates.

        This is the seam a store uses when it cannot trust its own
        hand-authored placeholder shape across providers. The caller supplies:

          * ``sql`` — its own statement, used ONLY to name the table and the
            column list (both parsed, never interpolated verbatim);
          * ``params`` — the row values in the column order ``sql`` declares;
          * ``conflict_target`` — the ON CONFLICT key columns. Omit to resolve
            them from the upsert-key registry (``upsert_columns``), which is
            validated against the table's DDL.

        The driver rebuilds the statement in its OWN paramstyle with ONE
        placeholder per column, so the placeholders and the parameters are
        produced by the same code from the same column list. The live failure
        this erases — ``the query has 0 placeholders but 4 parameters were
        passed`` — was a statement whose ``?`` markers were rewritten by a
        layer that never saw the parameter tuple.

        Raises :class:`BindArityError` before touching the connection when the
        column list and the parameters disagree.
        """
        columns = parse_upsert_columns(sql)
        if columns is None:
            # Not the single-row INSERT shape: refuse rather than guess what
            # the caller meant — a silently-wrong rewrite is the failure mode
            # this seam exists to eliminate.
            raise ValueError(
                f"{self.name}.execute_upsert: statement is not a single-row "
                f"INSERT INTO <table> (<columns>) statement"
            )
        if len(columns) != len(params):
            _log_bind_arity_failure(
                "execute_upsert", statement_shape(sql), len(columns), len(params), self.name
            )
            raise BindArityError(sql, len(columns), len(params), self.name)
        resolved_table = table or _table_of(sql) or ""
        if not resolved_table:
            raise ValueError(f"{self.name}.execute_upsert: cannot read the target table")
        if conflict_target is None:
            from nexus_scalp.database.upsert import upsert_columns

            conflict_target = upsert_columns(resolved_table)
        statement = build_upsert_statement(
            resolved_table,
            columns,
            conflict_target=conflict_target,
            paramstyle=self.paramstyle,
        )
        check_bind_arity(statement, params, driver=self.name, operation="execute_upsert")
        return self.execute(statement, params, conn=conn)

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
                with contextlib.suppress(Exception):
                    self._conn.rollback()
        finally:
            if self._owns:
                with contextlib.suppress(Exception):
                    self._conn.close()
