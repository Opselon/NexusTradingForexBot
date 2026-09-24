"""Shared SQL boundary guard for the persistence drivers (defense-in-depth).

The drivers are THE parameterization boundary of the persistence layer:
callers pass provider-native placeholders and bind values via ``args``.
Identifier interpolation is funneled through ``quote_ident`` allow-lists.
This guard adds a final shape check at the execute sinks so that even a
caller that bypasses both layers cannot smuggle stacked statements or
block-comment tricks into the server.

CodeQL disposition for py/sql-injection #108 / #110: the flagged sinks are
these driver ``execute`` wrappers themselves — the parameterization layer.
User-controlled SQL exists only in the auth-gated, read-only db console
(``db_console.console_query``), which enforces a SELECT/EXPLAIN/WITH/
PRAGMA/VALUES allow-list, single-statement rule and banned-keyword list
BEFORE reaching here. The alerts are dismissed as the intentional
boundary; this guard documents and enforces that decision at runtime.
"""

from __future__ import annotations

import re

#: Statement verbs a driver may execute on behalf of callers: the read paths
#: plus the DML/DDL the internal store layer legitimately issues (built from
#: allow-listed identifiers, never raw user text).
_ALLOWED_VERBS = re.compile(
    r"^\s*(SELECT|EXPLAIN|WITH|PRAGMA|VALUES|INSERT|UPDATE|DELETE|REPLACE"
    r"|CREATE|ALTER|DROP|BEGIN|COMMIT|END|ANALYZE|REINDEX)\b",
    re.IGNORECASE,
)

#: Stacked statements are never legitimate in driver input; a caller needing
#: multiple statements issues them one by one. Block comments are banned;
#: line comments (--) are permitted (schema DDL documents itself) because a
#: line comment cannot hide a second statement once ``;`` is rejected.
_FORBIDDEN = re.compile(r"(/\*|\*/)")
_INTERIOR_SEMICOLON = re.compile(r";\s*\S")


def assert_safe_sql(sql: str) -> str:
    """Validate statement shape at the driver sink and return the statement.

    Raises ValueError on block comments, stacked statements, or an
    unrecognized leading verb — converting a silent injection primitive into
    a loud driver-level contract failure.

    Returns the statement unchanged once it passes the shape checks. This is a
    runtime integrity check, NOT a taint sanitizer: CodeQL's py/sql-injection
    query recognizes no regex/whitelist function as a sanitizer-barrier, so
    rewriting the text here cannot cut the static taint chain (and any rewrite
    risks changing the statement the engine receives). Dispositions for this
    driver boundary are documented at the call sites.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("empty or non-string SQL rejected by driver guard")
    if _FORBIDDEN.search(sql):
        raise ValueError("SQL contains block comments (driver guard)")
    # a single trailing ";" is allowed; any interior ";" (statement stacking)
    # is rejected.
    if _INTERIOR_SEMICOLON.search(sql):
        raise ValueError("SQL contains stacked statements (driver guard)")
    if not _ALLOWED_VERBS.match(sql):
        raise ValueError("SQL verb not allowed at driver boundary (driver guard)")
    return sql


def _assert_single_statement(sql: str) -> str:
    """Shape-only check used where the provider's own authority decides verbs.

    The read-only path relies on SQLite's C-level authorizer to reject every
    non-read statement, so the verb allow-list must not run there.  Block
    comments and statement stacking are still rejected: those are shape
    defects, not verb questions.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("empty or non-string SQL rejected by driver guard")
    if _FORBIDDEN.search(sql):
        raise ValueError("SQL contains block comments (driver guard)")
    if _INTERIOR_SEMICOLON.search(sql):
        raise ValueError("SQL contains stacked statements (driver guard)")
    return sql
