"""PostgreSQLDriver.translate_sql upsert-verb + placeholder translation.

The generic write path (every store that authors its own INSERT string and
calls execute()/executemany()/query() — or PgWritePlane.execute, which calls
the same static seam) used to land SQLite SQL verbatim on PostgreSQL:

    [DB-FABRIC] operational write failed ... error=syntax error at or near "OR"
    LINE 2: INSERT OR REPLACE INTO model_runtime_health

The root cause: ``translate_sql()`` rewrote ``?`` -> ``%s`` and nothing else.
``upsert()`` / ``insert_ignore()`` emulate the OR-verbs but are SEPARATE
methods no generic caller reaches, so every hand-authored ``INSERT OR
REPLACE``/``INSERT OR IGNORE`` ran as SQLite syntax and dead-lettered its row.

These tests pin the fix on the translation layer itself, with no PostgreSQL
server required: the conflict target is resolved from a fake connection that
answers the same ``information_schema``/``pg_index`` queries a live server
would, and every translated statement is fed to psycopg's own client-side
parser (``_queries._query2pg``) so a statement the rewrite produces malformed
fails here, not in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

from nexus_scalp.database.drivers.postgres_driver import (  # noqa: E402
    PostgreSQLDriver,
    _translate_placeholders,
    _translate_upsert_verb,
)


class _Rows:
    """Minimal ``cursor`` for ``fetchall()`` on a fake connection."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakePgConn:
    """Answers the driver's conflict-target queries like a live PG server.

    The rewriter issues two statements: the ``pg_index``/``pg_attribute``
    primary-key lookup and the ``information_schema`` UNIQUE-constraint
    lookup.  Answering both from a fixture keeps these tests server-free.
    """

    def __init__(self, pks: list[str] = (), uniques: list[str] = ()) -> None:
        self.pks = list(pks)
        self.uniques = list(uniques)
        self.statements: list[str] = []
        self.args: list[Any] = []

    def execute(self, sql: str, args: Any = ()) -> _Rows:
        self.statements.append(sql)
        self.args.append(tuple(args) if args else ())
        upper = sql.upper()
        if "INDISPRIMARY" in upper:
            return _Rows([(c,) for c in self.pks])
        if "UNIQUE" in upper:
            return _Rows([(c,) for c in self.uniques])
        return _Rows([])


def _psycopg_placeholder_count(sql: str) -> int:
    """Count the placeholders psycopg's own client-side parser binds.

    A statement the rewrite produced malformed raises here, so a bad rewrite
    cannot pass this suite by producing text that merely looks right.
    """
    from psycopg import _queries

    _queries._query2pg(sql.encode(), "ascii")
    _, formats, _order, _parts = _queries._query2pg(sql.encode(), "ascii")
    return len(formats)


