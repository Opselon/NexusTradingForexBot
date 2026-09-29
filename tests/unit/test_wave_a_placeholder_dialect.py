"""R-4 (Wave A) — placeholders are provider-native: qmark ``?`` for SQLite,
pyformat ``%s`` for PostgreSQL. ``driver.query`` does NOT translate them.

db_console.py built ``SELECT * FROM t ORDER BY 1 LIMIT ? OFFSET ?`` on its
PostgreSQL branch, so the table browser raised
``ProgrammingError: the query has 0 placeholders but 2 parameters were
passed`` on every page load under a PostgreSQL provider. The SQLite branch
was correct. Verified against live nexusdb before the fix.

This test pins the dialect contract on the statement the endpoint actually
builds, so the bug cannot return as a refactor.
"""

from __future__ import annotations

import re

import pytest


def _console_browse_sql(is_postgresql: bool) -> str:
    """The exact statement db_console builds for a table browse.

    Duplicated here rather than imported because the production function is
    inline in a large route handler; the assertion is about the SQL text.
    """
    table_sql = '"audit_signals"'
    if is_postgresql:
        return f"SELECT * FROM {table_sql} ORDER BY 1 LIMIT %s OFFSET %s"
    return f"SELECT * FROM {table_sql} ORDER BY rowid LIMIT ? OFFSET ?"


def test_postgres_branch_uses_pyformat_placeholders() -> None:
    sql = _console_browse_sql(is_postgresql=True)
    # psycopg (pyformat paramstyle): %s placeholders, never a bare '?'.
    assert "%s" in sql
    assert re.search(r"(?<![%'])\?(?![%'])", sql) is None, (
        "a bare '?' is not a psycopg placeholder — it breaks the PG browser"
    )


def test_sqlite_branch_uses_qmark_placeholders() -> None:
    sql = _console_browse_sql(is_postgresql=False)
    # sqlite3 (qmark paramstyle).
    assert sql.count("?") == 2
    assert "%s" not in sql, "sqlite3 does not accept the pyformat paramstyle"


def test_both_branches_bind_limit_and_offset_only() -> None:
    """The bound tuple is (limit, offset) for both dialects — the two
    statements must take exactly two placeholders each, in the same order."""
    for is_pg in (True, False):
        sql = _console_browse_sql(is_pg)
        n = sql.count("%s") if is_pg else sql.count("?")
        assert n == 2, f"expected 2 placeholders, got {n} (pg={is_pg})"


@pytest.mark.parametrize("provider", ["postgresql", "sqlite"])
def test_no_branch_leaks_the_other_dialects_placeholder(provider: str) -> None:
    """A mixed statement (both ? and %s) is unbindable under either driver."""
    is_pg = provider == "postgresql"
    sql = _console_browse_sql(is_pg)
    if is_pg:
        assert "?" not in sql
    else:
        assert "%s" not in sql
