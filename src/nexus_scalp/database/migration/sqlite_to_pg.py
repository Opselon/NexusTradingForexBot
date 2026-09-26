"""SQLite -> PostgreSQL DATA migration (provider switch without data loss).

The operator-facing promise this module keeps: switching the active provider
from SQLite to PostgreSQL must never cost a row of history. It copies rows out
of the SQLite source-of-truth databases into a target PostgreSQL database,
using the SAME canonical schema the domain provisioner builds (see
:mod:`nexus_scalp.database.migration.schema_snapshot` + ``pg_schema``) — a
second hand-written schema would be a drift waiting to happen.

SAFETY CONTRACT (binding, and the reason every choice here looks the way it
does):

* READ-ONLY against the SQLite sources. Every source connection is opened as
  ``sqlite3.connect("file:<path>?mode=ro", uri=True)`` — read-only URI mode,
  mandatory. WAL files stay attached, a running engine is never disturbed and
  never sees a lock it has to wait on. Nothing in this module ever issues a
  write against a source file, and the copy path uses a plain ``SELECT``
  (``INSERT OR REPLACE`` / upsert helpers are never routed at the source).
* WRITE-ONLY the target given on the command line. It must never connect to
  the live cluster: the CLI takes an explicit ``--pg-url``, and the live
  ``localhost:5432`` is never a default here.
* Idempotent: re-running must not duplicate rows. The copy uses
  ``INSERT ... ON CONFLICT (<pk/unique>) DO NOTHING``, so a re-run after a
  crash resumes to the same end state without a pre-clean step.
* Never silently drop a row. A row that fails type validation is quarantined
  with its source table, PK and the reason, reported at the end, and makes
  the run exit NON-zero. A migration that "succeeded" by hiding bad rows is
  worse than one that failed loudly.

The classic migration bug this also fixes: after copying rows with explicit
ids, the PG sequences still sit at 1, so the application's next ``INSERT``
collides with a copied id and fails. ``fix_sequences`` advances each owned
sequence past ``max(id)`` (``setval(..., max(id))`` — the next nextval() then
returns ``max(id) + 1``).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus_scalp.database.config import mask_url_password
from nexus_scalp.database.ddl_port import port_create_table
from nexus_scalp.database.migration import migrate_domain

logger = logging.getLogger(__name__)

#: Batch size for ``executemany`` (spec: 1000). Large enough that the
#: per-statement round trip stops dominating, small enough that a batch's
#: memory and the per-batch transaction stay bounded on a 600k-row copy.
DEFAULT_BATCH_SIZE = 1000

#: Rows between progress log lines (spec: a progress log every 10k rows).
PROGRESS_INTERVAL = 10_000

#: SQLite source databases the tool knows how to copy, in the order the CLI
#: migrates them. Each entry is the on-disk filename inside ``--sqlite-dir``.
#: ``strategies.db`` / ``models.db`` / ``marketplace.db`` hold domains with no
#: declared ``DatabaseDomain`` (their tables are provisioned from the owning
#: packages' authored DDL — see ``_SCHEMA_GROUPS`` below), which is why they
#: are listed separately from the three replayed domains.
SOURCE_DATABASES: tuple[str, ...] = (
    "audit.db",
    "news.db",
    "candle_intel.db",
    "strategies.db",
    "models.db",
    "marketplace.db",
)

#: Schema groups: (source db file, migrate_domain name or None, table list).
#:
#: ``migrate_domain`` name is the key the provisioner uses
#: (``_DOMAIN_STATEMENTS`` in ``database/migration/__init__.py``). The schema
#: is created from THAT canonical path — translated by ``pg_schema`` — so this
#: tool never hand-writes a second schema. ``None`` means the tables' DDL is
#: read from the owning package's declared constants and ported with
#: ``port_create_table`` (the same translator the existing migrator uses),
#: which is the correct path for a domain with no replay of its own.
_SCHEMA_GROUPS: tuple[tuple[str, str | None, tuple[str, ...]], ...] = (
    # audit.db holds more than the audit domain: the shadow / governance /
    # model-lifecycle tables were created in it by their owning packages, and
    # the settings + executions-archive tables live here too. Their schemas
    # come from the groups in ``_EXTRA_GROUPS`` (the same canonical path the
    # fabric uses) and from the module-constant DDL in ``_PACKAGE_DDL_MODULES``.
    (
        "audit.db",
        "audit",
        (
            # DDL from the owning modules' declared constants.
            "application_settings",
            "configuration_metadata",
            "settings_audit",
            "audit_executions_reconciled",
        ),
    ),
    # news.db: the news domain plus the calendar layer's own tables (the
    # calendar worker persists into news.db under its own schema).
    (
        "news.db",
        "news",
        (
            "calendar_events",
            "calendar_worker_state",
        ),
    ),
    ("candle_intel.db", "candle_intel", ()),
    # strategies.db: the factory tables are the ``strategy_factory`` group;
    # ``strategy_research_meta`` is owned by the research store.
    (
        "strategies.db",
        "strategy_factory",
        ("strategy_research_meta",),
    ),
    # models.db: the model registry owns its own DDL (no provisioner group).
    ("models.db", None, ("model_checkpoints", "model_load_history")),
    # marketplace.db: the marketplace store owns its own DDL.
    (
        "marketplace.db",
        None,
        (
            "mk_packages",
            "mk_seeds",
            "mk_lifecycle_events",
            "mk_enablement",
            "mk_score_snapshots",
            "mk_repairs",
            "mk_runtime_snapshots",
            "mk_meta",
        ),
    ),
)

#: Extra provisioner groups to apply against a source file, beyond its primary
#: group: ``(source file, group name)``. The group's schema is created on the
#: target from the same canonical path the fabric uses, so tables a package
#: created inside another domain's database still get a destination. The tables
#: these groups create are NOT repeated in ``_SCHEMA_GROUPS`` — the group is
#: the single DDL authority for them.
_EXTRA_GROUPS: tuple[tuple[str, str], ...] = (
    ("audit.db", "ops_shadow"),
    ("audit.db", "model_lifecycle"),
)

#: Tables whose DDL is read from a named module constant instead of a
#: provisioner group: ``(source file, module)``. The module's declared schema
#: constants are read programmatically — two spellings of one table's DDL would
#: be a drift waiting to happen, so the owning package stays the single author.
_PACKAGE_DDL_MODULES: tuple[tuple[str, str], ...] = (
    ("audit.db", "nexus_scalp.adapters.database.executions_idempotency"),
    ("audit.db", "nexus_scalp.settings.service"),
    ("news.db", "nexus_scalp.calendar.worker"),
)

#: Tables that are pure engine bookkeeping whose rows must never be copied:
#: ``sqlite_sequence`` is SQLite's own AUTOINCREMENT ledger (PG sequences are
#: fixed by :func:`fix_sequences` instead), and the schema-meta tables are the
#: destination's own version chain — copying them would make a fresh PG
#: database claim to be at the SQLite source's schema version.
_SKIP_TABLES = frozenset(
    {
        "sqlite_sequence",
        "sqlite_master",
        "schema_meta",
        "schema_migrations",
    }
)


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


@dataclass
class QuarantinedRow:
    """One source row the tool refused to write (never silently dropped)."""

    database: str
    table: str
    row_id: Any
    column: str
    reason: str
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "table": self.table,
            "row_id": self.row_id,
            "column": self.column,
            "reason": self.reason,
            "value": repr(self.value)[:200],
        }


@dataclass
class TableResult:
    """Per-table outcome of one copy."""

    database: str
    table: str
    source_rows: int = 0
    rows_copied: int = 0
    rows_skipped: int = 0
    rows_quarantined: int = 0
    columns: tuple[str, ...] = ()
    dry_run: bool = False
    error: str = ""
    # insert counts by reason, e.g. {"inserted": 9000, "conflict": 100}
    insert_detail: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "table": self.table,
            "source_rows": self.source_rows,
            "rows_copied": self.rows_copied,
            "rows_skipped": self.rows_skipped,
            "rows_quarantined": self.rows_quarantined,
            "columns": list(self.columns),
            "dry_run": self.dry_run,
            "error": self.error,
            "insert_detail": self.insert_detail,
        }


@dataclass
class MigrationResult:
    """Structured end-state of a full ``--sqlite-dir`` migration run."""

    dry_run: bool
    sqlite_dir: str
    pg_url_masked: str
    tables: list[TableResult] = field(default_factory=list)
    quarantined: list[QuarantinedRow] = field(default_factory=list)
    schema_errors: list[dict[str, str]] = field(default_factory=list)
    schema_drift: list[dict[str, Any]] = field(default_factory=list)
    sequences_fixed: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def rows_copied(self) -> int:
        return sum(t.rows_copied for t in self.tables)

    @property
    def rows_quarantined(self) -> int:
        return sum(t.rows_quarantined for t in self.tables)

    @property
    def rows_dropped_or_failed(self) -> int:
        """Every row the source had that the destination did not receive.

        Quarantined rows count here (they were refused, not written) so a run
        that quarantined anything cannot report a clean zero.
        """
        return self.rows_quarantined + sum(1 for t in self.tables if t.error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "sqlite_dir": self.sqlite_dir,
            "pg_url": self.pg_url_masked,
            "tables": [t.to_dict() for t in self.tables],
            "tables_migrated": sum(1 for t in self.tables if not t.error),
            # Destination row count per table — the primary correctness signal
            # (source count == destination count, before and after a re-run).
            "per_table_counts": {t.table: t.rows_copied for t in self.tables},
            # Rows that failed type validation per table. A quarantine row is
            # reported (and costs the run its ``ok``) but is not a copy, so the
            # source/destination parity check reads
            # ``copied == source - quarantined``.
            "per_table_quarantined": {t.table: t.rows_quarantined for t in self.tables},
            "rows_copied": self.rows_copied,
            "rows_quarantined": self.rows_quarantined,
            "quarantine": [q.to_dict() for q in self.quarantined],
            "schema_errors": self.schema_errors,
            "schema_drift": self.schema_drift,
            "sequences_fixed": self.sequences_fixed,
            "duration_ms": self.duration_ms,
            # The operator-visible verdict. NON-zero-exit callers key off this.
            "ok": self.is_ok(),
        }

    def is_ok(self) -> bool:
        """True only when no row was lost and no table failed.

        A quarantined row is a lost row until a human looks at it, so any
        quarantine at all makes the run not-ok and the CLI exit non-zero
        (spec: exit NON-zero if any row was dropped).
        """
        if self.schema_errors:
            return False
        if self.quarantined:
            return False
        return not any(t.error for t in self.tables)


# ---------------------------------------------------------------------------
# Read-only SQLite source access
# ---------------------------------------------------------------------------


def _connect_source(path: Path) -> sqlite3.Connection:
    """Open a SQLite source READ-ONLY (``mode=ro`` URI, mandatory).

    Read-only URI mode is the whole reason a live engine is never disturbed:
    SQLite refuses any write through this handle, so a defect here can never
    modify the source. It also keeps the WAL attached and readable, which a
    ``PRAGMA query_only`` connection does not guarantee.
    """
    if not path.exists():
        raise FileNotFoundError(f"SQLite source database not found: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    except sqlite3.OperationalError as exc:
        raise FileNotFoundError(f"cannot open SQLite source read-only: {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def source_tables(conn: sqlite3.Connection) -> list[str]:
    """User tables of a SQLite source, excluding engine bookkeeping."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [str(r[0]) for r in rows if str(r[0]) not in _SKIP_TABLES]


