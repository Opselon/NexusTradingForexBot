"""Reconstruct the complete audit-domain logical schema by replay (CHG-0067).

WHY A REPLAY, AND NOT A SOURCE SCAN
===================================
The pre-CHG-0067 extractor read ``AuditRepository``'s source and kept only the
triple-quoted ``conn.execute`` CREATE literals. Anything authored anywhere else
was silently dropped, so PostgreSQL boot provisioned 35 tables + 2 indexes out
of the real 52 + 49:

* 47 of the 49 indexes — those issued through helpers (``_ensure_index``),
  through ``for ... in (...)`` tuples, through sibling bootstrap modules, or
  through the migration registry, never reach the provisioner;
* the 9 tables the audit migration registry creates (AUDIT-0005..0009);
* the engine's own ``schema_meta`` / ``schema_migrations`` tables — without
  which ``schema_version`` can never converge on the provider;
* 27 columns added at runtime by ``ALTER TABLE ADD COLUMN`` (present in every
  live schema, absent from every ``CREATE TABLE`` literal);
* the bootstrap tables owned by sibling modules (``audit_dead_letter``, the
  ``audit_broker_*`` family, ``audit_paper_executions``).

THE REPLAY
==========
A disposable in-memory SQLite connection records every DDL statement the real
bootstrap executes (a trace callback), then the ordered migration registry and
the engine meta tables are applied on top. What comes out is the schema a fresh
SQLite database would hold — tables, indexes (partial, DESC, unique), the
runtime ``ALTER`` history and the meta tables — in execution order and in the
original ``IF NOT EXISTS`` spelling, so the list stays idempotent when
PostgreSQL re-applies it.

Every captured statement is validated against the replayed schema before it is
returned (the application guards some DDL with ``contextlib.suppress``, so a
statement can be executed without landing), and the whole thing runs in
memory: no canonical database file is ever opened, let alone written.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

#: Statements that define a logical schema object. Everything else the replay
#: executes (PRAGMA, seed INSERTs, ANALYZE, SELECT guards) is not schema.
_DDL_STATEMENT = re.compile(r"(?is)^\s*(?:CREATE\s+(?:TABLE|UNIQUE\s+INDEX|INDEX)|ALTER\s+TABLE)\b")
_CREATE_TABLE = re.compile(r'(?is)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)("?)(\w+)\1')
_CREATE_INDEX = re.compile(
    r'(?is)^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)("?)(\w+)\1'
)
_ADD_COLUMN = re.compile(r'(?is)^\s*ALTER\s+TABLE\s+("?)(\w+)\1\s+ADD\s+COLUMN\s+("?)(\w+)\3')


def _open_replay() -> tuple[sqlite3.Connection, list[str]]:
    """A disposable in-memory connection plus the DDL statements it executes."""
    captured: list[str] = []
    conn = sqlite3.connect(":memory:")

    def _record(statement: str) -> None:
        sql = statement.strip()
        if _DDL_STATEMENT.match(sql):
            captured.append(sql)

    conn.set_trace_callback(_record)
    return conn, captured


def _apply_application_bootstrap(conn: sqlite3.Connection) -> None:
    """Run the DDL path an ``AuditRepository`` construction executes.

    ``object.__new__`` deliberately skips ``__init__``: construction starts a
    background write worker and touches the filesystem, neither of which a
    schema snapshot needs. The ``_create_*`` family only reads class attributes
    and the dead-letter store, so the half-built instance runs it unchanged —
    including the ``_add_column_if_missing`` history and the sibling bootstrap
    modules (broker history, paper executions, research/factory context heals,
    unique-constraint heal) it delegates to.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.adapters.database.dead_letter_store import DeadLetterStore

    host = object.__new__(AuditRepository)
    host.dead_letter_store = DeadLetterStore(
        conn_factory=sqlite3.connect,
        is_sqlite=True,
        db_path="",
        write_sink=None,
    )
    AuditRepository._create_sqlite_tables(host, conn)


def _apply_migration_registry(conn: sqlite3.Connection) -> None:
    """Apply the audit domain's ordered registry (AUDIT-0002..0009).

    This is the half of the schema that lives in code the bootstrap never
    calls: the governance/incident/release/archive tables, the research hot-path
    indexes and the ledger column upgrades. No registry helper reads ``db_path``
    (it is only the engine's reporting handle), so an empty path is honest here.
    """
    from nexus_scalp.database.models import DatabaseDomain
    from nexus_scalp.database.registry import migrations_for

    for migration in migrations_for(DatabaseDomain.AUDIT):
        migration.apply(conn, Path())


def _apply_engine_meta_tables(conn: sqlite3.Connection) -> None:
    """Create ``schema_meta`` / ``schema_migrations`` — the engine's own tables.

    The engine owns their DDL (it is what reads and writes them), so the
    statements are imported rather than copied: two spellings of the version
    chain would be a drift waiting to happen.
    """
    from nexus_scalp.database.engine import _HISTORY_TABLE_DDL, _META_TABLE_DDL

    conn.execute(_META_TABLE_DDL)
    conn.execute(_HISTORY_TABLE_DDL)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(row[1]).lower() for row in rows}


def _validated_statements(captured: list[str], conn: sqlite3.Connection) -> list[str]:
    """Keep the captured DDL that really exists in the replayed schema.

    A statement can be executed without landing — the application wraps some
    DDL in ``contextlib.suppress`` as expected control flow — and handing that
    to the provisioner would reproduce the very defect this module fixes.
    De-duplicated, execution order preserved (tables before their indexes,
    columns before the indexes over them), original ``IF NOT EXISTS`` spelling
    preserved so re-provisioning stays idempotent.
    """
    tables = {
        str(row[0]).lower()
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    indexes = {
        str(row[0]).lower()
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    columns: dict[str, set[str]] = {}
    statements: list[str] = []
    seen: set[str] = set()
    for sql in captured:
        key = " ".join(sql.lower().split())
        if key in seen:
            continue
        seen.add(key)
        present = False
        if (match := _CREATE_TABLE.match(sql)) is not None:
            present = match.group(2).lower() in tables
        elif (match := _CREATE_INDEX.match(sql)) is not None:
            present = match.group(2).lower() in indexes
        elif (match := _ADD_COLUMN.match(sql)) is not None:
            table = match.group(2).lower()
            if table in tables:
                cached = columns.get(table)
                if cached is None:
                    cached = columns.setdefault(table, _table_columns(conn, match.group(2)))
                present = match.group(4).lower() in cached
        if present:
            statements.append(sql)
        else:
            logger.warning(
                "[DB-SCHEMA] DDL did not land in the replayed schema, dropped: %s",
                " ".join(sql.split())[:120],
            )
    return statements


@lru_cache(maxsize=1)
def audit_schema_statements() -> tuple[str, ...]:
    """The complete audit-domain logical schema as SQLite DDL statements.

    Deterministic and free of any dependency on a database file: the replay is
    in-memory and discarded. Exceptions propagate on purpose — a provisioner
    that silently receives a partial schema is the defect this replaced.
    """
    conn, captured = _open_replay()
    try:
        _apply_application_bootstrap(conn)
        _apply_migration_registry(conn)
        _apply_engine_meta_tables(conn)
        statements = _validated_statements(captured, conn)
    finally:
        conn.close()
    logger.info("[DB-SCHEMA] audit logical schema replayed: %d statements", len(statements))
    return tuple(statements)
