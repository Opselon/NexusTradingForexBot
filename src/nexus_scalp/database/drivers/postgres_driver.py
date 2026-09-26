"""PostgreSQL database driver — the scalable/large-dataset/production mode.

Real PostgreSQL support via `psycopg` (v3).  This dependency is OPTIONAL
(``nexus[postgres]``): the application runs fully on SQLite without it, and
only code paths that actually connect to PostgreSQL import it (lazy import
inside the driver).

Driver responsibilities (portability contract):
  * translate ``?`` qmark and ``:name`` placeholders to ``%s`` automatically
    so existing repository SQL works against both providers;
  * translate the SQLite upsert verbs on the generic path —
    ``INSERT OR IGNORE`` → ``ON CONFLICT DO NOTHING`` and
    ``INSERT OR REPLACE`` → ``ON CONFLICT (...) DO UPDATE`` — inside
    :meth:`translate_sql_for_execution`, which every execute/query method
    goes through (``upsert`` / ``insert_ignore`` remain available for
    callers that build rows as dicts, but they are NOT the path stores that
    author their own SQL strings land on);
  * translate SQLite DDL types (INTEGER identity → BIGSERIAL, REAL → DOUBLE
    PRECISION, BLOB → BYTEA, ...) via :meth:`portable_type_for`;
  * never embed the password: it is injected at connect time from the
    secret store through :func:`nexus_scalp.database.config.build_postgres_url`.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, build_postgres_url, mask_url_password
from nexus_scalp.database.drivers._sql_guard import assert_safe_sql
from nexus_scalp.database.drivers.base import _IDENT_SHAPE, DatabaseDriver

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


#: A character that may continue an identifier inside a named placeholder.
_IDENT_CHAR = re.compile(r"[A-Za-z0-9_$]")

#: First character of a SQLite named placeholder (``:name``): a letter or
#: underscore.  This is what distinguishes ``:a`` (placeholder) from ``::``
#: (a PostgreSQL cast) and ``:1`` (not SQLite named syntax).
_NAMED_PLACEHOLDER_START = re.compile(r":[A-Za-z_]")

#: psycopg-native positional placeholder forms that must NOT be re-escaped:
#: psycopg's client-side scanner accepts ``%s``/``%b``/``%t`` and collapses
#: ``%%`` back to ``%``, so these already reach the server as written.
_PG_NATIVE_PLACEHOLDER = re.compile(r"%(?:[sbt]|%|\([A-Za-z_][A-Za-z0-9_$]*\)s)")


def _translate_placeholders(sql: str) -> str:
    """Rewrite SQLite-style placeholders to psycopg format style.

    Translates in ONE quote-aware pass:

      * qmark ``?`` → ``%s``;
      * named ``:name`` → ``%s``.  SQLite's named style is what repositories
        build (``VALUES (:a, :b, ...)`` with the params flattened to a
        positional sequence); psycopg leaves ``:name`` untouched, so without
        this it sees a statement with ZERO placeholders and rejects it with
        ``the query has 0 placeholders but N parameters were passed``.

    Both rewrites happen OUTSIDE single-quoted literals and double-quoted
    identifiers, so string content (URLs, JSON, regex) is never corrupted.
    Named placeholders follow the SQLite rule — a letter or underscore after
    the colon — so ``::`` (PostgreSQL casts) and ``:1`` are left alone.

    Stray percent signs are doubled (``%`` → ``%%``).  psycopg's client-side
    placeholder scanner is NOT quote-aware: it scans the whole statement for
    ``%s`` and raises ``incomplete placeholder: '%'`` on a bare ``%`` —
    including one inside a string literal.  Percent signs that already form a
    psycopg placeholder (``%s``/``%b``/``%t``/``%(name)s``/``%%``) are copied
    as-is, so statements already written in psycopg format keep working.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            # single-quoted literal: copy until closing quote ('' escape).
            # Every ``%`` inside it is doubled: psycopg's scanner reads the
            # whole statement, so a literal must never look like a
            # placeholder (``'a%sb'`` would bind as ``$1``).
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            literal = sql[i : j + 1]
            out.append(literal.replace("%", "%%") if "%" in literal else literal)
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
        elif ch == ":" and _NAMED_PLACEHOLDER_START.match(sql, i):
            # named placeholder: ``:name`` -> ``%s``.  The NAME is dropped,
            # never interpolated: the caller flattens params to positional
            # order, and the rewrite must not turn a name into SQL text.
            j = i + 1
            while j < n and _IDENT_CHAR.match(sql[j]):
                j += 1
            out.append("%s")
            i = j
        elif ch == ":" and i + 1 < n and sql[i + 1] == ":":
            # PostgreSQL cast operator (``::int``): two colons are syntax,
            # never a placeholder, so both are copied verbatim.
            out.append("::")
            i += 2
        elif ch == "%":
            # A psycopg-native placeholder is copied verbatim (doubling it
            # would make the server see a literal ``%s``); anything else is
            # escaped so psycopg's scanner cannot read it as a placeholder.
            m = _PG_NATIVE_PLACEHOLDER.match(sql, i)
            if m is not None:
                out.append(m.group(0))
                i = m.end()
            else:
                out.append("%%")
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


