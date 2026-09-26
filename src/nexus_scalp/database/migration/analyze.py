"""PostgreSQL stats + bloat maintenance (AUDIT-0012 / AUDIT-0013).

WHAT THIS FIXES
---------------
102 of 116 tables on the live cluster have ``reltuples = -1`` — they have
never been ANALYZEd, so the planner has no statistics at all. The fix is not a
schema change: it is a maintenance entry that runs ANALYZE on the public
schema, records itself in ``schema_migrations`` (so the schema lane's audit
trail stays complete), and is safe to re-run on a live cluster.

ANALYZE takes a SHARE lock; VACUUM takes one of similar strength. Neither
blocks reads or writes, but both contend on a table the tick hot path is
hitting, so this module runs at engine-idle moments only — never from the tick
path. ``apply_analyze_migrations`` is the entry the boot/idle scheduler calls;
nothing in this module is on the tick hot path and nothing here should be.

LOCK LEVELS (documented per the DBA contract)
---------------------------------------------
* ``ANALYZE`` — ``SHARE`` lock. Reads and writes continue; the lock only
  conflicts with other DDL that needs exclusive access (``DROP``,
  ``TRUNCATE``, most ``ALTER``). It is *not* free: on a large table the scan
  holds SHARE for the duration, so a concurrent ``ALTER TABLE`` waits.
* ``VACUUM`` — obtains ``SHARE UPDATE EXCLUSIVE`` per table. Also does not
  block reads/writes; it contends with ``ANALYZE`` itself and with other
  ``VACUUM`` on the same table (only one at a time).
* Neither runs inside a transaction block in PostgreSQL; both are issued on an
  autocommit connection by the runner (see :func:`pg_schema.apply_dba_migrations`).

IDEMPOTENCY
-----------
``ANALYZE`` is idempotent by nature (it only writes statistics, never schema).
The maintenance entries record their own state in ``schema_migrations`` with
``status='applied'``, and a re-run refreshes the statistics and re-writes the
record (``INSERT OR REPLACE`` semantics on the primary key). No duplicate
object can appear — ANALYZE creates no objects.

THRESHOLD
---------
:data:`VACUUM_DEAD_TUPLE_THRESHOLD` is a module constant so it is tunable
without editing logic. The reported bloat snapshot is read-only
(``pg_stat_user_tables``) and always returns, whether or not VACUUM ran.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from nexus_scalp.database.migration.indexes import _recovery_hook

logger = logging.getLogger(__name__)

#: Dead-tuple count above which the bloat maintenance runs VACUUM (ANALYZE) on
#: a table. 1000 is deliberately conservative: the live probe's worst table had
#: 49 dead tuples, and VACUUM is only worth its lock when bloat is real.
VACUUM_DEAD_TUPLE_THRESHOLD = 1000

#: Tables the stats maintenance ANALYZEs explicitly. The public-schema ANALYZE
#: covers everything, but the hot set is named so the reason log can state the
#: tables whose statistics the planner most needs. Derived from the probe's
#: seq-scan pressure list (see :mod:`indexes`).
HOT_TABLES: tuple[str, ...] = (
    "news_junk_hashes",
    "audit_ledger",
    "news_sources",
    "incidents",
    "audit_broker_orders",
    "audit_broker_trades",
    "audit_broker_deals",
    "audit_account_snapshots",
    "audit_guard_telemetry",
    "runtime_risk_state",
    "factory_generations",
    "model_runtime_health",
    "news_articles",
    "news_entities",
    "news_topics",
    "news_analysis_runs",
    "incident_value_traces",
    "trading_rules_config",
)

#: The read-only bloat report query. ``pg_stat_user_tables`` is per-database
#: and needs no superuser role. Columns are named so the caller can build the
#: report dict without a second lookup.
_BLOAT_REPORT_SQL = """
SELECT
    relname                          AS table_name,
    n_live_tup,
    n_dead_tup,
    COALESCE(last_analyze, last_autoanalyze) AS last_analyzed,
    COALESCE(last_vacuum, last_autovacuum)   AS last_vacuumed
