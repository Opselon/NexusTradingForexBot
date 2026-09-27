"""Lane A — PostgreSQL arity contract for EVERY audit write producer.

The dead-letter probe of the deployed cluster found 18 rows dead-lettered with
``the query has 0 placeholders but 32 parameters were passed``: a producer
whose argument count does not match its statement. The row only became visible
after travelling the queue, the batch, the rollback and the salvage pass.

This suite pins the contract at the source instead: for every audit producer,
the statement's placeholder count (counted AFTER the write plane's
``translate_sql`` normalisation and AFTER the driver's ``?`` -> ``%s``
translation) must equal ``len(args)``. A regression here is a producer defect,
and the arity guard now refuses it at enqueue with a named reason.

The parametrisation is built by reading the producer statements out of the
module source rather than hard-coding them, so a NEW producer that lands in
``audit_repository.py`` is covered the day it is added (the arity mismatch is
exactly the failure mode a copy/paste producer introduces).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.adapters.database.audit_write_plane import (  # noqa: E402
    _count_placeholders,
    assert_arity,
    translate_sql,
)
from nexus_scalp.database.drivers.postgres_driver import (  # noqa: E402
    _translate_placeholders,
)

AUDIT_REPO = REPO_ROOT / "src" / "nexus_scalp" / "adapters" / "database" / "audit_repository.py"


def _literal(node: ast.AST) -> str | None:
    """Resolve a string/JoinedStr AST node to its text, or None if not static."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            text = _literal(value)
            if text is None:
                return None
            parts.append(text)
        return "".join(parts)
    return None


def _extract_producers() -> list[tuple[str, str, int]]:
    """Every ``(producer, statement, arg count)`` triple in audit_repository.py.

    Pairs a ``query``/``sql`` string assignment with the ``args`` tuple that
    follows it inside the same function, and counts the tuple's elements.
    Statements that build their args outside a static tuple are skipped (the
    arg count is unknown statically) — none of the audit producers do.
    """
    tree = ast.parse(AUDIT_REPO.read_text(encoding="utf-8"))
    out: list[tuple[str, str, int]] = []
    for func in [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]:
        assigns = sorted(
            (n for n in func.body if isinstance(n, ast.Assign)), key=lambda n: n.lineno
        )
        pending: tuple[int, str] | None = None
        for assign in assigns:
            names = [t.id for t in assign.targets if isinstance(t, ast.Name)]
            if "query" in names or "sql" in names:
                text = _literal(assign.value)
                if isinstance(text, str) and "INSERT" in text.upper():
                    pending = (assign.lineno, text)
            elif "args" in names and pending is not None:
                if isinstance(assign.value, ast.Tuple):
                    out.append((func.name, pending[1], len(assign.value.elts)))
                pending = None
    return out


PRODUCERS = _extract_producers()
assert PRODUCERS, "no audit producers discovered — the AST extraction is broken"


@pytest.fixture(scope="module")
def translated() -> list[tuple[str, str, int, str]]:
    """Each producer after the write plane + driver translations."""
    rows = []
    for name, stmt, nargs in PRODUCERS:
        plane_sql = translate_sql(stmt)
        pg_sql = _translate_placeholders(plane_sql)
        rows.append((name, stmt, nargs, pg_sql))
    return rows


class TestAuditProducerArity:
    """``placeholder_count(translated_sql) == len(args)`` for every producer."""

    def test_producers_were_discovered(self) -> None:
        """The extraction must find the known audit producers, not nothing."""
        names = {name for name, _stmt, _n in PRODUCERS}
        # The hot-path producers the dead-letter probe named.
        for expected in (
            "_log_guard_telemetry",
            "log_signal",
            "log_order",
            "log_execution",
            "log_account_snapshot",
            "log_ledger_opened",
            "log_ledger_closed",
            "set_runtime_risk_state",
        ):
            assert expected in names, f"producer {expected} not discovered by the AST scan"

    def test_every_producer_arity_matches_after_translation(self, translated) -> None:
        """The contract both providers rely on, checked on the FINAL SQL text."""
        failures = []
        for name, stmt, nargs, pg_sql in translated:
            sqlite_placeholders = _count_placeholders(translate_sql(stmt))
            if sqlite_placeholders != nargs:
                failures.append(
                    f"{name}: SQLite-path {sqlite_placeholders} placeholder(s) vs {nargs} arg(s)"
                )
            # The driver's translation is a 1:1 ``?`` -> ``%s`` swap outside
            # literals, so the PG count must agree with the SQLite count.
            pg_placeholders = pg_sql.count("%s")
            if pg_placeholders != nargs:
                failures.append(
                    f"{name}: PostgreSQL-path {pg_placeholders} placeholder(s) vs {nargs} arg(s)"
                )
        assert not failures, "arity mismatch(es):\n" + "\n".join(failures)

    def test_no_producer_emits_a_bare_on_conflict(self, translated) -> None:
        """A bare ``ON CONFLICT`` with no target is a PostgreSQL syntax error.

        ``translate_sql`` must resolve a real conflict target (or fall back to
        ``ON CONFLICT DO NOTHING``) — never emit the targetless clause alone.
        """
        for name, _stmt, _nargs, pg_sql in translated:
            flat = " ".join(pg_sql.split())
            assert not re.search(r"ON CONFLICT\s+DO", flat, re.I), (
                f"{name}: bare ON CONFLICT would be rejected by PostgreSQL: {flat[:160]}"
            )

    def test_guard_is_survived_by_the_translation(self) -> None:
        """The guard itself never breaks a correct producer."""
        for _name, stmt, nargs in PRODUCERS:
            assert_arity(translate_sql(stmt), [None] * nargs)


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #
class TestArityGuard:
    def test_matching_arity_is_accepted(self) -> None:
        assert_arity("INSERT INTO t (a, b) VALUES (?, ?)", (1, 2))

    def test_too_many_args_is_refused(self) -> None:
        with pytest.raises(Exception, match="placeholder"):
            assert_arity("INSERT INTO t (a, b) VALUES (?, ?)", tuple(range(32)))

    def test_too_few_args_is_refused(self) -> None:
        with pytest.raises(Exception, match="placeholder"):
            assert_arity("INSERT INTO t (a, b, c) VALUES (?, ?, ?)", (1,))

    def test_zero_placeholders_zero_args_is_accepted(self) -> None:
        """The breaker-anchor seed: a literal statement with an empty arg tuple."""
        assert_arity("INSERT INTO t (id) VALUES (1)", ())

    def test_zero_placeholders_with_args_is_refused(self) -> None:
        """The live cluster failure: 0 placeholders, 32 parameters."""
        with pytest.raises(Exception, match="0 placeholder"):
            assert_arity("INSERT INTO t (id) VALUES (1)", tuple(range(32)))

    def test_literal_question_mark_is_not_a_placeholder(self) -> None:
        """A ``?`` inside a JSON/string literal must not be counted."""
        sql = "INSERT INTO t (payload) VALUES (?)"  # the arg supplies the '?'
        assert _count_placeholders(sql) == 1
        # A statement whose text contains a literal ? inside quotes
        literal_sql = "INSERT INTO t (note, x) VALUES ('what is this? a literal', ?)"
        assert _count_placeholders(literal_sql) == 1


