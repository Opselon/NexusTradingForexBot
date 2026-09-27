"""PostgreSQL server-side ``updated_at`` trigger layer (AUDIT-0011).

THE INVARIANT
-------------
30+ audit-domain tables carry a timestamp column (``updated_at`` /
``checked_at``) that the application maintains manually — or not at all. That
is exactly how the "BREAKER persisted anchors rejected (stale/corrupt
identity)" warning happens: an UPDATE path forgot to stamp the column, the row
kept a pre-restart timestamp, and the breaker treated its own persisted anchor
as stale. The store code cannot enforce the invariant across two providers
(SQLite has no triggers here, PostgreSQL is the live one), so the database
enforces it: a trigger sets ``updated_at = NOW()`` on every UPDATE.

This is the correct use of a server-side trigger — the ONE function this
cluster needs. The schema lane keeps SQLite first-class (SQLite has no
server-side equivalent in this stack), and the layer is a no-op there.

WHY SECURITY INVOKER
--------------------
``SECURITY INVOKER`` (the default, stated explicitly) means the function
executes with the privileges of the *calling* user, never the table owner. A
trigger function that ran as the definer would be a privilege-escalation
surface: any role allowed to UPDATE the table could execute code at owner
privilege. There is no dynamic SQL, no security-definer side channel.

CONTRACT
--------
* exactly one PL/pgSQL statement (``NEW.updated_at = NOW()``); no control
  flow, no exception handlers, no side effects;
* one trigger function shared by every table — the function is column-agnostic
  (it only touches ``NEW.updated_at``), so a table without that column never
  gets a trigger (``_columns_with_trigger`` is the guard);
* ``DROP FUNCTION IF EXISTS`` + ``CREATE OR REPLACE FUNCTION`` keeps the
  definition idempotent (the body is a single statement, so re-creating it
  can never change behaviour);
* per-table triggers are ``CREATE TRIGGER`` guarded by an existence probe —
  PG has no ``CREATE TRIGGER IF NOT EXISTS``, so the guard is required for
  re-runnability;
* trigger names match the table, so they are discoverable and never collide.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nexus_scalp.database.migration.indexes import _recovery_hook

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: The one server-side function the cluster needs. ``SECURITY INVOKER`` is the
#: default and stated on purpose (see module docstring). ``plpgsql`` is the
#: only procedural language in the default PG install — no extension is
#: created, so this works on a fresh database without superuser steps.
#:
#: ONE function, ONE assignment, no control flow and no dynamic SQL. The
#: column is ``updated_at`` literally: the tables wired to this function are
#: exactly the ones carrying that column (``_columns_of`` guards it). A table
#: whose maintenance column is spelled differently (``model_runtime_health``'s
#: ``checked_at``) is deliberately NOT wired — a second function would break
#: the "one server-side function" contract, and that table is written by the
#: health checker on every sample, not by an UPDATE path that can forget.
#:
#: The value is the app's own UTC text form (``to_char(now() AT TIME ZONE
#: 'utc', ...)``) rather than ``NOW()`` itself: the schema's timestamp columns
#: are TEXT for SQLite parity, and ``NOW()`` would coerce to a
#: ``+00``-suffixed timestamptz string the application's own comparisons do
#: not produce. Same invariant, same format both providers speak.
UPDATED_AT_FUNCTION_DDL = """\
CREATE OR REPLACE FUNCTION nexus_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
BEGIN
    NEW.updated_at = to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS');
    RETURN NEW;