#: Cheap quote-aware scan of the code (non-literal) part of a statement.
#:
#: Reused by the upsert-verb rewriter so a ``?`` or ``OR`` inside a string
#: literal or a quoted identifier is never mistaken for SQL syntax.
def _driver_domain(own_connection: bool) -> str:
    """Domain label for a driver log record: own connection vs a caller's."""
    return "postgresql" if own_connection else "postgresql:tx"


def _log_driver_failure(
    operation: str,
    exc: BaseException,
    sql: str,
    args: Any,
    own_connection: bool,
) -> None:
    """ERROR for one failed driver query; never raises, never logs values."""
    try:
        from nexus_scalp.database.query_logging import log_query_failure

        log_query_failure(
            operation=f"driver.{operation}",
            exc=exc,
            sql=sql,
            args=args,
            domain=_driver_domain(own_connection),
            kind="write" if operation.startswith("execute") else "read",
        )
    except Exception:
        pass


class _DriverQueryTimer:
    """Slow-query timer for the driver (monotonic; log only when slow).

    A cheap object the driver allocates per statement: the fast path is one
    ``time.monotonic()`` read on enter and one comparison + one attribute
    store on exit. The structured log line is only formatted past the
    threshold.
    """

    __slots__ = ("domain", "op", "rows", "sql", "start")

    def __init__(self, op: str, sql: str, domain: str) -> None:
        self.op = op
        self.sql = sql
        self.domain = domain
        self.rows: int | None = None
        self.start: float | None = None

    def __enter__(self) -> _DriverQueryTimer:
        try:
            self.start = time.monotonic()
        except Exception:
            self.start = None
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.start is None:
            return None
        try:
            elapsed = (time.monotonic() - self.start) * 1000.0
        except Exception:
            return None
        if exc_type is None:
            try:
                from nexus_scalp.database.query_logging import log_slow_query

                log_slow_query(
                    operation=f"driver.{self.op}",
                    duration_ms=elapsed,
                    sql=self.sql,
                    rows=self.rows,
                    domain=self.domain,
                )
            except Exception:
                pass
        return None


@contextmanager
def _query_logging(
    operation: str, sql: str, args: Any, *, own_connection: bool
) -> Iterator[_DriverQueryTimer]:
    """Time one driver statement and emit the slow-query WARNING if due.

    ``args`` is accepted but deliberately NOT logged here: the failure path
    (``_log_driver_failure``) is what renders context, and it masks.
    """
    _ = args
    timer = _DriverQueryTimer(operation, sql, _driver_domain(own_connection))
    with timer:
        yield timer


_QUOTED_PATTERN = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")+\"")


def _code_regions(sql: str) -> list[tuple[int, int]]:
    """Spans of ``sql`` OUTSIDE quoted literals/identifiers.

    One linear scan (``re.finditer`` over the quote forms only); the verb and
    ``VALUES`` shape checks then run against the code regions alone so literal
    content can never be read as SQL.
    """
    regions: list[tuple[int, int]] = []
    pos = 0
    for m in _QUOTED_PATTERN.finditer(sql):
        if m.start() > pos:
            regions.append((pos, m.start()))
        pos = m.end()
    if pos < len(sql):
        regions.append((pos, len(sql)))
    return regions