# --------------------------------------------------------------------------- #
# translate_sql: the ON CONFLICT rewrite
# --------------------------------------------------------------------------- #
class TestTranslateSql:
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT OR IGNORE INTO t (a, b) VALUES (?, ?)",
            "INSERT OR IGNORE INTO t (a, b) VALUES (?, ?);",
            "INSERT OR REPLACE INTO t (a, b) VALUES (?, ?)",
            "INSERT OR IGNORE INTO t VALUES (1, 2)",
            'INSERT OR IGNORE INTO "quoted" (a) VALUES (?)',
        ],
    )
    def test_or_verb_is_removed(self, sql: str) -> None:
        """No ``INSERT OR`` survives the rewrite — it is SQLite dialect."""
        out = translate_sql(sql)
        assert "INSERT OR" not in out.upper().lstrip(), out

    def test_ignore_becomes_a_real_on_conflict(self) -> None:
        out = translate_sql(
            "INSERT OR IGNORE INTO audit_paper_executions (ts, ticket, order_type, requested_price, volume) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        assert "ON CONFLICT (ts, ticket, order_type, requested_price) DO NOTHING" in out

    def test_replace_becomes_a_real_upsert(self) -> None:
        out = translate_sql(
            "INSERT OR REPLACE INTO audit_paper_executions (ts, ticket, order_type, requested_price, volume) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        assert "ON CONFLICT (ts, ticket, order_type, requested_price) DO UPDATE SET" in out
        # The conflict target is not assigned to itself.
        assert "ts = excluded.ts" not in out
        assert "volume = excluded.volume" in out

    def test_replace_with_only_target_columns_becomes_do_nothing(self) -> None:
        out = translate_sql(
            "INSERT OR REPLACE INTO audit_paper_executions (ts, ticket, order_type, requested_price) "
            "VALUES (?, ?, ?, ?)"
        )
        assert out.endswith("DO NOTHING")

    def test_unknown_table_falls_back_to_do_nothing(self) -> None:
        """Unresolvable target keeps the write idempotent, never a bare clause."""
        out = translate_sql("INSERT OR REPLACE INTO unknown_table (a) VALUES (?)")
        assert out.endswith("ON CONFLICT DO NOTHING")

    def test_partial_target_coverage_falls_back(self) -> None:
        """PostgreSQL requires the target to match a constraint EXACTLY.

        A statement covering only part of a composite unique target must not
        emit a partial target (that raises ``ON CONFLICT clause does not match
        any PRIMARY KEY or UNIQUE constraint``).
        """
        out = translate_sql(
            "INSERT OR IGNORE INTO audit_paper_executions (ts, ticket, volume) VALUES (?, ?, ?)"
        )
        assert "ON CONFLICT (ts" not in out, out
        assert out.endswith("ON CONFLICT DO NOTHING")

    def test_portable_statements_are_untouched(self) -> None:
        """A producer that already wrote a standard upsert is passed through."""
        sql = "INSERT INTO audit_signals (a) VALUES (?) ON CONFLICT(signal_dedup_key) DO NOTHING"
        assert translate_sql(sql) == sql

    def test_non_insert_statements_are_untouched(self) -> None:
        assert translate_sql("UPDATE t SET a = 1") == "UPDATE t SET a = 1"

    def test_placeholders_are_preserved(self) -> None:
        """``?`` is the driver's job — the plane must not pre-translate."""
        out = translate_sql(
            "INSERT OR IGNORE INTO audit_paper_executions (ts, ticket) VALUES (?, ?)"
        )
        assert _count_placeholders(out) == 2
        assert "%s" not in out