END;
$$
"""

#: Idempotent teardown (rollback path / re-create with a corrected body).
UPDATED_AT_FUNCTION_DROP_DDL = "DROP FUNCTION IF EXISTS nexus_set_updated_at() CASCADE"

#: The trigger function is owned by this module so the whole stack is one
#: import away for the migration runner and the tests.
UPDATED_AT_FUNCTION_NAME = "nexus_set_updated_at"

#: The column the trigger maintains. Every wired table carries this column;
#: see :data:`TRIGGER_TARGETS` for the one deliberate exception and why it is
#: not wired.
UPDATED_AT_COLUMN = "updated_at"

#: The tables the trigger layer maintains, with the column each one carries.
#: Derived from the domain DDL: every audit-domain table with an ``updated_at``
#: column that an UPDATE can legitimately refresh. Tables whose timestamp
#: column is only set on INSERT (append-only: ``created_at`` only) are
#: deliberately absent — a trigger that rewrote ``created_at`` would destroy
#: provenance.
#:
#: Only tables carrying the literal ``updated_at`` column are wired: the one
#: server-side function assigns that column by name (see
#: :data:`UPDATED_AT_FUNCTION_DDL`). ``model_runtime_health``'s ``checked_at``
#: is deliberately not wired (a second function would break the one-function
#: contract); the health checker rewrites the whole row on every sample.
#:
#: VERIFIED AGAINST THE LIVE SCHEMA (not the wishful list): two tables named
#: here originally do not carry the column the function assigns, and wiring
#: them would have installed a trigger that errors on every UPDATE —
#: * ``trading_rules_config`` has NO ``updated_at`` column (its DDL is
#:   rule_name / is_enabled / category / parameters — an append-once config
#:   table, not a row the app re-stamps).
#: * ``application_settings`` is not in nexusdb at all: it lives in the
#:   separate settings DB (``settings/service.py`` owns it), so the trigger
#:   layer's existence probe correctly skips it.
#: Both were removed rather than "fixed" — adding the column would be a schema
#: change this lane is contractually not making, and a config table the app
#: rewrites wholesale has no UPDATE path that can forget the stamp.
TRIGGER_TARGETS: dict[str, str] = {
    "calendar_worker_state": UPDATED_AT_COLUMN,
    "factory_loop_state": UPDATED_AT_COLUMN,
    "hygiene_worker_state": UPDATED_AT_COLUMN,
    "incidents": UPDATED_AT_COLUMN,
    "mk_enablement": UPDATED_AT_COLUMN,
    "mk_meta": UPDATED_AT_COLUMN,
    "mk_seeds": UPDATED_AT_COLUMN,
    "model_governance_state": UPDATED_AT_COLUMN,
    "news_article_versions": UPDATED_AT_COLUMN,
    "news_articles": UPDATED_AT_COLUMN,
    "release_metadata": UPDATED_AT_COLUMN,
    "strategy_intelligence_registry": UPDATED_AT_COLUMN,
    "strategy_registry": UPDATED_AT_COLUMN,
    "strategy_research_meta": UPDATED_AT_COLUMN,
}


#: The conventional name for one table's ``BEFORE UPDATE`` trigger.
def trigger_name_for(table: str) -> str:
    """The conventional trigger name for ``table`` (stable, discoverable)."""
    return f"trg_{table}_updated_at"


def column_for(table: str) -> str:
    """The maintenance column the trigger on ``table`` maintains."""
    return TRIGGER_TARGETS.get(table, UPDATED_AT_COLUMN)


def trigger_ddl(table: str, column: str | None = None) -> str:
    """The idempotent DDL wiring the trigger function to one table.

    The trigger is ``BEFORE UPDATE`` so the stamped value is what gets written
    (an ``AFTER`` trigger would need a second UPDATE). ``WHEN`` is intentionally
    absent: the guard is the trigger's *existence* (only tables carrying the
    column are registered), not a runtime predicate the planner evaluates on
    every row — this keeps the hot path free of a per-row condition, and avoids
    a text-vs-timestamp comparison the schema's SQLite-parity TEXT columns
    would otherwise have to coerce.
    """
    column = column if column is not None else column_for(table)
    fn = UPDATED_AT_FUNCTION_NAME
    # ``NEW`` is the trigger's row variable; the function is generic in it and
    # must never be inlined into a per-table specialisation (one function, one
    # definition, one place to fix).
    return (
        f"CREATE TRIGGER {trigger_name_for(table)}\n"
        f"    BEFORE UPDATE ON {table}\n"
        f"    FOR EACH ROW\n"
        f"    EXECUTE FUNCTION {fn}()"
    )


def list_missing_triggers(
    tables: list[str],
    *,
    execute_query: Callable[[str], list[tuple[Any, ...]]],
) -> list[tuple[str, str]]:
    """The (table, column) pairs whose trigger is not yet installed.

    ``execute_query`` runs one SELECT and returns rows. It takes the SQL string
    alone — the runner hands the SAME one-argument callable to every DBA layer,
    so the probe's parameters are interpolated as literals here rather than
    passed as a second argument (see :func:`_literal`). The probe covers both
    directions: a table can be missing (provisioning order) or the trigger can
    already exist (re-run). Tables present in :data:`TRIGGER_TARGETS` but absent
    from the database are skipped, never reported missing — the schema is
    provisioned by the DDL replay first.
    """
    if not tables:
        return []
    out: list[tuple[str, str]] = []
    for table in tables:
        column = column_for(table)
        rows = execute_query(
            f"""
            SELECT 1
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = {_literal(table)} AND n.nspname = current_schema()
              AND NOT t.tgisinternal AND t.tgname = {_literal(trigger_name_for(table))}
            """
        )
        if not rows:
            out.append((table, column))
    return out


def _columns_of(
    table: str,
    execute_query: Callable[[str], list[tuple[Any, ...]]],
) -> set[str]:
    """The lower-cased column names of ``table`` (empty when it is absent)."""
    rows = execute_query(
        f"""
        SELECT a.attname
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = {_literal(table)} AND n.nspname = current_schema()
          AND a.attnum > 0 AND NOT a.attisdropped
        """
    )
    return {str(r[0]).lower() for r in rows}


def apply_trigger_layer(
    *,
    execute: Callable[[str], None],
    execute_query: Callable[[str], list[tuple[Any, ...]]],
    query_scalar: Callable[[str], Any],
    recover: Callable[[], None] | None = None,
    tables: list[str] | None = None,
) -> dict[str, Any]:
    """Install the trigger function and every per-table trigger (idempotent).

    Returns an audit record: what was installed and what was skipped. Never
    raises — a trigger failure is recorded and logged so the boot path stays
    alive (the migration records ``FAILED``, the cluster still boots).
    ``recover`` rolls back an aborted transaction on the shared connection
    (see the DBA layer's :func:`indexes._recovery_hook`). ``execute_query`` is
    the same one-argument SELECT callable every DBA layer receives.
    """
    targets = tables if tables is not None else list(TRIGGER_TARGETS)
    applied: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    try:
        execute(UPDATED_AT_FUNCTION_DDL)
        applied.append(UPDATED_AT_FUNCTION_NAME)
    except Exception as exc:
        errors.append(
            {"statement": UPDATED_AT_FUNCTION_NAME, "error": f"{type(exc).__name__}: {exc}"}
        )
        logger.error("[DB-TRIGGERS] function install failed: %s", exc)
        _recovery_hook(recover, exc)
    for table in targets:
        column = column_for(table)
        try:
            exists = query_scalar(f"SELECT to_regclass({_literal(table)}) IS NOT NULL")
        except Exception as exc:
            errors.append({"statement": table, "error": f"probe: {type(exc).__name__}: {exc}"})
            _recovery_hook(recover, exc)
            continue
        if not exists:
            skipped.append(table)
            logger.debug("[DB-TRIGGERS] table %s absent, skipping trigger", table)
            continue
        # The trigger function touches ``NEW.<column>``; a table that exists
        # but has lost the column (a partial/legacy schema) must fail LOUD
        # rather than silently install a trigger that errors on every UPDATE.
        columns = _columns_of(table, execute_query)
        if column.lower() not in columns:
            errors.append(
                {
                    "statement": trigger_name_for(table),
                    "error": f"column {column!r} missing from {table}",
                }
            )
            logger.error(
                "[DB-TRIGGERS] %s exists without %s — trigger not installed "
                "(the schema must carry the maintenance column)",
                table,
                column,
            )
            continue
        try:
            missing = list_missing_triggers([table], execute_query=execute_query)
            if not missing:
                skipped.append(table)
                continue
            trigger = trigger_ddl(table, column)
            execute(trigger)
            applied.append(trigger_name_for(table))
            logger.info(
                "[DB-TRIGGERS] installed %s on %s(%s)", trigger_name_for(table), table, column
            )
        except Exception as exc:
            errors.append(
                {"statement": trigger_name_for(table), "error": f"{type(exc).__name__}: {exc}"}
            )
            logger.error("[DB-TRIGGERS] trigger for %s failed: %s", table, exc)
            _recovery_hook(recover, exc)
    logger.info(
        "[DB-TRIGGERS] installed=%d skipped=%d errors=%d",
        len(applied),
        len(skipped),
        len(errors),
    )
    return {
        "applied": applied,
        "applied_count": len(applied),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "errors": errors,
    }


def rollback_trigger_layer(*, execute: Callable[[str], Any]) -> None:
    """Drop the shared function (cascades to every per-table trigger)."""
    execute(UPDATED_AT_FUNCTION_DROP_DDL)


def _literal(value: str) -> str:
    """A single-quoted PG string literal (the probe's ``to_regclass`` arg)."""
    return "'" + value.replace("'", "''") + "'"
