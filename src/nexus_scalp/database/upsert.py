"""
Provider-split upsert SQL (DATABASE PORTABILITY, Lane B)
========================================================

The store modules own SQLite-shaped ``INSERT OR REPLACE`` statements. Under
PostgreSQL the pooled write backend executes the statement verbatim (only the
``?`` placeholders are rewritten to ``%s`` at the driver boundary), so
``INSERT OR REPLACE`` reaches the server and dies::

    ERROR: syntax error at or near "OR"
    LINE 2: INSERT OR REPLACE INTO model_governance_events (...)

This module builds the per-dialect statement pair, mirroring the split PR #480
landed for the incidents store:

  * SQLite keeps ``INSERT OR REPLACE`` byte-identical. SQLite is and remains a
    first-class provider — the statement is NOT deleted, simplified or routed
    through the PostgreSQL form.
  * PostgreSQL gets ``INSERT INTO ... ON CONFLICT (<cols>) DO UPDATE SET
    <col>=excluded.<col>``.

The ON CONFLICT target MUST name real constraint columns: an unconstrained
column makes the statement invalid under PostgreSQL (``there is no unique or
exclusion constraint matching the ON CONFLICT specification``).
:func:`upsert_columns` resolves the target from the table's registered DDL and
refuses to hand back a column set the DDL does not cover — the caller then adds
the constraint to BOTH DDL locations (domain DDL + module bootstrap DDL),
exactly as PR #480 did for ``incident_events`` / ``incident_value_traces``.

All values stay bound as ``?`` qmark placeholders (the driver translates them
at the boundary). Column names are validated through
:meth:`nexus_scalp.database.drivers.base.DatabaseDriver.quote_ident`.
"""

from __future__ import annotations

import re
from functools import lru_cache

from nexus_scalp.database.drivers.base import _IDENT_SHAPE

__all__ = ["UpsertKeyError", "build_upsert_sql", "upsert_columns"]


class UpsertKeyError(ValueError):
    """The table's DDL exposes no constraint covering the requested key."""


#: ON CONFLICT targets, per table. These are NOT guesses — each entry is
#: resolved and re-validated against the table's registered DDL by
#: :func:`upsert_columns`, which raises when the constraint is absent.
_UPSERT_KEYS: dict[str, tuple[str, ...]] = {
    "model_governance_events": ("event_id",),
    "model_governance_state": ("model_id", "model_version"),
    "model_shadow_comparisons": ("comparison_id",),
    "model_runtime_health": ("checked_at",),
    "model_promotion_audit": ("promotion_id",),
    "model_rollback_audit": ("rollback_id",),
    "shadow_runs": ("run_id",),
    "shadow_decisions": ("shadow_decision_id",),
    "shadow_comparisons": ("run_id",),
    "shadow_promotions": ("run_id",),
    "training_runs": ("run_id",),
    "model_comparisons": ("run_id",),
    "hygiene_run_history": ("run_id",),
}


def _quote(ident: str) -> str:
    """Quote one column identifier through the driver's whitelist.

    Mirrors :meth:`DatabaseDriver.quote_ident` without a driver instance (that
    method needs a constructed config the store layer does not have at module
    import time). The identifier characters are EXTRACTED by the allow-list
    regex rather than interpolated whole, so nothing outside the whitelist can
    reach a statement (SEC, py/sql-injection). Every identifier reaching here
    is a module-level literal, so this is validation, not untrusted input.
    """
    if not isinstance(ident, str) or _IDENT_SHAPE.fullmatch(ident) is None:
        raise ValueError(f"invalid SQL identifier: {ident!r}")
    return f'"{ident}"'


_TABLE_HEADER = re.compile(
    r"\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?([\w.]+)\"?\s*\(", re.I
)


@lru_cache(maxsize=256)
def _statement_table(statement: str) -> str | None:
    m = _TABLE_HEADER.match(statement)
    return m.group(1) if m else None