FROM pg_stat_user_tables
WHERE n_dead_tup > 0 OR n_live_tup > 0
ORDER BY n_dead_tup DESC, n_live_tup DESC
"""

#: The dead-tuple probe for one table (used by the threshold gate).
_DEAD_TUPLES_SQL = """
SELECT n_dead_tup
FROM pg_stat_user_tables
WHERE relname = %s
"""


def _literal(value: str) -> str:
    """A single-quoted PG string literal for a probe query."""
    return "'" + value.replace("'", "''") + "'"


def _int(value: Any) -> int:
    """Coerce a probe result to a non-negative int (absent -> 0)."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def bloat_report(
    query: Callable[[str], list[tuple[Any, ...]]],
    recover: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Read-only dead-tuple report per table (``pg_stat_user_tables``).

    ``query`` runs one SELECT and returns rows. Never raises: a stats-schema
    failure degrades to an empty report, not a dead boot path.
    """
    try:
        rows = query(_BLOAT_REPORT_SQL)
    except Exception as exc:
        logger.error("[DB-ANALYZE] bloat report failed: %s", exc)
        _recovery_hook(recover, exc)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "table_name": str(row[0]),
                "n_live_tup": _int(row[1]),
                "n_dead_tup": _int(row[2]),
                "last_analyzed": str(row[3]) if row[3] is not None else "",
                "last_vacuumed": str(row[4]) if row[4] is not None else "",
            }
        )
    return out


def analyze_statements(tables: tuple[str, ...] | None = None) -> list[str]:
    """The ANALYZE statements for the hot set (or the whole public schema).

    ``ANALYZE public`` refreshes every table's statistics; the per-table
    spelling is kept for the reason log so an operator can see exactly which
    tables the hot-set pass covered. ``ANALYZE`` takes a SHARE lock — see the
    module docstring for the lock level and why this never runs on the tick
    path.
    """
    if tables is None:
        tables = HOT_TABLES
    return [f"ANALYZE {t}" for t in tables]


def apply_analyze_migrations(
    *,
    execute: Callable[[str], None],
    query: Callable[[str], list[tuple[Any, ...]]],
    query_scalar: Callable[[str], Any],
    recover: Callable[[], None] | None = None,
    record: Callable[[str, str, str], None] | None = None,
) -> dict[str, Any]:
    """Refresh planner statistics on the public schema (idempotent, read-safe).

    ``execute`` runs one SQL string on an AUTOCOMMIT connection (ANALYZE cannot
    run inside a transaction block); ``query`` / ``query_scalar`` run SELECTs;
    ``recover`` rolls back an aborted transaction on the shared connection;
    ``record(migration_id, status, detail)`` persists the entry in
    ``schema_migrations`` so the audit trail stays complete. All three callables
    take exactly one SQL argument — the runner hands the SAME callable to every
    DBA layer, so a layer that invents a second spelling breaks the boot path.

    The whole pass is best-effort by design: statistics are not schema, so a
    failure here is a warning and a ``FAILED`` record — never an exception out
    of the boot path.
    """
    applied: list[str] = []
    errors: list[dict[str, str]] = []
    statements = analyze_statements()
    for stmt in statements:
        try:
            execute(stmt)
            applied.append(stmt)
            logger.info("[DB-ANALYZE] %s", stmt)
        except Exception as exc:
            errors.append({"statement": stmt, "error": f"{type(exc).__name__}: {exc}"})
            logger.error("[DB-ANALYZE] %s failed: %s", stmt, exc)
            _recovery_hook(recover, exc)
    # The whole-schema pass covers every table the hot-set list may have missed
    try:
        execute("ANALYZE")
        applied.append("ANALYZE")
        logger.info("[DB-ANALYZE] schema statistics refreshed")
    except Exception as exc:
        errors.append({"statement": "ANALYZE", "error": f"{type(exc).__name__}: {exc}"})
        logger.error("[DB-ANALYZE] schema ANALYZE failed: autovacuum still owns stats")
        _recovery_hook(recover, exc)
    # The stats-maintenance entry records itself in the migration history so the
    # schema lane's audit trail stays complete, with a non-fatal FAILED status
    # when the pass could not refresh anything.
    if record is not None:
        try:
            record(
                "AUDIT-0012-analyze-stats-maintenance",
                "applied" if applied else "FAILED",
                f"analyze_statements={len(applied)} errors={len(errors)}",
            )
        except Exception as exc:
            logger.error("[DB-ANALYZE] migration record failed: %s", exc)
    logger.info(
        "[DB-ANALYZE] statements_ok=%d errors=%d threshold=%d",
        len(applied),
        len(errors),
        VACUUM_DEAD_TUPLE_THRESHOLD,
    )
    return {
        "applied": applied,
        "applied_count": len(applied),
        "error_count": len(errors),
        "errors": errors,
        "threshold": VACUUM_DEAD_TUPLE_THRESHOLD,
    }


def apply_vacuum_migrations(
    *,
    execute: Callable[[str], None],
    query: Callable[[str], list[tuple[Any, ...]]],
    recover: Callable[[], None] | None = None,
    record: Callable[[str, str, str], None] | None = None,
    threshold: int = VACUUM_DEAD_TUPLE_THRESHOLD,
) -> dict[str, Any]:
    """Report bloat per table and VACUUM (ANALYZE) the tables over threshold.

    The report is ALWAYS produced (read-only, ``pg_stat_user_tables``); VACUUM
    runs only on tables whose dead-tuple count exceeds the threshold. VACUUM
    cannot run inside a transaction block, so ``execute`` must be an autocommit
    connection (see :func:`pg_schema.apply_dba_migrations`).

    Never raises — the same FAILED-not-fatal contract as the ANALYZE entry.
    """
    report = bloat_report(query, recover=recover)
    vacuumed: list[str] = []
    errors: list[dict[str, str]] = []
    for entry in report:
        if entry["n_dead_tup"] < threshold:
            continue
        table = entry["table_name"]
        stmt = f"VACUUM (ANALYZE) {table}"
        try:
            execute(stmt)
            vacuumed.append(table)
            logger.info(
                "[DB-VACUUM] %s vacuumed (dead=%d >= threshold=%d)",
                table,
                entry["n_dead_tup"],
                threshold,
            )
        except Exception as exc:
            errors.append({"statement": stmt, "error": f"{type(exc).__name__}: {exc}"})
            logger.error("[DB-VACUUM] %s failed: %s", table, exc)
            _recovery_hook(recover, exc)
    if record is not None:
        try:
            record(
                "AUDIT-0013-vacuum-bloat-maintenance",
                "applied" if not errors else "FAILED",
                f"vacuumed={len(vacuumed)} report_rows={len(report)} errors={len(errors)}",
            )
        except Exception as exc:
            logger.error("[DB-VACUUM-RECORD] migration record failed: %s", exc)
    logger.info(
        "[DB-VACUUM] report_rows=%d vacuumed=%d errors=%d threshold=%d",
        len(report),
        len(vacuumed),
        len(errors),
        threshold,
    )
    return {
        "report": report,
        "report_count": len(report),
        "vacuumed": vacuumed,
        "vacuumed_count": len(vacuumed),
        "error_count": len(errors),
        "errors": errors,
        "threshold": threshold,
    }
