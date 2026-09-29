"""R-2 (Wave A) — LIMIT must be part of the SQL the pooled read plane
executes, not a Python slice applied afterwards.

Phase-2 evidence (P2-05/P2-06): ``web/api_v1/common.fetch_rows_bounded``
injected ``LIMIT ?`` on its SQLite branch but ran the bare statement on the
pooled (PostgreSQL) branch and sliced the list in Python. A route asking for
ONE row therefore fetched every row. Measured with EXPLAIN (ANALYZE, BUFFERS)
against live nexusdb, on the exact statement shape of the "latest signal"
route (``SELECT * FROM audit_signals ORDER BY id DESC``, limit 1):

  BEFORE (no LIMIT in SQL):
    Gather Merge  rows=8791  time=41.414 ms
    Buffers: shared hit=105 read=1231, temp read=818 written=819
  AFTER (LIMIT in SQL):
    Limit  rows=1  time=0.009 ms
    Buffers: shared hit=3
    -> Index Scan Backward using audit_signals_pkey (stops after 1 row)

The fix turns a full parallel table sort into a single index probe.
"""

from __future__ import annotations

from nexus_scalp.web.api_v1.common import _inject_limit


def test_injects_limit_on_a_plain_select() -> None:
    assert _inject_limit("SELECT * FROM t ORDER BY id DESC", 1) == (
        "SELECT * FROM t ORDER BY id DESC LIMIT 1"
    )


def test_limit_is_bounded_by_the_caller_cap() -> None:
    # fetch_rows_bounded clamps to 200*25 before calling; _inject_limit
    # trusts its argument. The contract is that the SQL carries the bound.
    assert _inject_limit("SELECT * FROM t", 5000).endswith("LIMIT 5000")


def test_does_not_double_append_when_limit_present() -> None:
    """A caller that already wrote a LIMIT must not get a second one — that
    would be a hard SQL error in production."""
    sql = "SELECT * FROM t LIMIT 10"
    assert _inject_limit(sql, 1) == sql


def test_detects_limit_in_lowercase_and_mixed_case() -> None:
    for variant in ("limit", "Limit", "LIMIT"):
        sql = f"SELECT * FROM t {variant} 10"
        assert _inject_limit(sql, 1) == sql, f"failed for {variant!r}"


def test_ignores_limit_inside_a_string_literal() -> None:
    """A WHERE clause mentioning the word LIMIT in a literal must not fool
    the guard into skipping a real injection."""
    sql = "SELECT * FROM t WHERE note = 'no LIMIT applied'"
    out = _inject_limit(sql, 5)
    assert out == "SELECT * FROM t WHERE note = 'no LIMIT applied' LIMIT 5"


def test_ignores_fetch_first() -> None:
    """FETCH FIRST is the SQL-standard spelling of LIMIT; appending both is
    a syntax error, so it is treated as already-bounded."""
    sql = "SELECT * FROM t ORDER BY id FETCH FIRST 10 ROWS ONLY"
    assert _inject_limit(sql, 1) == sql


def test_strips_a_trailing_semicolon() -> None:
    """A statement carrying a terminator must not become
    ``...; LIMIT 1`` which PostgreSQL rejects."""
    assert _inject_limit("SELECT * FROM t;", 1) == "SELECT * FROM t LIMIT 1"


def test_preserves_a_complex_statement() -> None:
    sql = (
        "SELECT a.id, b.name FROM audit_signals a JOIN other b ON b.id = a.ref "
        "WHERE a.kind = $1 AND a.ts > $2 ORDER BY a.id DESC"
    )
    out = _inject_limit(sql, 20)
    assert out == sql + " LIMIT 20"
    # The parameter placeholders survive untouched (the caller binds them).
    assert "$1" in out and "$2" in out