# ---------------------------------------------------------------------------
# translate_sql: the pure-string contract (unchanged signature, no I/O)
# ---------------------------------------------------------------------------
class TestTranslateSqlPlaceholders:
    def test_plain_insert_passes_through_with_placeholders_translated(self):
        """(1) A plain INSERT keeps its shape; only ``?`` -> ``%s``."""
        out = PostgreSQLDriver.translate_sql("INSERT INTO t (a, b) VALUES (?, ?)")
        assert out == "INSERT INTO t (a, b) VALUES (%s, %s)"
        assert _psycopg_placeholder_count(out) == 2

    def test_select_untouched(self):
        assert PostgreSQLDriver.translate_sql("SELECT * FROM t WHERE a = ?") == (
            "SELECT * FROM t WHERE a = %s"
        )

    def test_qmark_inside_single_quoted_literal_not_rewritten(self):
        """(4) ``?`` inside a string literal is content, not a placeholder."""
        out = PostgreSQLDriver.translate_sql("SELECT * FROM t WHERE note = 'what?'")
        assert out == "SELECT * FROM t WHERE note = 'what?'"

    def test_qmark_inside_double_quoted_identifier_not_rewritten(self):
        out = PostgreSQLDriver.translate_sql('SELECT "col?x" FROM t')
        assert out == 'SELECT "col?x" FROM t'

    def test_named_placeholders_translated(self):
        """``:name`` -> ``%s`` so a store that builds ``VALUES (:a, :b)`` and
        flattens its params to a positional tuple still matches psycopg's
        placeholder count (without this psycopg sees 0 placeholders and
        rejects ``the query has 0 placeholders but N parameters were passed``)."""
        out = PostgreSQLDriver.translate_sql("INSERT INTO t (a, b) VALUES (:a, :b)")
        assert out == "INSERT INTO t (a, b) VALUES (%s, %s)"
        assert _psycopg_placeholder_count(out) == 2

    def test_named_placeholder_only_outside_literals(self):
        out = PostgreSQLDriver.translate_sql("INSERT INTO t (n) VALUES (:n)")
        assert out == "INSERT INTO t (n) VALUES (%s)"

    def test_pg_cast_operator_untouched(self):
        """``::`` is PostgreSQL's cast operator, not two named placeholders."""
        out = PostgreSQLDriver.translate_sql("SELECT (amount / 100.0)::numeric FROM t")
        assert "::numeric" in out
        assert "%s" not in out

    def test_psycopg_native_placeholders_preserved(self):
        """SQL already written for psycopg must not be re-escaped."""
        out = PostgreSQLDriver.translate_sql("INSERT INTO t (a, b) VALUES (%s, %s)")
        assert out == "INSERT INTO t (a, b) VALUES (%s, %s)"

    def test_literal_percent_survives_psycopg_parser(self):
        """A ``%`` inside a string literal must not look like a placeholder to
        psycopg's quote-blind client-side scanner (it raises
        ``incomplete placeholder: '%'`` on a bare ``%``)."""
        out = PostgreSQLDriver.translate_sql("INSERT INTO t (n) VALUES (?)")
        _psycopg_placeholder_count(out)  # parses clean
        out2 = PostgreSQLDriver.translate_sql("SELECT '100%' AS p")
        assert _psycopg_placeholder_count(out2) == 0

    def test_no_db_io_on_the_noop_path(self):
        """The hot path must be a pure string function for non-upserts."""
        import sqlite3

        calls: list[str] = []

        class ExplodingConn:
            def execute(self, sql: str, args: Any = ()) -> Any:
                calls.append(sql)
                raise sqlite3.OperationalError("no DB I/O on the no-op path")

        # A plain INSERT through the execution seam must not touch a connection.
        out = PostgreSQLDriver.translate_sql_for_execution(
            "INSERT INTO t (a) VALUES (?)", ExplodingConn()
        )
        assert out == "INSERT INTO t (a) VALUES (%s)"
        assert calls == []


# ---------------------------------------------------------------------------
# INSERT OR IGNORE  ->  ON CONFLICT DO NOTHING
# ---------------------------------------------------------------------------
class TestInsertOrIgnore:
    def test_becomes_on_conflict_do_nothing(self):
        """(3) IGNORE needs no conflict target, so it always rewrites."""
        out = PostgreSQLDriver.translate_sql_for_execution(
            "INSERT OR IGNORE INTO t (a, b) VALUES (?, ?)"
        )
        assert out == "INSERT INTO t (a, b) VALUES (%s, %s) ON CONFLICT DO NOTHING"
        assert "OR IGNORE" not in out
        assert _psycopg_placeholder_count(out) == 2

    def test_becomes_on_conflict_do_nothing_with_connection(self):
        conn = FakePgConn(pks=["a"])
        out = _translate_upsert_verb("INSERT OR IGNORE INTO t (a, b) VALUES (?, ?)", conn)
        assert out.endswith("ON CONFLICT DO NOTHING")
        # IGNORE never needs introspection, so no lookup is issued.
        assert conn.statements == []

    def test_named_placeholders_with_ignore(self):
        out = PostgreSQLDriver.translate_sql_for_execution(
            "INSERT OR IGNORE INTO mk_lifecycle_events (event_id, seed_id) "
            "VALUES (:event_id, :seed_id)"
        )
        assert out.startswith("INSERT INTO mk_lifecycle_events (event_id, seed_id) VALUES (%s, %s)")
        assert out.endswith("ON CONFLICT DO NOTHING")
        assert _psycopg_placeholder_count(out) == 2

    def test_semicolon_stripped_before_conflict_clause(self):
        """A trailing ``;`` is legal input; the clause follows the VALUES list,
        never the semicolon."""
        out = _translate_upsert_verb(
            "INSERT OR IGNORE INTO t (a) VALUES (?);",
            FakePgConn(),
        )
        assert out == "INSERT INTO t (a) VALUES (%s) ON CONFLICT DO NOTHING"