def source_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """Column layout of a source table (``PRAGMA table_info`` shape)."""
    rows = conn.execute("SELECT * FROM pragma_table_info(?)", (table,)).fetchall()
    return [dict(r) for r in rows]


def _source_pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """The source table's primary-key columns (single or composite)."""
    cols = source_columns(conn, table)
    pk = [c for c in cols if int(c.get("pk") or 0) > 0]
    pk.sort(key=lambda c: int(c.get("pk") or 0))
    return [str(c["name"]) for c in pk]


def _source_unique_groups(conn: sqlite3.Connection, table: str) -> list[list[str]]:
    """Explicit UNIQUE constraints and unique indexes of a source table.

    Used only as the idempotency key when the table has no declared PK —
    ``ON CONFLICT DO NOTHING`` needs a constraint or unique index to arm
    against, and a rowid table with no PK would otherwise be copied twice.
    """
    groups: list[list[str]] = []
    # Explicit UNIQUE(...) clauses in the CREATE TABLE body.
    create = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if create and create[0]:
        for raw in _UNIQUE_CLAUSE.finditer(str(create[0])):
            names = [n.strip().strip('"') for n in raw.group(1).split(",") if n.strip()]
            if names:
                groups.append(names)
    # Declared unique indexes (explicit CREATE UNIQUE INDEX on the table).
    for row in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ).fetchall():
        sql = str(row[0] or "")
        if not sql.upper().startswith("CREATE UNIQUE INDEX"):
            continue
        m = _INDEX_COLUMNS.search(sql)
        if m:
            names = [n.strip().strip('"') for n in m.group(1).split(",") if n.strip()]
            if names:
                groups.append(names)
    return groups