_TABLE_CONSTRAINT_START = re.compile(
    r"\s*(?:CONSTRAINT\s+\"?\w+\"?\s+)?(PRIMARY\s+KEY|UNIQUE)\s*\(([^)]*)\)", re.I
)


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on top-level commas only."""
    chunks: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            chunks.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        chunks.append("".join(cur))
    return chunks


def _constraint_columns(ddl: str) -> list[tuple[str, ...]]:
    """Every UNIQUE / PRIMARY KEY column tuple the DDL declares.

    Covers both spellings: a column-level ``UNIQUE`` / ``PRIMARY KEY`` on one
    column, and a table-level ``UNIQUE (a, b)`` / ``PRIMARY KEY (a, b)``.
    """
    start = ddl.find("(")
    if start == -1:
        return []
    depth = 0
    end = -1
    for k in range(start, len(ddl)):
        if ddl[k] == "(":
            depth += 1
        elif ddl[k] == ")":
            depth -= 1
            if depth == 0:
                end = k
                break
    if end == -1:
        return []
    body = ddl[start + 1 : end]
    out: list[tuple[str, ...]] = []
    for chunk in _split_top_level(body):
        text = chunk.strip().rstrip(",").strip()
        if not text:
            continue
        m = _TABLE_CONSTRAINT_START.match(text)
        if m:
            cols = tuple(c.strip().strip('"') for c in m.group(2).split(",") if c.strip())
            if cols:
                out.append(cols)
            continue
        name_m = re.match(r'\s*"?([\w]+)"?\s', text)
        if not name_m:
            continue
        name = name_m.group(1)
        rest = text[name_m.end(1) :].upper()
        # column-level constraint: INTEGER PRIMARY KEY, TEXT UNIQUE, ...
        if re.match(r"\s*(?:\w+\s+)?PRIMARY\s+KEY\b", rest) or re.match(
            r"\s*(?:\w+\s+)?UNIQUE\b", rest
        ):
            out.append((name,))
    return out


_CREATE_INDEX = re.compile(
    r"\s*CREATE\s+UNIQUE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?\w+\"?\s+ON\s+\"?([\w.]+)\"?\s*\(([^)]*)\)",
    re.I,
)


def _ddl_for(table: str) -> tuple[str, ...]:
    """Every registered statement that provisions ``table``.

    The domain DDL is the authority: it is what the fabric translates and
    provisions under PostgreSQL, and what the store's own ``ensure_schema``
    runs under SQLite (the two are kept identical). Returns the table's CREATE
    TABLE plus any of its CREATE [UNIQUE] INDEX statements — a UNIQUE INDEX is
    a legitimate ON CONFLICT target under both providers.
    """
    from nexus_scalp.hygiene.schema import ops_hygiene_schema_statements
    from nexus_scalp.model_lifecycle.schema import model_lifecycle_schema_statements
    from nexus_scalp.shadow.schema import ops_shadow_schema_statements

    out: list[str] = []
    for extract in (
        ops_shadow_schema_statements,
        ops_hygiene_schema_statements,
        model_lifecycle_schema_statements,
    ):
        for statement in extract():
            if _statement_table(statement) == table:
                out.append(statement)
                continue
            mi = _CREATE_INDEX.match(statement)
            if mi and mi.group(1) == table:
                out.append(statement)
    if not out:
        # The audit-domain tables (model_promotion_audit / model_rollback_audit)
        # are provisioned by the audit domain extractor, not a domain package.
        from nexus_scalp.database.migration.schema_snapshot import (
            audit_schema_statements,
        )

        for statement in audit_schema_statements():
            if _statement_table(statement) == table:
                out.append(statement)
                continue
            mi = _CREATE_INDEX.match(statement)
            if mi and mi.group(1) == table:
                out.append(statement)
    return tuple(out)


def upsert_columns(table: str) -> list[str]:
    """The ON CONFLICT target columns for ``table``, validated against the DDL.

    The columns come from :data:`_UPSERT_KEYS` and are then checked against the
    constraints the table's registered DDL actually declares. A key the DDL
    does not cover raises :class:`UpsertKeyError` — the fix is to add the
    constraint to BOTH DDL locations, never to weaken the check here.
    """
    key = _UPSERT_KEYS.get(table)
    if key is None:
        raise UpsertKeyError(f"upsert_columns: no registered ON CONFLICT key for table {table!r}")
    statements = _ddl_for(table)
    if not statements:
        raise UpsertKeyError(
            f"upsert_columns: no registered CREATE TABLE for {table!r} "
            "(the table must be provisioned by a domain schema extractor)"
        )
    constraints: list[tuple[str, ...]] = []
    for statement in statements:
        if _statement_table(statement) == table:
            constraints.extend(_constraint_columns(statement))
        else:
            mi = _CREATE_INDEX.match(statement)
            if mi:
                constraints.append(
                    tuple(c.strip().strip('"') for c in mi.group(2).split(",") if c.strip())
                )
    keyset = set(key)
    if not any(keyset.issubset(set(cols)) for cols in constraints):
        raise UpsertKeyError(
            f"upsert_columns: ON CONFLICT key {key} for {table!r} is not covered "
            f"by any UNIQUE/PRIMARY KEY constraint in the DDL "
            f"(found: {constraints or 'none'}). Add the constraint to BOTH DDL "
            "locations (domain DDL + module bootstrap DDL), as PR #480 did for "
            "incident_events/incident_value_traces."
        )
    return list(key)


def build_upsert_sql(
    table: str,
    columns: list[str],
    *,
    sqlite_sql: str,
) -> tuple[str, str]:
    """Build the provider-aware upsert statement pair for ``table``.

    Parameters
    ----------
    table:
        Target table. Used only to resolve and validate the real ON CONFLICT
        target columns from the table's DDL.
    columns:
        The column list of the statement being split, in insertion order. The
        SQLite statement's own column list is the authority — the PostgreSQL
        branch never reorders, renames or drops columns.
    """
    if not columns:
        raise ValueError("build_upsert_sql: empty column list")
    conflict_columns = upsert_columns(table)
    quoted_cols = ", ".join(_quote(c) for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    conflict = ", ".join(_quote(c) for c in conflict_columns)
    set_clause = ", ".join(
        f"{_quote(c)}=excluded.{_quote(c)}" for c in columns if c not in set(conflict_columns)
    )
    pg_sql = (
        f"INSERT INTO {_quote(table)} ({quoted_cols}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {set_clause}"
    )
    return sqlite_sql, pg_sql
