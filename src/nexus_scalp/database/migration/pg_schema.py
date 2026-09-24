"""SQLite -> PostgreSQL schema translation for the audit domain.

The domain's DDL is authored once (in AuditRepository, SQLite dialect) and
translated here so both providers keep an identical physical schema. This is a
deliberate 1:1 structural translation, NOT a redesign: column names, types,
constraints and index shapes match the SQLite originals so application SQL and
the SQLite reconciliation paths keep working byte-identically.

Translation rules
-----------------
* ``INTEGER PRIMARY KEY AUTOINCREMENT`` -> ``BIGINT GENERATED ALWAYS AS
  IDENTITY PRIMARY KEY`` (the PG parity contract from the guard suite).
* ``INTEGER``        -> ``BIGINT``  (SQLite integers are 64-bit)
* ``REAL``           -> ``DOUBLE PRECISION``
* ``TEXT``           -> ``TEXT``
* ``BLOB``           -> ``BYTEA``
* ``AUTOINCREMENT`` keyword is dropped (identity owns the sequence)
* ``CREATE VIRTUAL TABLE`` -> error (FTS has no PG equivalent here; the audit
  schema uses none)
* partial-index predicates (``WHERE col IS NOT NULL AND col != ''``) are kept
  verbatim — PG supports them — but the literal must be typed when it is a
  parameter-ambiguous string, so '' becomes ''::text.

Everything else passes through unchanged. Each statement is idempotent
(``IF NOT EXISTS``) so the migration is safe to re-run on an existing
NexusDB — that is what makes "switch provider" a non-destructive operation.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# --- type mapping (longest-first so INTEGER PRIMARY KEY wins over INTEGER) ----
_TYPE_MAP = (
    (
        re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.I),
        "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY",
    ),
    (re.compile(r"\bINTEGER\s+PRIMARY\s+KEY", re.I), "BIGINT PRIMARY KEY"),
    (re.compile(r"\bINTEGER\b", re.I), "BIGINT"),
    (re.compile(r"\bREAL\b", re.I), "DOUBLE PRECISION"),
    (re.compile(r"\bBLOB\b", re.I), "BYTEA"),
)


def translate_type(sql: str) -> str:
    """Apply the type substitutions to one statement."""
    out = sql
    for pattern, replacement in _TYPE_MAP:
        out = pattern.sub(replacement, out)
    # AUTOINCREMENT as a standalone keyword (outside the PRIMARY KEY phrase)
    out = re.sub(r"\bAUTOINCREMENT\b", "", out, flags=re.I)
    return out


def _type_partial_predicate(sql: str) -> str:
    """PG cannot infer the type of '' inside a partial index predicate."""
    return re.sub(r"!=\s*''(?!\s*::)", "!= ''::text", sql)


def translate_ddl(statement: str) -> str:
    """Translate one SQLite DDL statement to PostgreSQL dialect."""
    if re.match(r"(?i)^\s*CREATE\s+VIRTUAL\s+TABLE", statement):
        raise ValueError(
            "CREATE VIRTUAL TABLE has no PostgreSQL equivalent in the audit "
            "schema; refusing to emit a silently-wrong translation"
        )
    out = translate_type(statement)
    if re.search(r"(?i)^\s*CREATE\s+(UNIQUE\s+)?INDEX", out):
        out = _type_partial_predicate(out)
    # collapse the harmless double space the substitutions can leave
    out = re.sub(r"[ \t]+\n", "\n", out)
    return out.strip()


# --- execution ---------------------------------------------------------------


def apply_schema(
    statements: list[str], execute, *, stop_on_error: bool = False
) -> dict[str, object]:
    """Translate + apply a schema on a PostgreSQL connection.

    ``execute`` is any callable running one SQL string (a psycopg cursor or a
    thin wrapper). Returns an audit record of what happened — the migration is
    observable by design, never a silent best-effort.
    """
    applied: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    for raw in statements:
        try:
            translated = translate_ddl(raw)
        except ValueError as exc:
            errors.append(
                {"statement": raw.strip().splitlines()[0][:80], "error": f"translate: {exc}"}
            )
            if stop_on_error:
                break
            continue
        try:
            execute(translated)
            applied.append(translated.strip().splitlines()[0][:80])
        except Exception as exc:
            head = translated.strip().splitlines()[0][:80]
            # IF NOT EXISTS already covers the normal re-run; a residual error
            # is a genuine schema drift the operator must see.
            errors.append({"statement": head, "error": f"{type(exc).__name__}: {exc}"})
            logger.error("[DB-MIGRATE] statement failed: %s -> %s", head, exc)
            if stop_on_error:
                break
    result: dict[str, object] = {
        "applied": applied,
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "errors": errors,
    }
    logger.info(
        "[DB-MIGRATE] schema applied=%d errors=%d",
        len(applied),
        len(errors),
    )
    return result