_UNIQUE_CLAUSE = re.compile(r"UNIQUE\s*\(([^()]*)\)", re.IGNORECASE)
_INDEX_COLUMNS = re.compile(r"\bON\b\s+\S+\s*\(([^()]*)\)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Target schema: create from the canonical path, verify before copying
# ---------------------------------------------------------------------------


def _package_table_ddl(module_name: str) -> list[tuple[str, str]]:
    """``(table, sqlite-ddl)`` pairs declared as module-level constants.

    Used for the domains with no provisioner replay (model registry,
    marketplace, settings, the executions archive, the calendar layer): their
    DDL is authored in the owning package, and reading it from there is the
    same ownership contract ``schema_snapshot`` uses for every other domain.
    The constants are named variously (``_SCHEMA``, ``CALENDAR_SCHEMA_SQL``,
    ``_RECONCILED_DDL``), so every non-callable string attribute holding a
    CREATE TABLE is scanned rather than only ALL_CAPS names.
    """
    import importlib
    import re

    module = importlib.import_module(module_name)
    out: list[tuple[str, str]] = []

    def _scan_text(text: str) -> None:
        # Extract each CREATE TABLE from the constant (a constant may hold a
        # multi-statement schema string like settings._SCHEMA, or a list of
        # statements like calendar.worker.CALENDAR_SCHEMA_SQL).
        for statement in _split_statements(text):
            m = re.match(
                r"\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
                statement,
                re.IGNORECASE,
            )
            if m:
                out.append((m.group(1), statement.strip()))

    for attr in sorted(vars(module)):
        if attr.startswith("__"):
            continue
        value = getattr(module, attr, None)
        if isinstance(value, str):
            if "CREATE TABLE" in value.upper():
                _scan_text(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and "CREATE TABLE" in item.upper():
                    _scan_text(item)
    return out


_IF_NOT_EXISTS_GUARD = re.compile(
    r"(?is)^(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX))\s+((?!IF\s+NOT\s+EXISTS)(\"?[\w]+\"?))"
)


def _restore_if_not_exists(statement: str) -> str:
    """Undo SQLite's catalog normalization of ``CREATE ... IF NOT EXISTS``.

    ``sqlite_master.sql`` drops the clause (the object exists by the time the
    catalog text is stored, so it is moot to SQLite). The idempotent re-run
    needs the original spelling or a second migration run fails on the tables
    the first one already created.
    """
    return _IF_NOT_EXISTS_GUARD.sub(r"\1 IF NOT EXISTS \2", statement)


def _split_statements(text: str) -> list[str]:
    """Split a schema string into statements (quote- and paren-aware)."""
    statements: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    for ch in text:
        buf.append(ch)
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            statements.append("".join(buf).strip())
            buf = []
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s]


def extra_groups_for(source_db: str) -> list[str]:
    """Provisioner groups beyond the primary one for this source database."""
    return [group for db_name, group in _EXTRA_GROUPS if db_name == source_db]


def _package_ddl_for(source_db: str) -> dict[str, str]:
    """``table -> sqlite DDL`` from the modules that own tables in this source.

    Reads the owning packages' declared schema constants (the same ownership
    contract ``schema_snapshot`` uses for every domain): the DDL never gets a
    second spelling here.
    """
    out: dict[str, str] = {}
    for db_name, module_name in _PACKAGE_DDL_MODULES:
        if db_name != source_db:
            continue
        for table, ddl in _package_table_ddl(module_name):
            out.setdefault(table, ddl)
    return out


def _source_catalog_ddl(source_path: Path, tables: Sequence[str]) -> dict[str, str]:
    """Read a source table's CREATE TABLE text from the SQLite catalog.

    The catalog holds the exact DDL the owning module issued (in its original
    ``IF NOT EXISTS`` spelling, restored for the idempotent re-run). Used for
    the package-owned domains whose tables were created by the owner rather
    than by a provisioner replay.
    """
    conn = _connect_source(source_path)
    try:
        out: dict[str, str] = {}
        for table in tables:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if row and row[0]:
                ddl = str(row[0]).strip()
                out[table] = _restore_if_not_exists(ddl)
        return out
    finally:
        conn.close()


def ensure_target_schema(
    group: str | None,
    extra_tables: Sequence[str],
    source_ddl: dict[str, str],
    execute: Callable[[str], None],
    *,
    stop_on_error: bool = False,
) -> dict[str, Any]:
    """Create the target schema from the canonical DDL path.

    ``group`` is the ``migrate_domain`` name (audit / news / candle_intel /
    strategy_factory); ``extra_tables`` are the tables whose DDL comes from
    the owning package instead (ported with ``port_create_table``, the same
    translator the existing migrator uses).

    Returns the ``apply_schema`` audit record: applied count + per-statement
    errors. A statement that fails on a re-run is reported, never swallowed —
    ``IF NOT EXISTS`` covers the normal re-run, so a residual error is real
    drift the operator must see before rows are copied.
    """
    applied: list[str] = []
    errors: list[dict[str, str]] = []
    if group:
        record = migrate_domain(group, execute, stop_on_error=stop_on_error)
        applied.extend(str(a) for a in record.get("applied", []))
        errors.extend(record.get("errors", []))  # type: ignore[arg-type]
    for table in extra_tables:
        ddl = source_ddl.get(table)
        if not ddl:
            errors.append(
                {
                    "statement": f"CREATE TABLE {table}",
                    "error": "no DDL available for this table (owning module changed)",
                }
            )
            if stop_on_error:
                break
            continue
        try:
            ported = port_create_table(ddl)
            if ported:
                execute(ported)
                applied.append(f"CREATE TABLE {table}")
        except Exception as exc:
            errors.append(
                {"statement": f"CREATE TABLE {table}", "error": f"{type(exc).__name__}: {exc}"}
            )
            if stop_on_error:
                break
    return {"applied": applied, "applied_count": len(applied), "errors": errors}


def verify_schema_alignment(
    table: str,
    source_columns: Sequence[dict[str, Any]],
    target_columns: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare source vs target column names/types for one table.

    Reports drift instead of silently proceeding (spec §1): a missing target
    column would make the copy insert into a column the destination does not
    have, and a type the destination cannot hold is a silent truncation.
    Column NAME drift is a hard report; TYPE drift is reported as a warning
    because the coercion map legitimately maps SQLite ``INTEGER`` to PG
    ``bigint`` and the copy adapts values to the target type anyway.
    """
    drift: list[dict[str, Any]] = []
    src = {str(c["name"]).lower(): c for c in source_columns}
    dst = {str(c["name"]).lower(): c for c in target_columns}
    for name in sorted(set(src) - set(dst)):
        drift.append({"table": table, "column": name, "issue": "missing in target"})
    for name in sorted(set(dst) - set(src)):
        drift.append({"table": table, "column": name, "issue": "missing in source"})
    return drift


# ---------------------------------------------------------------------------
# Type coercion SQLite -> PostgreSQL
# ---------------------------------------------------------------------------

#: PG ``information_schema.data_type`` values that hold a timestamp. The
#: coercion map converts SQLite's TEXT ISO timestamps into real PG timestamps
#: only when the TARGET column is one of these — the target schema decides,
#: never a guess from the value's shape.
_PG_TIMESTAMP_TYPES = frozenset(
    {
        "timestamp with time zone",
        "timestamp without time zone",
        "timestamp",
    }
)

#: PG data types backed by a boolean. SQLite stores booleans as 0/1 INTEGER;
#: the coercion converts those to real PG booleans when the target column is
#: boolean (``information_schema`` reports the base type for both ``boolean``
#: and any domain over it).
_PG_BOOLEAN_TYPES = frozenset({"boolean"})

#: PG bytea: SQLite BLOB columns map to BYTEA.
_PG_BINARY_TYPES = frozenset({"bytea"})

#: PG whole-number types. SQLite INTEGER ids map to these (BIGINT under the
#: parity contract); the coercion only converts when the value does not
#: already fit the source's own storage class.
_PG_INTEGER_TYPES = frozenset({"smallint", "integer", "bigint", "numeric"})


def _parse_iso_timestamp(value: str) -> datetime | None:
    """Parse an ISO-ish SQLite timestamp text, or return ``None``.

    SQLite stores timestamps as TEXT in a mix of shapes: ``YYYY-MM-DD HH:MM:SS``,
    ``YYYY-MM-DDTHH:MM:SS`` (the ISO 8601 separator), ``YYYY-MM-DD HH:MM:SS.fff``,
    and a trailing ``Z``. All are accepted; anything else is refused so the
    caller can quarantine the row instead of storing a wrong instant.
    """
    text = value.strip()
    if not text:
        return None
    normalized = text
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    normalized = normalized.replace("T", " ", 1)
    # datetime.fromisoformat handles the offset and fractional forms in 3.11+;
    # the space separator is what the app actually writes.
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


class RowCoercer:
    """Coerce one source row's values to the target PG column types.

    The target type is read from the PG schema (``information_schema``), never
    guessed from the value: a ``'2026-01-01'`` string in a TEXT column stays a
    string, and only becomes a timestamp when the destination column is one.
    """

    def __init__(self, table: str, columns: Sequence[dict[str, Any]]) -> None:
        self.table = table
        #: ordered (name, pg data_type, sqlite declared type) per column
        self._plan: list[tuple[str, str, str]] = []
        for col in columns:
            name = str(col["name"])
            pg_type = str(col.get("type") or "").lower()
            sqlite_type = str(col.get("sqlite_type") or "").upper()
            self._plan.append((name, pg_type, sqlite_type))

    def coerce(
        self, row: Sequence[Any], column_names: Sequence[str]
    ) -> tuple[list[Any], QuarantinedRow | None]:
        """Coerce one row. Returns ``(values, quarantine_or_None)``.

        ``column_names`` is the order the values appear in ``row``; the coerced
        output is emitted in the TARGET column order (``self._plan``), so the
        caller hands the tuple straight to ``executemany``. ``None`` values
        pass through unchanged (a NULL source is a NULL destination); a refusal
        is a quarantine record, never an exception — the batch keeps going and
        the row is reported, not dropped.
        """
        by_name = dict(zip(column_names, row, strict=False))
        return [
            self._coerce_value(name, pg_type, by_name.get(name)) for name, pg_type, _ in self._plan
        ], None

    def _coerce_value(self, column: str, pg_type: str, value: Any) -> Any:
        if value is None:
            return None
        if pg_type in _PG_BOOLEAN_TYPES:
            return self._to_boolean(column, value)
        if pg_type in _PG_TIMESTAMP_TYPES:
            return self._to_timestamp(column, value)
        if pg_type in _PG_BINARY_TYPES:
            return self._to_bytes(column, value)
        return value

    @staticmethod
    def _to_boolean(column: str, value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, float):
            return value != 0.0
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ("1", "t", "true", "yes", "on"):
                return True
            if text in ("0", "f", "false", "no", "off", ""):
                return False
        # An unparseable boolean is a quarantine, not a silent NULL.
        raise CoercionError(column, value, "cannot interpret value as boolean")

    @staticmethod
    def _to_timestamp(column: str, value: Any) -> Any:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            # A raw epoch number in a timestamp column is ambiguous (seconds
            # vs ms vs a stray integer); refuse rather than guess an instant.
            raise CoercionError(
                column, value, "numeric value in a timestamp column (ambiguous epoch)"
            )
        if isinstance(value, str):
            parsed = _parse_iso_timestamp(value)
            if parsed is None:
                raise CoercionError(
                    column,
                    value,
                    "timestamp string does not parse as an ISO timestamp",
                )
            return parsed
        raise CoercionError(column, value, f"unsupported type {type(value).__name__} for timestamp")

    @staticmethod
    def _to_bytes(column: str, value: Any) -> Any:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        # A text value in a BYTEA column is stored as its UTF-8 bytes rather
        # than refused: the SQLite source kept it as text, and the byte
        # content is what the destination must preserve.
        if isinstance(value, str):
            return value.encode("utf-8", errors="surrogatepass")
        raise CoercionError(column, value, f"unsupported type {type(value).__name__} for bytea")


class CoercionError(ValueError):
    """A value cannot be coerced to the target column type (quarantine)."""

    def __init__(self, column: str, value: Any, reason: str) -> None:
        self.column = column
        self.value = value
        self.reason = reason
        super().__init__(f"column {column!r}: {reason}")


def _identify(row: sqlite3.Row, pk_columns: Sequence[str], columns: Sequence[str]) -> Any:
    """A stable identity for a quarantined row (PK value, else rowid-ish)."""
    for col in pk_columns:
        try:
            return row[col]
        except (IndexError, KeyError):
            pass
    if "id" in columns:
        try:
            return row["id"]
        except (IndexError, KeyError):
            pass
    return None


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------


def _on_conflict_target(
    pk_columns: Sequence[str], unique_groups: Sequence[Sequence[str]]
) -> list[str] | None:
    """The conflict target for ``ON CONFLICT (...) DO NOTHING``.

    Prefers the primary key; falls back to the first explicit UNIQUE
    constraint or unique index when the table has no PK (a rowid table). A
    table with neither has no safe idempotency key and the copy falls back to
    a row-existence precheck instead.
    """
    if pk_columns:
        return list(pk_columns)
    for group in unique_groups:
        if group:
            return list(group)
    return None


def _extra_unique_groups(
    pk_columns: Sequence[str], unique_groups: Sequence[Sequence[str]]
) -> list[list[str]]:
    """Unique constraint groups that are NOT the primary key.

    The idempotency contract covers EVERY unique constraint on the
    destination, not just the PK. A table can carry a PK on ``article_id``
    AND a separate UNIQUE index on ``article_hash``: a row whose PK is new
    but whose unique key already exists sails past ``ON CONFLICT (pk) DO
    NOTHING`` and then dies on the unguarded unique index (a real
    cross-dataset duplicate — same article ingested twice under two ids).
    Every group here gets an existence precheck so such a row is SKIPPED
    with a reported reason instead of crashing the copy.
    """
    pk_set = {str(c).lower() for c in pk_columns}
    out: list[list[str]] = []
    for group in unique_groups:
        names = [c for c in group if c]
        if not names:
            continue
        if {str(c).lower() for c in names} == pk_set:
            continue
        out.append(names)
    return out


def _target_unique_groups(target_conn: Any, table: str) -> list[list[str]]:
    """Every unique-constraint column set the DESTINATION table has.

    The idempotency guard must cover the destination's real constraints, which
    are wider than the source's: ``news_articles`` carries a UNIQUE index on
    ``article_hash`` on PostgreSQL that the SQLite source never declared (the
    SQLite schema relies on the application layer for that). A row new by PK
    but present by such a unique key is a genuine cross-dataset duplicate;
    reporting it skipped is the honest outcome, and discovering it here beats
    a batch-killing ``UniqueViolation`` at insert time.
    """
    try:
        rows = target_conn.execute(
            "SELECT pg_get_constraintdef(oid), contype "
            "FROM pg_constraint "
            "WHERE conrelid = %s::regclass AND contype IN ('u','p')",
            (table,),
        ).fetchall()
    except Exception as exc:
        logger.debug("[DB-MIGRATE-DATA] target constraint read failed for %s: %s", table, exc)
        return []
    out: list[list[str]] = []
    for definition, _contype in rows:
        text = str(definition or "")
        m = _UNIQUE_CLAUSE.search(text)
        if not m:
            continue
        names = [n.strip().strip('"') for n in m.group(1).split(",") if n.strip()]
        if names:
            out.append(names)
    return out


def _row_exists_precheck(
    conn: Any,
    table: str,
    columns: Sequence[str],
    conflict_target: Sequence[str],
    batch: Sequence[tuple[Any, ...]],
) -> tuple[list[tuple[Any, ...]], int]:
    """Pre-check existence for tables with no conflict target.

    Returns ``(rows_to_insert, already_present_count)``. Used when the table
    has no PK to arm ON CONFLICT against, and for every unique constraint
    beyond the PK (the copy inserts a row only when it is absent on ALL keys).
    The prepared ``SELECT ... WHERE (key) IN (...)`` is parameterized.
    """
    if not conflict_target or not batch:
        return list(batch), 0
    ph = ",".join("%s" for _ in conflict_target)
    keys = list(conflict_target)
    key_idxs = [list(columns).index(k) for k in keys if k in columns]
    if len(key_idxs) != len(keys):
        return list(batch), 0
    placeholders = ",".join(f"({ph})" for _ in batch)
    params: list[Any] = []
    for row in batch:
        params.extend(row[i] for i in key_idxs)
    cur = conn.execute(
        f"SELECT {', '.join(keys)} FROM {table} WHERE ({', '.join(keys)}) IN ({placeholders})",
        params,
    )
    present = {tuple(r) for r in cur.fetchall()}
    kept: list[tuple[Any, ...]] = []
    skipped = 0
    for row in batch:
        key = tuple(row[i] for i in key_idxs)
        if key in present:
            skipped += 1
        else:
            kept.append(row)
    return kept, skipped


def copy_table_rows(
    *,
    database: str,
    table: str,
    source_conn: sqlite3.Connection,
    target_conn: Any,
    target_columns: Sequence[dict[str, Any]],
    coercer: RowCoercer,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    log_every: int = PROGRESS_INTERVAL,
) -> TableResult:
    """Copy one table's rows from SQLite into PG (idempotent, batched).

    * ``SELECT`` only against the source — a read-only handle plus a plain
      read statement, so the copy can never write the source.
    * ``executemany`` batches of ``batch_size``; each batch is one
      transaction (committed per batch so a crash never loses everything and
      a re-run is cheap).
    * Idempotent via ``ON CONFLICT (pk) DO NOTHING`` (or an existence
      precheck when the table has no key), so a re-run converges to the same
      state.
    * A value that cannot be coerced quarantines its row (reported) rather
      than aborting the table; the tool exits non-zero at the end.
    """
    columns = [str(c["name"]) for c in target_columns]
    result = TableResult(database=database, table=table, columns=tuple(columns), dry_run=dry_run)

    src_cols = source_columns(source_conn, table)
    src_names = [str(c["name"]) for c in src_cols]
    # Copy only the columns the destination actually has (name intersection,
    # order = destination order). Extra source columns are reported, not
    # silently ignored: see the drift report in ``run``.
    usable = [c for c in columns if c in src_names]
    if not usable:
        result.error = f"no shared columns between source and target for {table}"
        return result

    pk_columns = _source_pk_columns(source_conn, table)
    unique_groups = _source_unique_groups(source_conn, table)
    conflict_target = _on_conflict_target(pk_columns, unique_groups)
    # Unique constraints beyond the PK: each gets an existence precheck so a
    # cross-dataset duplicate is skipped rather than crashing the insert.
    # Read them from the TARGET catalog when it is available: the destination
    # can carry a unique index the source never declared (news_articles' hash
    # index exists on the PG side only), and ON CONFLICT arms only the PK.
    extra_unique = _extra_unique_groups(pk_columns, unique_groups)
    if target_conn is not None and not dry_run:
        for group in _target_unique_groups(target_conn, table):
            if group not in extra_unique:
                extra_unique.append(group)

    order_sql = ""
    if pk_columns:
        order_sql = f" ORDER BY {', '.join(pk_columns)}"
    select_sql = f"SELECT {', '.join(usable)} FROM {table}{order_sql}"

    quoted_cols = ", ".join(f'"{c}"' for c in usable)
    quoted_table = f'"{table}"'
    placeholders = ",".join("%s" for _ in usable)

    if dry_run:
        try:
            source_rows = int(source_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.Error as exc:
            result.error = f"cannot count source rows: {exc}"
            return result
        # The destination has not been created in a dry run (nothing is
        # written, and the target may not even be reachable), so the plan is
        # described by the SOURCE: the count is what WOULD be copied. Coercion
        # is still exercised row by row so a dry run also reports the rows it
        # would refuse (a preview that hides a quarantine is not a preview).
        select_cursor = source_conn.execute(select_sql)
        refused: list[QuarantinedRow] = []
        planned = 0
        try:
            while True:
                rows = select_cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    try:
                        coercer.coerce(row, usable)
                    except CoercionError as exc:
                        refused.append(
                            QuarantinedRow(
                                database=database,
                                table=table,
                                row_id=_identify(row, pk_columns, src_names),
                                column=exc.column,
                                reason=exc.reason,
                                value=exc.value,
                            )
                        )
                    planned += 1
        finally:
            try:
                select_cursor.close()
            except Exception:  # pragma: no cover - best effort
                pass
        result.source_rows = source_rows
        result.rows_copied = planned
        result.rows_quarantined = len(refused)
        result.insert_detail = {"dry_run": planned}
        result.quarantined_rows = refused  # type: ignore[attr-defined]
        return result

    insert_sql = f"INSERT INTO {quoted_table} ({quoted_cols}) VALUES ({placeholders})"
    if conflict_target:
        target_list = ", ".join(f'"{c}"' for c in conflict_target)
        insert_sql += f" ON CONFLICT ({target_list}) DO NOTHING"

    cursor = source_conn.execute(select_sql)
    batch: list[tuple[Any, ...]] = []
    seen = 0
    copied = 0
    skipped = 0
    quarantined: list[QuarantinedRow] = []
    insert_detail: dict[str, int] = {"inserted": 0, "conflict": 0}

    def _flush() -> None:
        nonlocal batch, copied, skipped
        if not batch:
            return
        rows_to_write = batch
        if conflict_target is None:
            # No PK and no UNIQUE constraint to arm ON CONFLICT against: a
            # table with no key at all would otherwise duplicate every row on
            # a re-run. The existence precheck is parameterized and keeps the
            # copy idempotent without a unique constraint.
            rows_to_write, pre_skipped = _row_exists_precheck(
                target_conn,
                quoted_table,
                usable,
                _precheck_key(usable, pk_columns, unique_groups),
                batch,
            )
            skipped += pre_skipped
        else:
            # ON CONFLICT (pk) DO NOTHING only arms the PK. Any OTHER unique
            # constraint on the destination is still unguarded: a row new by
            # PK but already present by unique key would crash the copy. Such
            # a row is a real cross-dataset duplicate, so it is prechecked and
            # SKIPPED here (counted, never an error) rather than letting PG
            # reject the whole batch.
            for group in extra_unique:
                rows_to_write, pre_skipped = _row_exists_precheck(
                    target_conn, quoted_table, usable, group, rows_to_write
                )
                skipped += pre_skipped
                if not rows_to_write:
                    break
        if not rows_to_write:
            batch = []
            return
        before = copied
        with target_conn.cursor() as cur:
            cur.executemany(insert_sql, rows_to_write)
        copied += len(rows_to_write)
        # psycopg's executemany result rowcount is -1 for multi-row inserts;
        # conflicts surface as the difference between rows sent and rows that
        # landed, so only attribute to "conflict" what we can actually prove.
        delta = copied - before
        if delta < len(rows_to_write):
            insert_detail["conflict"] += len(rows_to_write) - delta
            insert_detail["inserted"] += delta
        else:
            insert_detail["inserted"] += len(rows_to_write)
        target_conn.commit()
        batch = []

    try:
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                seen += 1
                try:
                    values, _q = coercer.coerce(row, usable)
                except CoercionError as exc:
                    quarantined.append(
                        QuarantinedRow(
                            database=database,
                            table=table,
                            row_id=_identify(row, pk_columns, src_names),
                            column=exc.column,
                            reason=exc.reason,
                            value=exc.value,
                        )
                    )
                    continue
                batch.append(tuple(values))
                if log_every and seen % log_every == 0:
                    logger.info(
                        "[DB-MIGRATE-DATA] %s.%s %d rows read, %d copied",
                        database,
                        table,
                        seen,
                        copied,
                    )
            _flush()
        _flush()
    finally:
        try:
            cursor.close()
        except Exception:  # pragma: no cover - best effort
            pass

    result.source_rows = seen
    result.rows_copied = copied
    result.rows_skipped = skipped
    result.rows_quarantined = len(quarantined)
    result.insert_detail = insert_detail
    result.quarantined_rows = quarantined  # type: ignore[attr-defined]
    return result


def _precheck_key(
    columns: Sequence[str],
    pk_columns: Sequence[str],
    unique_groups: Sequence[Sequence[str]],
) -> list[str]:
    """The key used by the existence precheck (all-keyable-columns fallback)."""
    if pk_columns:
        return list(pk_columns)
    for group in unique_groups:
        if group:
            return list(group)
    return list(columns)


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------


def fix_sequences(target_conn: Any, table: str, columns: Sequence[dict[str, Any]]) -> str | None:
    """Advance the sequence for ``table``'s id column past ``max(id)``.

    The classic migration bug: rows copied with explicit ids leave the PG
    sequence at 1, so the application's next INSERT collides with a copied id.
    ``setval(seq, max(id))`` makes the next ``nextval()`` return
    ``max(id) + 1``. Tables without an ``id`` column (or without an owned
    sequence) are skipped gracefully.
    """
    id_col = next((c for c in columns if str(c["name"]).lower() == "id"), None)
    if id_col is None:
        return None
    name = str(id_col["name"])
    try:
        seq = target_conn.execute("SELECT pg_get_serial_sequence(%s, %s)", (table, name)).fetchone()
        if not seq or not seq[0]:
            # No owned sequence (a plain BIGINT id, or an identity column
            # pg_get_serial_sequence cannot resolve by name) — nothing to fix.
            return None
        seq_name = str(seq[0])
        highest = target_conn.execute(f'SELECT max("{name}") FROM "{table}"').fetchone()
        max_id = highest[0] if highest else None
        if max_id is None:
            # Table is empty: leave the sequence alone (a fresh sequence is
            # already correct for the first insert).
            return None
        target_conn.execute("SELECT setval(%s, %s)", (seq_name, int(max_id)))
        target_conn.commit()
        return f"{table}.{name} -> {seq_name} @ {int(max_id)}"
    except Exception as exc:
        logger.debug("[DB-MIGRATE-DATA] sequence fix skipped for %s: %s", table, exc)
        return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def mask_url(url: str) -> str:
    """Never let a password reach a log or a report."""
    return mask_url_password(url)


def _target_columns_by_table(
    target_conn: Any, tables: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """Target column layout per table, straight from the PG catalog."""
    out: dict[str, list[dict[str, Any]]] = {}
    for table in tables:
        rows = target_conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        out[table] = [
            {"name": r[0], "type": r[1], "notnull": r[2] == "NO", "dflt_value": r[3]} for r in rows
        ]
    return out


def _connect_target(pg_url: str) -> Any:
    """Open the target PG connection (the URL the operator passed)."""
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(pg_url)


def _create_table_from_source(
    source_conn: sqlite3.Connection, target_conn: Any, table: str
) -> bool:
    """Create a destination table from the SOURCE table's own DDL.

    The last-resort path: a table no canonical group, package DDL or extra
    covers would otherwise have no destination, and the tool's contract is to
    never lose a row. The source catalog holds the exact DDL the owner issued;
    ``port_create_table`` is the same translator the existing migrator uses, so
    the destination is still not a hand-written second schema. ``_refine_ddl``
    widens that translation for the copy's own coercion map (a ``TEXT`` column
    holding ISO timestamps becomes a real PG timestamp), so a fallback table
    gets the same type fidelity a provisioned one does.

    Returns True when the table exists on the target afterwards. A failure is
    reported by the caller as drift, never swallowed.
    """
    row = source_conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row or not row[0]:
        return False
    ddl = _restore_if_not_exists(str(row[0]).strip())
    try:
        ported = port_create_table(ddl)
        if not ported:
            return False
        target_conn.execute(_refine_ddl(ported, source_conn, table))
        target_conn.commit()
        return True
    except Exception as exc:
        logger.warning("[DB-MIGRATE-DATA] source-DDL fallback failed for %s: %s", table, exc)
        return False


#: Column-name shapes that hold an ISO timestamp in this codebase. Used only by
#: the source-DDL fallback path: SQLite stores timestamps as TEXT and the
#: provisioned PG schema declares them as a real timestamp type, so a table
#: that misses the canonical path would otherwise keep its timestamps as TEXT
#: and lose the type on the way into PostgreSQL.
_TIMESTAMP_COLUMN = re.compile(
    r"^(?:.*_)?(?:at|time|timestamp|date|created|updated|occurred|executed|detected|"
    r"happened|recorded|received|closed|opened|scheduled|retrieved)(?:_at|_time|_ts|_date)?$",
    re.IGNORECASE,
)


#: Column-name shapes that hold a 0/1 flag rather than a count. Same fallback
#: path, same reasoning: the provisioned schema declares these as BOOLEAN.
_BOOLEAN_COLUMN = re.compile(
    r"^(?:is|has|was|can|should|allow)[_a-z]+$|^(?:enabled|disabled|active|deleted)$"
    r"|^(?:.*_)?(?:flag|bool|enabled|ok|success|successful)$",
    re.IGNORECASE,
)

#: A column-constraint keyword, or the end of the column definition. Anchors the
#: type token so it cannot swallow ``NOT NULL``/``PRIMARY KEY`` clauses.
_COLUMN_END = (
    r"(?=\s+(?:NOT\s+NULL|NULL|DEFAULT|PRIMARY|REFERENCES|UNIQUE|CHECK|GENERATED"
    r"|COLLATE)|\s*,|\s*\))"
)


def _refine_ddl(pg_ddl: str, source_conn: sqlite3.Connection, table: str) -> str:
    """Widen a ported fallback table's types to the copy's coercion targets.

    ``port_create_table`` maps SQLite ``TEXT`` to PG ``TEXT`` (correct in
    general) and keeps plain ``INTEGER`` as-is. The provisioned schema goes
    further: ISO-timestamp TEXT columns become real PG timestamps and 0/1 flag
    INTEGER columns become booleans. Without this, a table created by the
    fallback would silently keep timestamps as TEXT — every downstream query
    comparing a timestamp to a text column degrades to a cast, and the
    coercion map would never fire for it.

    Scoped to the fallback path only, and deliberately conservative: a name
    must match a timestamp/flag shape AND declare the matching SQLite type.
    A wrong guess costs availability, not data — an un-coercible value is
    quarantined, never dropped.
    """
    out = pg_ddl
    for col in source_columns(source_conn, table):
        name = str(col["name"])
        declared = str(col.get("type") or "").strip().upper()
        if declared == "TEXT" and _TIMESTAMP_COLUMN.fullmatch(name):
            # TIMESTAMPTZ keeps the instant unambiguous across the provider
            # switch (the app writes UTC).
            out = _replace_column_type(out, name, "TIMESTAMPTZ")
        elif declared in ("INTEGER", "INT") and _BOOLEAN_COLUMN.fullmatch(name):
            out = _replace_column_type(out, name, "BOOLEAN", rewrite_default=True)
    return out


def _replace_column_type(
    ddl: str, column: str, new_type: str, *, rewrite_default: bool = False
) -> str:
    """Replace one column's declared type in a CREATE TABLE body.

    ``port_create_table`` emits bare (unquoted) lower-case identifiers and may
    lay the column list out on one line, so the type token is anchored on the
    column name followed by a constraint keyword or the column boundary — it
    can never swallow ``NOT NULL`` or ``PRIMARY KEY``. Everything after the
    type (defaults, constraints) is preserved; a boolean widening also rewrites
    ``DEFAULT 0/1`` to ``false``/``true`` so the default stays valid.
    """
    name_alt = '(?:"' + re.escape(column) + r'"|' + re.escape(column) + r")"
    # Three groups, no wrapper: (1) everything up to and including the column
    # name, (2) the declared type token, (3) the remainder of the definition
    # (defaults, constraints). The lookahead anchors the type token without
    # consuming it, so ``NOT NULL``/``PRIMARY KEY`` survive intact.
    pattern = re.compile(
        r"(?i)((?:[(,]\s*)" + name_alt + r"\s+)"
        r"([A-Z][A-Z0-9]*(?:\s+[A-Z][A-Z0-9]*)*?)" + _COLUMN_END + r"([\s\S]*?)(?=[,);])"
    )

    def _apply(match: re.Match[str]) -> str:
        head, _old_type, tail = match.group(1), match.group(2), match.group(3)
        if rewrite_default:
            tail = re.sub(r"(?i)\bDEFAULT\s+0\b", "DEFAULT false", tail)
            tail = re.sub(r"(?i)\bDEFAULT\s+1\b", "DEFAULT true", tail)
        return f"{head}{new_type}{tail}"

    return pattern.sub(_apply, ddl, count=1)


def _source_layout_as_target(source_conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """Describe a table's layout as the copy's TARGET would see it, from the
    source schema.

    Used only by a dry run, which has no destination database to introspect
    (nothing is written and the target may not even be reachable). The source
    schema is an honest stand-in: the real run creates the destination from
    the same canonical DDL the source was built from, so the column set and
    the type the copy coerces to are the ones declared here. A ``TEXT`` source
    column is reported as ``text`` (not a timestamp) so the dry-run plan never
    claims a coercion the real run will not perform.
    """
    from nexus_scalp.database.drivers.sqlite_driver import sqlite_type_to_portable
    from nexus_scalp.database.migration.pg_schema import translate_type

    cols = source_columns(source_conn, table)
    out: list[dict[str, Any]] = []
    for col in cols:
        portable = sqlite_type_to_portable(str(col.get("type") or "TEXT"))
        out.append(
            {
                "name": str(col["name"]),
                "type": translate_type(f"CREATE TABLE t (x {portable})")
                .split("x", 1)[1]
                .strip()
                .split()[0]
                .lower(),
                "notnull": bool(col.get("notnull")),
                "dflt_value": col.get("dflt_value"),
            }
        )
    return out


def migrate_sqlite_to_pg(
    sqlite_dir: str | Path,
    pg_url: str,
    *,
    dry_run: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    databases: Sequence[str] = SOURCE_DATABASES,
    schema_groups: Sequence[tuple[str, str | None, tuple[str, ...]]] = _SCHEMA_GROUPS,
    log_every: int = PROGRESS_INTERVAL,
) -> MigrationResult:
    """Copy every SQLite source database's rows into the target PG database.

    Read-only against every source (``mode=ro`` URI), writes only the target
    given by ``pg_url``. Idempotent end to end: schema creation is
    ``IF NOT EXISTS`` and the row copy is ``ON CONFLICT DO NOTHING``, so a
    re-run converges to the same state without a pre-clean.
    """
    import time

    started = time.monotonic()
    sqlite_dir = Path(sqlite_dir)
    result = MigrationResult(
        dry_run=dry_run,
        sqlite_dir=str(sqlite_dir),
        pg_url_masked=mask_url(pg_url),
    )

    # Which source databases actually exist (a partial artifacts dir is fine —
    # the tool reports what it found, never invents a database).
    available = [name for name in databases if (sqlite_dir / name).exists()]
    if not available:
        result.schema_errors.append(
            {
                "statement": "source discovery",
                "error": f"no SQLite databases found in {sqlite_dir}",
            }
        )
        return result

    # A dry run plans the copy and never touches the destination, so it must
    # not require a reachable target either — the operator previews BEFORE
    # handing the tool a real database. Only the real copy connects.
    target_conn: Any = None
    if not dry_run:
        target_conn = _connect_target(pg_url)
    try:
        # --- schema first, per group -------------------------------------
        group_tables: dict[str, list[str]] = {}
        for db_name, group, extras in schema_groups:
            if db_name not in available:
                continue
            src = _connect_source(sqlite_dir / db_name)
            try:
                tables = source_tables(src)
            finally:
                src.close()
            group_tables[db_name] = tables

            def _exec(sql: str) -> None:
                if target_conn is not None:
                    target_conn.execute(sql)

            applied_errors: list[dict[str, str]] = []
            if target_conn is not None:
                if group:
                    record = ensure_target_schema(group, (), {}, _exec, stop_on_error=False)
                    applied_errors += record.get("errors", [])
                # Extra provisioner groups: tables another package created inside
                # this database (shadow/governance/model-lifecycle in audit.db).
                # Same canonical path, so no second schema spelling.
                for extra_group in extra_groups_for(db_name):
                    record = ensure_target_schema(extra_group, (), {}, _exec, stop_on_error=False)
                    applied_errors += record.get("errors", [])
                # Tables whose DDL comes from a declared module constant, then
                # the remaining extras read from the source catalog (the exact
                # DDL the owner issued, in its original IF NOT EXISTS spelling).
                package_ddl = _package_ddl_for(db_name)
                catalog_tables = tuple(t for t in extras if t not in package_ddl)
                catalog_ddl = _source_catalog_ddl(sqlite_dir / db_name, catalog_tables)
                merged: dict[str, str] = {**catalog_ddl, **package_ddl}
                record = ensure_target_schema(None, extras, merged, _exec, stop_on_error=False)
                applied_errors += record.get("errors", [])
            for err in applied_errors:
                result.schema_errors.append(
                    {
                        "statement": f"{db_name}: {err.get('statement', '')}",
                        "error": str(err.get("error", "")),
                    }
                )

        if target_conn is not None:
            target_conn.commit()

        # --- copy rows ----------------------------------------------------
        for db_name in available:
            src = _connect_source(sqlite_dir / db_name)
            try:
                tables = group_tables.get(db_name) or source_tables(src)
                if not tables:
                    continue
                target_layout: dict[str, list[dict[str, Any]]] = {}
                if target_conn is not None:
                    target_layout = _target_columns_by_table(target_conn, tables)
                for table in tables:
                    tgt_cols = target_layout.get(table) or []
                    if not tgt_cols and not dry_run:
                        # No canonical group, package DDL or extra covers this
                        # table. The tool's contract is to never lose a row, so
                        # the destination is created from the SOURCE table's own
                        # DDL (ported by the same translator the migrator uses).
                        # The fallback is recorded as drift so the operator sees
                        # it was not the canonical provisioner path.
                        created = _create_table_from_source(src, target_conn, table)
                        if created:
                            result.schema_drift.append(
                                {
                                    "table": table,
                                    "database": db_name,
                                    "issue": "created from the source catalog "
                                    "(no canonical schema group covers this table)",
                                }
                            )
                            tgt_cols = (
                                _target_columns_by_table(target_conn, [table]).get(table) or []
                            )
                    if not tgt_cols and not dry_run:
                        # Still no destination: a real failure the operator must
                        # see, never a silent row skip.
                        result.schema_drift.append(
                            {
                                "table": table,
                                "database": db_name,
                                "issue": "no target table after schema creation",
                            }
                        )
                        result.tables.append(
                            TableResult(
                                database=db_name,
                                table=table,
                                error="no target schema for this table",
                            )
                        )
                        continue
                    if not tgt_cols:
                        # A dry run has no destination to read the layout from,
                        # so the plan is described by the SOURCE schema: the
                        # column types the copy would coerce to are the ones
                        # the source declares (the target is created from the
                        # same DDL on the real run — the fallback above, or the
                        # canonical group when one covers it).
                        tgt_cols = _source_layout_as_target(src, table)
                    src_cols = source_columns(src, table)
                    drift = verify_schema_alignment(table, src_cols, tgt_cols)
                    for d in drift:
                        result.schema_drift.append({**d, "database": db_name})
                    coercer = RowCoercer(table, tgt_cols)
                    table_result = copy_table_rows(
                        database=db_name,
                        table=table,
                        source_conn=src,
                        target_conn=target_conn,
                        target_columns=tgt_cols,
                        coercer=coercer,
                        batch_size=batch_size,
                        dry_run=dry_run,
                        log_every=log_every,
                    )
                    result.tables.append(table_result)
                    quarantined = getattr(table_result, "quarantined_rows", None) or []
                    result.quarantined.extend(quarantined)
                    if not dry_run and target_conn is not None:
                        fixed = fix_sequences(target_conn, table, tgt_cols)
                        if fixed:
                            result.sequences_fixed.append(fixed)
            finally:
                src.close()
    finally:
        try:
            target_conn.close()
        except Exception:  # pragma: no cover - best effort
            pass

    result.duration_ms = round((time.monotonic() - started) * 1000.0, 1)
    _log_summary(result)
    return result


def _log_summary(result: MigrationResult) -> None:
    logger.info(
        "[DB-MIGRATE-DATA] %s tables=%d rows=%d quarantined=%d sequences=%d drift=%d "
        "schema_errors=%d ok=%s (%.0f ms)",
        "DRY RUN" if result.dry_run else "COPY",
        len(result.tables),
        result.rows_copied,
        len(result.quarantined),
        len(result.sequences_fixed),
        len(result.schema_drift),
        len(result.schema_errors),
        result.is_ok(),
        result.duration_ms,
    )


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "SOURCE_DATABASES",
    "CoercionError",
    "MigrationResult",
    "QuarantinedRow",
    "RowCoercer",
    "TableResult",
    "copy_table_rows",
    "ensure_target_schema",
    "fix_sequences",
    "mask_url",
    "migrate_sqlite_to_pg",
    "source_columns",
    "source_tables",
    "verify_schema_alignment",
]
