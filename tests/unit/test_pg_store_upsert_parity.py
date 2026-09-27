"""Lane B — provider-split upsert parity for all 13 INSERT OR REPLACE sites.

Context (PR #480 pattern, applied to the governance/shadow/model-lifecycle/
hygiene stores): the SQLite branch keeps ``INSERT OR REPLACE`` byte-identical
(SQLite is a first-class provider); the PostgreSQL branch the pooled write
backend runs gets an explicit ``INSERT INTO ... ON CONFLICT (...) DO UPDATE
SET``. Without the split, the SQLite statement reaches the server verbatim
apart from the ``?``->``%s`` placeholder translation and dies with::

    ERROR: syntax error at or near "OR"
    LINE 2: INSERT OR REPLACE INTO model_governance_events (...

These tests pin both halves of the contract:

* the SQLite branch is the historical statement, unchanged;
* the PG branch is an ON CONFLICT upsert whose target names REAL constraint
  columns read from the DDL — never a hardcoded guess — and whose placeholder
  count matches the column count so the driver boundary sees one parameter per
  value (the incidents-store ``:name`` placeholder bug is NOT replicated here).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import pytest

from nexus_scalp.database.upsert import UpsertKeyError, build_upsert_sql, upsert_columns
from nexus_scalp.governance import store as governance_store
from nexus_scalp.hygiene import state as hygiene_state
from nexus_scalp.model_lifecycle import store as lifecycle_store
from nexus_scalp.shadow import store as shadow_store

#: The 13 production INSERT OR REPLACE sites and their (module, table, sqlite
#: statement, pg statement) quadruples. ``sites_converted`` in the lane report
#: is exactly this table list.
SITES: list[tuple[str, Any, str, str, str]] = [
    (
        "model_governance_events",
        governance_store,
        "_INSERT_EVENT_SQL",
        "_SQLITE_EVENT_SQL",
        "_PG_EVENT_SQL",
    ),
    (
        "model_governance_state",
        governance_store,
        "_UPSERT_STATE_SQL",
        "_SQLITE_STATE_SQL",
        "_PG_STATE_SQL",
    ),
    (
        "model_shadow_comparisons",
        governance_store,
        "_INSERT_COMPARISON_SQL",
        "_SQLITE_COMPARISON_SQL",
        "_PG_COMPARISON_SQL",
    ),
    (
        "model_runtime_health",
        governance_store,
        "_INSERT_HEALTH_SQL",
        "_SQLITE_HEALTH_SQL",
        "_PG_HEALTH_SQL",
    ),
    (
        "model_promotion_audit",
        governance_store,
        "_INSERT_PROMOTION_AUDIT_SQL",
        "_SQLITE_PROMOTION_AUDIT_SQL",
        "_PG_PROMOTION_AUDIT_SQL",
    ),
    (
        "model_rollback_audit",
        governance_store,
        "_INSERT_ROLLBACK_AUDIT_SQL",
        "_SQLITE_ROLLBACK_AUDIT_SQL",
        "_PG_ROLLBACK_AUDIT_SQL",
    ),
    (
        "shadow_runs",
        shadow_store,
        "_INSERT_RUN_SQL",
        "_SQLITE_RUN_SQL",
        "_PG_RUN_SQL",
    ),
    (
        "shadow_decisions",
        shadow_store,
        "_INSERT_DECISION_SQL",
        "_SQLITE_DECISION_SQL",
        "_PG_DECISION_SQL",
    ),
    (
        "shadow_comparisons",
        shadow_store,
        "_INSERT_COMPARISON_SQL",
        "_SQLITE_COMPARISON_SQL",
        "_PG_COMPARISON_SQL",
    ),
    (
        "shadow_promotions",
        shadow_store,
        "_INSERT_PROMOTION_SQL",
        "_SQLITE_PROMOTION_SQL",
        "_PG_PROMOTION_SQL",
    ),
    (
        "training_runs",
        lifecycle_store,
        "_INSERT_RUN_SQL",
        "_SQLITE_RUN_SQL",
        "_PG_RUN_SQL",
    ),
    (
        "model_comparisons",
        lifecycle_store,
        "_INSERT_COMPARISON_SQL",
        "_SQLITE_COMPARISON_SQL",
        "_PG_COMPARISON_SQL",
    ),
    (
        "hygiene_run_history",
        hygiene_state,
        None,  # the SQLite literal lived inline in record_run; now the constant
        "_SQLITE_RUN_HISTORY_SQL",
        "_PG_RUN_HISTORY_SQL",
    ),
]


@pytest.fixture(scope="module")
def ddl_constraints() -> dict[str, set[tuple[str, ...]]]:
    """The UNIQUE/PRIMARY KEY constraint tuples each table's DDL declares.

    Built by creating the registered schema in an in-memory SQLite database and
    reading ``PRAGMA index_list`` / ``PRAGMA table_info``: the constraints the
    ON CONFLICT clauses target must exist in the schema that actually gets
    provisioned, not in an assertion.
    """
    from nexus_scalp.database.migration.schema_snapshot import audit_schema_statements
    from nexus_scalp.hygiene.schema import ops_hygiene_schema_statements
    from nexus_scalp.model_lifecycle.schema import model_lifecycle_schema_statements
    from nexus_scalp.shadow.schema import ops_shadow_schema_statements

    conn = sqlite3.connect(":memory:")
    try:
        for extract in (
            ops_shadow_schema_statements,
            ops_hygiene_schema_statements,
            model_lifecycle_schema_statements,
            audit_schema_statements,
        ):
            for statement in extract():
                conn.execute(statement)
        out: dict[str, set[tuple[str, ...]]] = {}
        for table, _, _, _, _ in SITES:
            cols: set[tuple[str, ...]] = set()
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            cols.update(tuple([r[1]]) for r in info if r[5])  # rowid pk column
            for idx in conn.execute(f"PRAGMA index_list({table})").fetchall():
                if idx[2] == 1:  # unique index
                    names = tuple(
                        i[2] for i in conn.execute(f"PRAGMA index_info('{idx[1]}')").fetchall()
                    )
                    cols.add(names)
            out[table] = cols
        return out
    finally:
        conn.close()


@pytest.mark.parametrize(("table", "module", "_orig", "sqlite_attr", "pg_attr"), SITES)
def test_sqlite_branch_keeps_insert_or_replace(
    table: str, module: Any, _orig: Any, sqlite_attr: str, pg_attr: str
) -> None:
    """SQLite stays a first-class provider: INSERT OR REPLACE, untouched."""
    sqlite_sql = getattr(module, sqlite_attr)
    assert "INSERT OR REPLACE INTO" in sqlite_sql, sqlite_sql
    assert "ON CONFLICT" not in sqlite_sql
    # byte-identical to the historical statement where one existed
    if _orig is not None:
        assert getattr(module, sqlite_attr) == getattr(module, _orig)


@pytest.mark.parametrize(("table", "module", "_orig", "sqlite_attr", "pg_attr"), SITES)
def test_pg_branch_uses_on_conflict(
    table: str, module: Any, _orig: Any, sqlite_attr: str, pg_attr: str
) -> None:
    """The statement the pooled write backend runs under PostgreSQL."""
    pg_sql = getattr(module, pg_attr)
    assert "INSERT OR REPLACE" not in pg_sql, pg_sql
    assert pg_sql.startswith("INSERT INTO ")
    assert "ON CONFLICT" in pg_sql
    assert "DO UPDATE SET" in pg_sql
    # the SQLite-only idiom never leaks into the PG branch
    assert "OR REPLACE" not in pg_sql


@pytest.mark.parametrize(("table", "module", "_orig", "sqlite_attr", "pg_attr"), SITES)
def test_pg_branch_conflict_target_is_a_real_constraint(
    table: str,
    module: Any,
    _orig: Any,
    sqlite_attr: str,
    pg_attr: str,
    ddl_constraints: dict[str, set[tuple[str, ...]]],
) -> None:
    """The ON CONFLICT target names columns the DDL really constrains.

    This is the failure the helper exists to make impossible: PostgreSQL
    rejects an ON CONFLICT target with no matching unique/exclusion
    constraint (``there is no unique or exclusion constraint matching the ON
    CONFLICT specification``).
    """
    pg_sql = getattr(module, pg_attr)
    m = re.search(r"ON CONFLICT \(([^)]+)\) DO UPDATE SET", pg_sql)
    assert m is not None, pg_sql
    target = tuple(c.strip().strip('"') for c in m.group(1).split(","))
    constraints = ddl_constraints[table]
    assert target in constraints, (
        f"ON CONFLICT {target} on {table} is not a constraint the DDL declares (have {constraints})"
    )
    # and the helper agrees, resolved from the DDL rather than the statement
    assert list(target) == upsert_columns(table)


@pytest.mark.parametrize(("table", "module", "_orig", "sqlite_attr", "pg_attr"), SITES)
def test_pg_branch_placeholders_match_columns(
    table: str, module: Any, _orig: Any, sqlite_attr: str, pg_attr: str
) -> None:
    """One ``?`` per column — the incidents-store ``:name`` bug, not repeated.

    The driver boundary rewrites bare ``?`` to ``%s``; named placeholders are
    NOT rewritten, so a statement built with ``:name`` reaches psycopg with
    zero ``%s`` while the caller passes N values
    (``the query has 0 placeholders but 32 parameters were passed``).
    """
    pg_sql = getattr(module, pg_attr)
    cols = re.search(r"INSERT INTO \S+ \(([^)]+)\) VALUES", pg_sql)
    assert cols is not None, pg_sql
    n_cols = len([c for c in cols.group(1).split(",") if c.strip()])
    # every placeholder in the statement is a bare qmark
    assert ":" not in re.sub(r"excluded\.", "", pg_sql), "named placeholder leaked"
    placeholders = re.search(r"VALUES \(([^)]*)\)", pg_sql)
    assert placeholders is not None, pg_sql
    n_ph = len([p for p in placeholders.group(1).split(",") if p.strip()])
    assert n_ph == n_cols, f"{table}: {n_ph} placeholders for {n_cols} columns"
    assert set(re.findall(r"\?", pg_sql)) == {"?"}


@pytest.mark.parametrize(("table", "module", "_orig", "sqlite_attr", "pg_attr"), SITES)
def test_pg_branch_updates_every_non_key_column(
    table: str, module: Any, _orig: Any, sqlite_attr: str, pg_attr: str
) -> None:
    """INSERT OR REPLACE replaces the whole row; ON CONFLICT must too.

    A SET clause that omits a column would silently drop its value on update,
    so every non-key column gets ``col=excluded.col``.
    """
    pg_sql = getattr(module, pg_attr)
    key = set(upsert_columns(table))
    cols = re.search(r"INSERT INTO \S+ \(([^)]+)\) VALUES", pg_sql)
    assert cols is not None, pg_sql
    statement_cols = [c.strip().strip('"') for c in cols.group(1).split(",")]
    set_clause = re.search(r"DO UPDATE SET (.*)$", pg_sql, re.S)
    assert set_clause is not None, pg_sql
    for col in statement_cols:
        if col in key:
            continue
        assert f'"{col}"=excluded."{col}"' in set_clause.group(1), (
            f"{table}: column {col!r} missing from the SET clause"
        )


def test_upsert_columns_refuses_unconstrained_key() -> None:
    """A key the DDL does not cover raises instead of emitting invalid SQL."""
    with pytest.raises(UpsertKeyError):
        build_upsert_sql("shadow70_feature_health", ["snapshot_id"], sqlite_sql="--")