#: ``INSERT OR REPLACE/IGNORE`` head, quote-aware (code regions only).
_INSERT_OR_VERB_PATTERN = re.compile(r"INSERT\s+OR\s+(REPLACE|IGNORE)\b", re.IGNORECASE)


def _parse_insert_or_verb(sql: str) -> tuple[str, str] | None:
    """Split a SQLite upsert verb out of an INSERT statement.

    Returns ``(verb, remainder)`` with ``verb`` ∈ ``{"REPLACE", "IGNORE"}``
    and ``remainder`` the rest of the statement (``" INTO <t> (...)"``), or
    ``None`` when the statement is not a SQLite upsert INSERT.  Quote-aware:
    the verb is matched in the CODE regions only, so an ``OR`` inside a
    string literal is never mistaken for the verb.

    Hot path: one quote scan + one head match, and nothing else for the
    overwhelming majority of statements (every SELECT/UPDATE/plain INSERT
    returns here).
    """
    regions = _code_regions(sql)
    if not regions:
        return None
    # The verb always precedes any value literal, so it lives in the first
    # code region; scanning from its start keeps literal text out of the match.
    text = sql[regions[0][0] :].lstrip()
    m = _INSERT_OR_VERB_PATTERN.match(text)
    if m is None:
        return None
    return (m.group(1).upper(), text[m.end() :])


def _split_top_level(body: str, sep: str = ",") -> list[str]:
    """Split ``body`` on ``sep`` outside parentheses and quotes."""
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
        elif ch == sep and depth == 0:
            parts.append(body[last:i])
            last = i + 1
        i += 1
    parts.append(body[last:])
    return parts


#: ``[INSERT OR REPLACE/IGNORE ]INTO <t> (<cols>) VALUES (...)`` — the
#: single-row shape this rewrite supports confidently.  :func:`_parse_insert_or_verb`
#: has already consumed the verb when this sees the remainder, which starts at
#: ``INTO``; matching ``INSERT`` too keeps the helper usable on full
#: statements.  Multi-row VALUES lists, ``RETURNING``, sub-select bodies and
#: ``DEFAULT VALUES`` are left untouched (fail-open).
_INSERT_SHAPE_PATTERN = re.compile(
    r"^(?:INSERT\s+)?INTO\s+(?P<table>\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<cols>[^()]*)\)\s*"
    r"VALUES\s*\((?P<vals>[^()]*)\)\s*(?P<tail>[^()]*)$",
    re.IGNORECASE | re.DOTALL,
)


def _parse_insert_shape(remainder: str) -> dict[str, Any] | None:
    """Confidently parse ``INTO <t> (<cols>) VALUES (<vals>)``.

    Returns a dict with table/columns/values/tail, or ``None`` when the shape
    is anything but the single-row VALUES form: those statements pass through
    unchanged rather than risk malformed SQL.
    """
    m = _INSERT_SHAPE_PATTERN.match(remainder.strip())
    if m is None:
        return None
    cols = [c.strip().strip('"') for c in _split_top_level(m.group("cols"))]
    if not cols or any(not c for c in cols):
        return None
    # ``VALUES (..),(..)`` is a multi-row list: the conflict target of ONE
    # row cannot be resolved for a batch, and the rewritten clause would be
    # wrong for every row but the first.  Leave it to the caller.
    if m.group("tail").lstrip().startswith(","):
        return None
    # The table name reaches the catalog queries as a bound PARAMETER, so it
    # must be the bare name: a quoted ``"order"`` would look up a table whose
    # name literally contains the quotes and never match a constraint.  The
    # original text is kept separately for the output, so a quoted identifier
    # stays quoted (``_quote_ident`` re-quotes only the conflict target).
    table = m.group("table").strip('"')
    return {
        "table": table,
        "raw_table": m.group("table"),
        "cols": cols,
        "raw_cols": m.group("cols"),
        "vals": m.group("vals"),
        "tail": m.group("tail").strip(),
    }


