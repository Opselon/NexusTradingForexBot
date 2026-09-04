"""Shared SQL boundary guard for the persistence drivers (defense-in-depth).

The drivers are THE parameterization boundary of the persistence layer:
callers pass provider-native placeholders and bind values via ``args``.
Identifier interpolation is funneled through ``quote_ident`` allow-lists.
This guard adds a final shape check at the execute sink so that even a
caller that bypasses both layers cannot smuggle stacked statements or
comment tricks into the server.

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

#: Statement verbs a driver may execute on behalf of callers. DDL/DML from
#: the application flows through dedicated driver methods (create_table,
#: insert_row, ...) that build their SQL from allow-listed identifiers; the
#: free-form path is read-only by contract.
_ALLOWED_VERBS = re.compile(
    r"^\s*(SELECT|EXPLAIN|WITH|PRAGMA|VALUES|INSERT|UPDATE|DELETE|REPLACE)\b",
    re.IGNORECASE,
)

#: Comment markers and stacked statements are never legitimate in driver
#: input; a caller needing multiple statements issues them one by one.
_FORBIDDEN = re.compile(r"(--|/\*|\*/|;)")


def assert_safe_sql(sql: str) -> str:
    """Validates statement shape at the driver sink and returns it unchanged.

    Raises ValueError on comments, stacked statements, or an unrecognized
    leading verb — converting a silent injection primitive into a loud
    driver-level contract failure.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("empty or non-string SQL rejected by driver guard")
    if _FORBIDDEN.search(sql):
        raise ValueError("SQL contains comments or stacked statements (driver guard)")
    if not _ALLOWED_VERBS.match(sql):
        raise ValueError("SQL verb not allowed at driver boundary (driver guard)")
    return sql