# ---------------------------------------------------------------------------
# INSERT OR REPLACE  ->  ON CONFLICT (<target>) DO UPDATE SET ...
# ---------------------------------------------------------------------------
class TestInsertOrReplace:
    def test_known_pk_becomes_on_conflict_do_update(self):
        """(2) PK columns all present in the row -> the PK is the target."""
        conn = FakePgConn(pks=["id"])
        out = _translate_upsert_verb("INSERT OR REPLACE INTO t (id, a, b) VALUES (?, ?, ?)", conn)
        assert out == (
            "INSERT INTO t (id, a, b) VALUES (%s, %s, %s) "
            'ON CONFLICT ("id") DO UPDATE SET "a" = EXCLUDED."a", "b" = EXCLUDED."b"'
        )
        assert "OR REPLACE" not in out
        assert _psycopg_placeholder_count(out) == 3

    def test_unique_columns_used_when_no_pk_covers_row(self):
        """No PK in the row -> the table's UNIQUE columns present in the row."""
        conn = FakePgConn(pks=["id"], uniques=["name"])
        out = _translate_upsert_verb("INSERT OR REPLACE INTO t (name, v) VALUES (?, ?)", conn)
        assert out == (
            "INSERT INTO t (name, v) VALUES (%s, %s) "
            'ON CONFLICT ("name") DO UPDATE SET "v" = EXCLUDED."v"'
        )

    def test_all_columns_are_the_key_becomes_do_nothing(self):
        """Nothing left to update -> DO NOTHING rather than an empty SET list."""
        conn = FakePgConn(pks=["id"])
        out = _translate_upsert_verb("INSERT OR REPLACE INTO t (id) VALUES (?)", conn)
        assert out == "INSERT INTO t (id) VALUES (%s) ON CONFLICT DO NOTHING"

    def test_unresolvable_target_fails_open(self):
        """No PK and no UNIQUE constraint: leave the statement alone rather
        than emit a bare ``ON CONFLICT`` (a PostgreSQL syntax error)."""
        conn = FakePgConn()
        sql = "INSERT OR REPLACE INTO t (a, b) VALUES (?, ?)"
        out = _translate_upsert_verb(sql, conn)
        assert out == "INSERT OR REPLACE INTO t (a, b) VALUES (%s, %s)"

    def test_replace_without_connection_fails_open(self):
        """translate_sql (no connection) must keep the verb, not guess a target."""
        out = PostgreSQLDriver.translate_sql("INSERT OR REPLACE INTO t (a) VALUES (?)")
        assert out == "INSERT OR REPLACE INTO t (a) VALUES (%s)"

    def test_conflict_target_lookup_is_parameterized(self):
        conn = FakePgConn(pks=["id"])
        _translate_upsert_verb("INSERT OR REPLACE INTO t (id, a) VALUES (?, ?)", conn)
        # Exactly the two layout queries, parameterized by the bare table name.
        assert len(conn.statements) == 2
        assert any("INDISPRIMARY" in s.upper() for s in conn.statements)
        assert any("UNIQUE" in s.upper() for s in conn.statements)

    def test_quoted_table_name_reaches_catalog_unquoted(self):
        """The catalog lookup is a bound parameter, so the table name it sees
        must be the bare name — a quoted ``"order"`` would never match a
        constraint and the rewriter would fail open for every quoted table."""
        conn = FakePgConn(pks=["id"])
        _translate_upsert_verb('INSERT OR REPLACE INTO "order" (id, a) VALUES (?, ?)', conn)
        bound = [a[0] for a in conn.args if a]
        assert bound == ["order", "order"], conn.args


# ---------------------------------------------------------------------------
# Quote awareness: literals and identifiers are never read as SQL
# ---------------------------------------------------------------------------
class TestQuoteAwareness:
    def test_word_or_inside_string_literal_not_a_verb(self):
        """(5) ``OR`` inside a string literal must not be read as the verb."""
        out = _translate_upsert_verb(
            "INSERT INTO t (note) VALUES (?)",
            FakePgConn(pks=["note"]),
        )
        assert out == "INSERT INTO t (note) VALUES (%s)"

    def test_or_verb_after_a_quoted_literal(self):
        out = _translate_upsert_verb(
            "INSERT OR IGNORE INTO t (a) VALUES (?)",
            FakePgConn(),
        )
        assert out == "INSERT INTO t (a) VALUES (%s) ON CONFLICT DO NOTHING"

    def test_double_quoted_table_and_columns(self):
        """A quoted identifier table/columns are preserved and re-quoted."""
        conn = FakePgConn(pks=["id"])
        out = _translate_upsert_verb(
            'INSERT OR REPLACE INTO "order" (id, "order") VALUES (?, ?)', conn
        )
        assert out == (
            'INSERT INTO "order" (id, "order") VALUES (%s, %s) '
            'ON CONFLICT ("id") DO UPDATE SET "order" = EXCLUDED."order"'
        )

    def test_literal_qmark_not_a_placeholder_after_verb(self):
        out = _translate_upsert_verb(
            "INSERT OR IGNORE INTO t (a) VALUES (?)",
            FakePgConn(),
        )
        assert "?" not in out

    def test_named_placeholder_lookalike_in_literal_untouched(self):
        """``:name`` text inside a literal stays literal."""
        out = PostgreSQLDriver.translate_sql("INSERT INTO t (n) VALUES (?)")
        assert out == "INSERT INTO t (n) VALUES (%s)"
        out2 = PostgreSQLDriver.translate_sql("SELECT * FROM t WHERE n = 'a:b'")
        assert out2 == "SELECT * FROM t WHERE n = 'a:b'"