def _resolve_conflict_target(
    table: str, conn: Any, cols: list[str]
) -> tuple[list[str], bool] | None:
    """Best conflict target for an upsert row — the single resolver.

    Returns ``(target_columns, covers_row)``: PK columns when they are all
    present in the row, else the unique columns present in the row, else
    ``None``.  Used by :meth:`PostgreSQLDriver._conflict_target` (cached per
    table) and by the static execution-translation seam, so the generic write
    path and ``upsert()`` can never disagree on the target.
    """
    pks: list[str] = []
    uniques: list[str] = []
    with contextlib.suppress(Exception):
        rows = conn.execute(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = (SELECT c.oid FROM pg_class c JOIN pg_namespace n "
            "  ON n.oid = c.relnamespace WHERE c.relname = %s AND n.nspname = 'public') "
            "AND i.indisprimary",
            (table,),
        ).fetchall()
        pks = [str(r[0]) for r in rows]
    with contextlib.suppress(Exception):
        rows = conn.execute(
            "SELECT kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "WHERE tc.table_name = %s AND tc.constraint_type = 'UNIQUE' "
            "ORDER BY kcu.ordinal_position",
            (table,),
        ).fetchall()
        uniques = [str(r[0]) for r in rows]
    if pks and all(p in cols for p in pks):
        return (pks, True)
    present_unique = [u for u in uniques if u in cols]
    if present_unique:
        return (present_unique, True)
    if pks:
        return (pks, False)
    return None


def _quote_ident(ident: str) -> str:
    """Validate and quote a simple SQL identifier (the driver's own rule).

    The accepted characters are pulled out of the input rather than
    interpolated whole, so nothing outside the whitelist can reach a
    statement.  Mirrors :meth:`DatabaseDriver.quote_ident` for the static
    translation seam, which has no driver instance.
    """
    if not isinstance(ident, str):
        raise ValueError("invalid SQL identifier")
    m = _IDENT_SHAPE.fullmatch(ident)
    if m is None:
        raise ValueError("invalid SQL identifier")
    return f'"{m.group(0)}"'


def _conflict_clause(parsed: dict[str, Any], conn: Any) -> str | None:
    """Build the ON CONFLICT clause for a parsed INSERT, or None to fail open.

    The target is resolved exactly as :meth:`PostgreSQLDriver.upsert` resolves
    it.  ``ON CONFLICT DO NOTHING`` needs no target, so ``INSERT OR IGNORE``
    always rewrites confidently; ``INSERT OR REPLACE`` without a resolvable
    target returns ``None`` (the caller leaves the statement alone) because a
    bare ``ON CONFLICT ... DO UPDATE`` with no target is a PostgreSQL syntax
    error and a wrong target is worse than no rewrite.
    """
    if parsed["verb"] == "IGNORE":
        return " ON CONFLICT DO NOTHING"
    if conn is None:
        return None
    try:
        target = _resolve_conflict_target(parsed["table"], conn, parsed["cols"])
    except Exception:
        return None
    if target is None:
        return None
    tcols, _covers = target
    if not tcols:
        return " ON CONFLICT DO NOTHING"
    non_key = [c for c in parsed["cols"] if c not in tcols]
    if not non_key:
        return " ON CONFLICT DO NOTHING"
    target_sql = ",".join(_quote_ident(c) for c in tcols)
    sets = ", ".join(f"{_quote_ident(c)} = EXCLUDED.{_quote_ident(c)}" for c in non_key)
    return f" ON CONFLICT ({target_sql}) DO UPDATE SET {sets}"


def _apply_upsert_verb(sql: str, parsed: dict[str, Any], conn: Any) -> str:
    """Rebuild a parsed INSERT with its ON CONFLICT clause (or fail open)."""
    clause = _conflict_clause(parsed, conn)
    if clause is None:
        return sql
    # Only the table name is re-emitted (whitelist-validated at parse time);
    # the column list and values are copied from the already
    # placeholder-translated text, so nothing is translated twice.  A trailing
    # ``;`` is legal caller input but the conflict clause must follow the
    # statement, not the terminator, so it is dropped here (``assert_safe_sql``
    # rejects interior semicolons, a single trailing one adds nothing).
    tail = parsed["tail"].rstrip()
    if tail.endswith(";"):
        tail = tail[:-1].rstrip()
    return (
        "INSERT INTO "
        + parsed.get("raw_table", parsed["table"])
        + " ("
        + parsed["raw_cols"]
        + ") VALUES ("
        + parsed["vals"]
        + ")"
        + clause
        + ((" " + tail) if tail else "")
    )


def _translate_upsert_verb(sql: str, conn: Any) -> str:
    """Placeholder-translate ``sql`` and rewrite its SQLite upsert verb.

    Pure string function apart from the optional conflict-target lookup, and
    a no-op (one quote scan + one head match) for anything that is not an
    ``INSERT OR REPLACE``/``INSERT OR IGNORE`` statement.
    """
    out = _translate_placeholders(sql)
    verb = _parse_insert_or_verb(out)
    if verb is None:
        return out
    kind, remainder = verb
    parsed = _parse_insert_shape(remainder)
    if parsed is None:
        return out  # unparseable VALUES shape: fail open
    parsed["verb"] = kind
    return _apply_upsert_verb(out, parsed, conn)


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
        """Provider-agnostic SQL → PostgreSQL (placeholders).

        Pure string function, no DB I/O: rewrites ``?`` qmark placeholders to
        ``%s`` outside literals/identifiers.  Kept callable with just the SQL
        string — the pooled write plane's static call site (and any other
        pure-string caller) keeps working unchanged.

        The SQLite upsert *verbs* (``INSERT OR REPLACE`` / ``INSERT OR
        IGNORE``) need a connection to resolve the conflict target, so they
        are translated on the execution path via
        :meth:`translate_sql_for_execution`.
        """
        return _translate_placeholders(sql)

    @staticmethod
    def translate_sql_for_execution(sql: str, conn: Any = None) -> str:
        """Translate a statement for execution on a connection.

        Thin module-level seam: kept as a named method so callers that hold a
        connection (the pooled write plane, the portable connection proxy, the
        instance execute/query methods) can reach the verb rewrite without
        importing a private helper, while :meth:`translate_sql` stays the
        pure-string entry point.


        Same contract as :meth:`translate_sql` (placeholders first), and ALSO
        rewrites the SQLite upsert verbs on the generic write path:

          * ``INSERT OR REPLACE INTO <t> (<cols>) VALUES (...)`` →
            ``INSERT INTO <t> (<cols>) VALUES (...) ON CONFLICT (<target>)
            DO UPDATE SET <non-key> = EXCLUDED.<non-key>, ...``
          * ``INSERT OR IGNORE INTO <t> ...`` → ``... ON CONFLICT DO NOTHING``

        The conflict target is resolved exactly as :meth:`upsert` resolves it
        (PK columns when all present in the row, else the table's UNIQUE
        columns present in the row, else the statement is left untouched — a
        bare ``ON CONFLICT`` with no target is a PostgreSQL syntax error).

        """
        return _translate_upsert_verb(sql, conn)

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
            cur = c.execute(
                assert_safe_sql(_translate_upsert_verb(sql, c)),
                tuple(args) if args else None,
            )
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
                # SEC (py/sql-injection #1114 sibling): same boundary as the
                # sqlite driver — the shared guard runs before the engine sees
                # the statement; values stay bound through ``seq``.
                cur.executemany(assert_safe_sql(_translate_upsert_verb(sql, c)), seq)
            if own:
                c.commit()
        finally:
            if own:
                c.close()

    def query(self, sql: str, args: Sequence[Any] = (), conn: Any = None) -> list[dict[str, Any]]:
        own = conn is None
        c = conn or self.connect()
        try:
            cur = c.execute(
                assert_safe_sql(_translate_upsert_verb(sql, c)),
                tuple(args) if args else None,
            )
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
            cur = c.execute(
                assert_safe_sql(_translate_upsert_verb(sql, c)),
                tuple(args) if args else None,
            )
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
            row = c.execute(
                assert_safe_sql(_translate_upsert_verb(sql, c)),
                tuple(args) if args else None,
            ).fetchone()
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

        The resolution itself lives in the module-level
        :func:`_resolve_conflict_target` so the generic write path's SQL
        translation and :meth:`upsert` share ONE resolver and can never
        disagree on a target.
        """
        cached = getattr(self, "_conflict_cache", None)
        if cached is None:
            cached = {}
            self._conflict_cache = cached
        if table not in cached:
            with contextlib.suppress(Exception):
                cached[table] = _resolve_conflict_target(table, conn, cols)
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