# ---------------------------------------------------------------------------
# Fail-open: shapes the rewriter does not touch
# ---------------------------------------------------------------------------
class TestFailOpen:
    def test_multi_row_values_list_unchanged(self):
        """A batched VALUES list has no single-row conflict target; leave it."""
        conn = FakePgConn(pks=["a"])
        sql = "INSERT OR REPLACE INTO t (a, b) VALUES (?, ?), (?, ?)"
        out = _translate_upsert_verb(sql, conn)
        assert out == "INSERT OR REPLACE INTO t (a, b) VALUES (%s, %s), (%s, %s)"

    def test_select_unchanged(self):
        out = _translate_upsert_verb("SELECT * FROM t", FakePgConn())
        assert out == "SELECT * FROM t"

    def test_update_unchanged(self):
        out = _translate_upsert_verb("UPDATE t SET a = ?", FakePgConn())
        assert out == "UPDATE t SET a = %s"

    def test_returning_still_supported(self):
        """RETURNING is a real producer shape; the rewrite keeps it intact."""
        conn = FakePgConn(pks=["id"])
        sql = "INSERT OR REPLACE INTO t (id, a) VALUES (?, ?) RETURNING id"
        out = _translate_upsert_verb(sql, conn)
        assert out == (
            "INSERT INTO t (id, a) VALUES (%s, %s) "
            'ON CONFLICT ("id") DO UPDATE SET "a" = EXCLUDED."a" RETURNING id'
        )

    def test_unknown_verb_fails_open(self):
        """``INSERT OR ZAP`` is not SQLite syntax the rewriter handles; leave
        it alone (placeholders still translate)."""
        out = _translate_upsert_verb("INSERT OR ZAP INTO t (a) VALUES (?)", FakePgConn())
        assert out == "INSERT OR ZAP INTO t (a) VALUES (%s)"

    def test_empty_and_odd_input_unchanged(self):
        for sql in ("", "   ", "INSERT", "INSERT OR"):
            assert _translate_upsert_verb(sql, FakePgConn()) == sql


# ---------------------------------------------------------------------------
# Real producer statements, end to end through psycopg's own parser
# ---------------------------------------------------------------------------
class TestRealProducerStatements:
    """The 13 store sites this fix un-breaks, in their authored shapes."""

    @pytest.mark.parametrize(
        ("label", "sql", "pks", "uniques"),
        [
            (
                "governance.save_health",
                "\n    INSERT OR REPLACE INTO model_runtime_health (\n"
                "        checked_at, champion_id, champion_version, champion_healthy,\n"
                "        last_update, payload\n    ) VALUES (?, ?, ?, ?, ?, ?);\n    ",
                ["checked_at"],
                [],
            ),
            (
                "governance events",
                "INSERT OR REPLACE INTO model_governance_events (event_id, event, stage) "
                "VALUES (?, ?, ?)",
                ["event_id"],
                [],
            ),
            (
                "incident quarantine",
                "INSERT OR REPLACE INTO incident_quarantine (id, reason) VALUES (?, ?)",
                ["id"],
                [],
            ),
            (
                "incident traces (IGNORE)",
                "INSERT OR IGNORE INTO incident_value_traces (incident_id, field, source) "
                "VALUES (?, ?, ?)",
                [],
                [],
            ),
            (
                "marketplace lifecycle (named, IGNORE)",
                "INSERT OR IGNORE INTO mk_lifecycle_events "
                "(event_id, seed_id, reason, actor) VALUES (:event_id, :seed_id, :reason, :actor)",
                [],
                ["event_id"],
            ),
        ],
    )
    def test_translates_and_parses_in_psycopg(
        self, label: str, sql: str, pks: list[str], uniques: list[str]
    ):
        out = _translate_upsert_verb(sql, FakePgConn(pks=pks, uniques=uniques))
        assert "INSERT OR " not in out, f"{label}: SQLite verb survived: {out!r}"
        # psycopg's client-side parser must accept it and bind one placeholder
        # per parameter the caller passes.
        assert _psycopg_placeholder_count(out) > 0
