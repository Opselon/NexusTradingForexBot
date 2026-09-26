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
* ``ALTER TABLE ... ADD COLUMN`` gains ``IF NOT EXISTS``: SQLite guards the
  same statement with a PRAGMA pre-check in application code (and has no
  ``IF NOT EXISTS`` spelling at all), PG spells the guard directly — without it
  every re-provisioning run would fail on the columns it added last time.
* ``datetime('now')`` (a SQLite keyword-form call in registry DDL) becomes the
  equivalent UTC text expression; PG has no ``datetime()`` function, so the
  statement would otherwise be rejected while the table silently stays absent.

Everything else passes through unchanged. Each statement is idempotent
(``IF NOT EXISTS``) so the migration is safe to re-run on an existing
NexusDB — that is what makes "switch provider" a non-destructive operation.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from collections.abc import Callable
from typing import Any

from nexus_scalp.database.migration import analyze as analyze_mod
from nexus_scalp.database.migration import indexes as indexes_mod
from nexus_scalp.database.migration import triggers as triggers_mod

logger = logging.getLogger(__name__)

# Error names raised when the provider itself is unreachable — as opposed to a
# per-statement schema problem. Matched on the class name so this never imports
# psycopg (optional dependency) and works for both psycopg2 and psycopg3.
_CONNECTION_FAILURE_NAMES = (
    "OperationalError",
    "ConnectionFailure",
    "PoolTimeout",
    "TimeoutError",
    "ConnectTimeoutError",
    "ConnectionRefusedError",
    "ConnectionResetError",
)


def _is_connection_failure(exc: BaseException) -> bool:
    """True when ``exc`` means the provider is unreachable (not schema drift)."""
    if type(exc).__name__ in _CONNECTION_FAILURE_NAMES:
        return True
    # psycopg's pool raises these on a dead DSN; the message is the stable part.
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "connection refused",
            "could not connect",
            "connection to server at",
            "timeout expired",
        )
    )


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


#: SQLite's ``datetime('now')`` — UTC 'YYYY-MM-DD HH:MM:SS' as text.
_SQLITE_DATETIME_NOW = re.compile(r"(?i)\bdatetime\s*\(\s*(['\"])now\1\s*\)")
_PG_DATETIME_NOW = "to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')"

#: SQLite has no ``ADD COLUMN IF NOT EXISTS``; the application guards it with a
#: PRAGMA pre-check instead. PG has the guard, and needs it: every extracted
#: ALTER must survive a re-provisioning run over the schema it created.
_ADD_COLUMN_GUARD = re.compile(r"(?i)(\bALTER\s+TABLE\s+(?:\"[^\"]+\"|\w+)\s+ADD\s+COLUMN\s+)")


def translate_ddl(statement: str) -> str:
    """Translate one SQLite DDL statement to PostgreSQL dialect."""
    if re.match(r"(?i)^\s*CREATE\s+VIRTUAL\s+TABLE", statement):
        raise ValueError(
            "CREATE VIRTUAL TABLE has no PostgreSQL equivalent in the audit "
            "schema; refusing to emit a silently-wrong translation"
        )
    out = translate_type(statement)
    out = _SQLITE_DATETIME_NOW.sub(_PG_DATETIME_NOW, out)
    out = _ADD_COLUMN_GUARD.sub(r"\1IF NOT EXISTS ", out)
    if re.search(r"(?i)^\s*CREATE\s+(UNIQUE\s+)?INDEX", out):
        out = _type_partial_predicate(out)
    # collapse the harmless double space the substitutions can leave
    out = re.sub(r"[ \t]+\n", "\n", out)
    return out.strip()


# --- execution ---------------------------------------------------------------


#: Columns the engine's own history table carries (engine._record_migration).
#: Kept in sync with ``_HISTORY_TABLE_DDL`` rather than copied so a new column
#: added there surfaces here as a compile-time import error instead of a silent
#: drift between the recorded row shapes.
_HISTORY_COLUMNS = (
    "migration_id",
    "domain",
    "version",
    "description",
    "checksum",
    "applied_at",
    "application_version",
    "git_commit",
    "execution_ms",
    "status",
)


def record_applied_migrations(
    domain: str,
    migrations,
    execute,
    *,
    application_version: str = "",
    git_commit: str = "",
) -> int:
    """Record the applied migration chain for ``domain`` on a PostgreSQL db.

    ``execute(sql, args)`` runs one parameterized statement. This mirrors what
    the SQLite engine's ``_record_migration`` does per migration, in PG dialect
    (``INSERT ... ON CONFLICT DO UPDATE``; PG has no ``INSERT OR REPLACE``).
    The migration's DDL is *not* replayed here — the provisioner's own statement
    list already created every object the chain owns; only the ledger row is
    missing, and re-running DDL out of order could fight the IF NOT EXISTS replay.

    Idempotent: re-running over a recorded chain refreshes the checksum row.
    Returns the number of rows written.
    """
    from nexus_scalp.database.models import DatabaseDomain

    try:
        domain_enum = DatabaseDomain(domain)
        domain_value = domain_enum.value
    except ValueError:
        domain_value = domain  # an ops domain (ops_shadow / ops_hygiene)

    placeholders = ", ".join("%s" for _ in _HISTORY_COLUMNS)
    column_list = ", ".join(_HISTORY_COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _HISTORY_COLUMNS if c != "migration_id")
    sql = (
        f"INSERT INTO schema_migrations ({column_list}) VALUES ({placeholders}) "
        f"ON CONFLICT (migration_id) DO UPDATE SET {updates}"
    )
    written = 0
    for mig in migrations:
        try:
            checksum = mig.checksum() if callable(mig.checksum) else str(mig.checksum)
        except Exception:
            checksum = ""
        args = (
            mig.migration_id,
            domain_value,
            int(mig.to_version),
            mig.description,
            checksum,
            datetime.now(UTC).isoformat(),
            application_version,
            git_commit,
            0,
            "applied",
        )
        try:
            execute(sql, args)
            written += 1
        except Exception as exc:
            # Recording must never mask the schema work itself, but a silent
            # no-op here is exactly the empty-ledger defect this fixes, so the
            # failure is logged loudly rather than swallowed.
            logger.error(
                "[DB-MIGRATE] failed to record migration %s on domain %s: %s",
                mig.migration_id,
                domain_value,
                exc,
            )
    if written:
        logger.info(
            "[DB-MIGRATE] recorded %d applied migration(s) for domain %s",
            written,
            domain_value,
        )
    return written


def _recorded_domain_migrations(domain: str):
    """The migration registry's chain for a domain, or None when it has none."""
    from nexus_scalp.database.models import DatabaseDomain
    from nexus_scalp.database.registry import migrations_for

    try:
        domain_enum = DatabaseDomain(domain)
    except ValueError:
        return None  # ops domain: no governed registry chain
    try:
        return migrations_for(domain_enum)
    except KeyError:
        return None


def apply_schema(
    statements: list[str], execute, *, stop_on_error: bool = False, domain: str = ""
) -> dict[str, object]:
    """Translate + apply a schema on a PostgreSQL connection.

    ``execute`` is any callable running one SQL string (a psycopg cursor or a
    thin wrapper). Returns an audit record of what happened — the migration is
    observable by design, never a silent best-effort.

    When ``domain`` names a governed registry domain, the applied migration
    chain is recorded into ``schema_migrations`` after the DDL lands. Without
    that step a freshly provisioned PostgreSQL database has every table but an
    EMPTY ledger, so every version/convergence check reports "never migrated".
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
            # Fast-fail on an unreachable provider. Without this, a dead DSN
            # replays EVERY remaining statement and each one waits on the pool's
            # connect timeout (128 statements x timeout = an unbounded,
            # multi-minute-to-forever provisioning pass that a caller cannot
            # distinguish from a hang). An unreachable server is not per-statement
            # drift, so stop replaying and let the caller report the outage.
            if _is_connection_failure(exc):
                logger.error(
                    "[DB-MIGRATE] provider unreachable, aborting schema replay "
                    "after %d/%d statement(s)",
                    len(applied),
                    len(statements),
                )
                break
            if stop_on_error:
                break
    migrations_recorded = 0
    if domain:
        chain = _recorded_domain_migrations(domain)
        if chain:
            try:
                migrations_recorded = record_applied_migrations(domain, chain, execute)
            except Exception as exc:  # never let recording abort provisioning
                logger.error(
                    "[DB-MIGRATE] migration recording failed for domain %s: %s",
                    domain,
                    exc,
                )
                errors.append(
                    {
                        "statement": "record_applied_migrations",
                        "error": f"record: {type(exc).__name__}: {exc}",
                    }
                )
    result: dict[str, object] = {
        "applied": applied,
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "errors": errors,
        "migrations_recorded": migrations_recorded,
    }
    logger.info(
        "[DB-MIGRATE] schema applied=%d errors=%d migrations_recorded=%d",
        len(applied),
        len(errors),
        migrations_recorded,
    )
    return result


# --- DBA layer: ordered, idempotent server-side migrations -------------------
#
# The DBA migrations are the server-side query layer the operator expects on a
# live PostgreSQL cluster: missing indexes, the ``updated_at`` trigger function
# and its per-table wiring, ANALYZE stats maintenance and VACUUM bloat
# maintenance. They are NOT schema (no table is created or altered), so they do
# not live in the SQLite migration registry — SQLite has no server-side objects
# and stays first-class. They run on the DDL-replayed PostgreSQL schema, in a
# stable id order, after ``apply_schema`` has created the tables.
#
# TRANSACTION CONTRACT
# --------------------
# ``CREATE INDEX CONCURRENTLY``, ``VACUUM`` and ``ANALYZE`` cannot run inside a
# transaction block — PostgreSQL raises ``ActiveSqlTransaction``. psycopg3
# defaults to ``autocommit=False``, so a connection that has already executed a
# statement is inside an implicit transaction. The runner therefore asks the
# caller for TWO executors: ``execute`` (transactional, DDL/trigger function)
# and ``execute_autocommit`` (one statement per implicit transaction, for the
# statements PG forbids inside a block). The record for each migration states
# which path it took.
#
# ORDERING + FAILURE CONTRACT
# ---------------------------
# Migrations apply in ascending :data:`DBA_MIGRATION_IDS` order and each is
# independently idempotent. A failed migration logs ERROR, records
# ``status='FAILED'`` and the runner CONTINUES — a failure never raises out of
# the boot path and never blocks the cluster from coming up.
#
# Lock levels: ANALYZE takes SHARE, VACUUM takes SHARE UPDATE EXCLUSIVE, an
# index build takes SHARE. None block reads/writes; all contend on a tick-hot
# table, so the caller schedules the DBA layer at engine-idle moments only.

#: The ordered DBA migration ids. ``AUDIT-0010`` follows the last registry
#: migration (``AUDIT-0009``) and each entry is a stable, monotone key the
#: history table records. The order is dependency-ordered, not urgency-ordered:
#: indexes first (they need nothing but the tables), the trigger function next
#: (a trigger needs the function to exist), then stats, then bloat (VACUUM
#: ANALYZE refreshes the stats the ANALYZE entry just wrote, so it runs last).
DBA_MIGRATION_IDS: tuple[str, ...] = (
    "AUDIT-0010-missing-indexes",
    "AUDIT-0011-updated-at-triggers",
    "AUDIT-0012-analyze-stats-maintenance",
    "AUDIT-0013-vacuum-bloat-maintenance",
)

#: The human description the history table stores alongside each id.
DBA_MIGRATION_DESCRIPTIONS: dict[str, str] = {
    "AUDIT-0010-missing-indexes": (
        "evidence-based indexes for the seq-scan pressure tables "
        "(news_junk_hashes / audit_ledger / news_sources / incidents / broker history)"
    ),
    "AUDIT-0011-updated-at-triggers": (
        "SECURITY INVOKER updated_at trigger function + per-table wiring "
        "(the invariant the app cannot enforce across two providers)"
    ),
    "AUDIT-0012-analyze-stats-maintenance": (
        "ANALYZE the public schema (102/116 tables have reltuples=-1) so the "
        "planner has statistics; SHARE lock, idle-path only"
    ),
    "AUDIT-0013-vacuum-bloat-maintenance": (
        "read-only n_dead_tup report + VACUUM (ANALYZE) above "
        "VACUUM_DEAD_TUPLE_THRESHOLD; SHARE UPDATE EXCLUSIVE, idle-path only"
    ),
}


def _record_migration(
    record: Callable[[str, str, str], None] | None,
    migration_id: str,
    status: str,
    detail: str,
) -> None:
    """Persist one DBA migration's outcome in ``schema_migrations``.

    ``record(migration_id, status, detail)`` is the caller-supplied writer
    (the fabric's write plane); None means the caller does not want the audit
    trail written (the unit tests of the layers themselves). Failures here are
    logged, never raised — the audit trail is best-effort and a dead history
    table must not kill the boot.
    """
    if record is None:
        return
    try:
        record(migration_id, status, detail)
    except Exception as exc:
        logger.error("[DB-MIGRATE] history record failed for %s: %s", migration_id, exc)


def apply_dba_migrations(
    *,
    execute: Callable[[str], None],
    execute_autocommit: Callable[[str], None],
    query: Callable[[str], list[tuple[Any, ...]]],
    query_scalar: Callable[[str], Any],
    recover: Callable[[], None] | None = None,
    record: Callable[[str, str, str], None] | None = None,
    concurrently: bool = True,
) -> dict[str, Any]:
    """Apply the ordered DBA migration set to a PostgreSQL provider.

    The four entries run in :data:`DBA_MIGRATION_IDS` order, each idempotent,
    each isolated from the others' failures. The whole call returns an audit
    record (per-migration results + the ordered history) and never raises:
    a failed migration becomes ``status='FAILED'`` in the history table and the
    runner continues, so the cluster always finishes booting.

    ``execute`` runs a statement inside a transaction block (indexes without
    CONCURRENTLY, the trigger function, per-table triggers). Statements PG
    forbids inside a block — ``CREATE INDEX CONCURRENTLY``, ``ANALYZE``,
    ``VACUUM`` — go through ``execute_autocommit`` instead. ``recover`` rolls
    back an aborted transaction so one failed statement cannot poison the
    shared connection for the rest of the pass. Which path each migration took
    is recorded in its result and in the history detail.
    """
    history: list[dict[str, str]] = []
    per_migration: dict[str, dict[str, Any]] = {}
    error_total = 0

    # AUDIT-0010 — missing indexes. CONCURRENTLY needs an autocommit
    # connection; inside a transaction block the plain IF NOT EXISTS spelling
    # is correct and the runner records which path it used.
    index_result: dict[str, Any]
    try:
        index_result = indexes_mod.apply_index_migrations(
            execute=execute_autocommit if concurrently else execute,
            query_scalar=query_scalar,
            recover=recover,
            concurrently=concurrently,
        )
    except Exception as exc:
        index_result = {
            "applied": [],
            "applied_count": 0,
            "error_count": 1,
            "errors": [{"statement": "AUDIT-0010", "error": f"{type(exc).__name__}: {exc}"}],
            "concurrently": concurrently,
        }
        logger.error("[DB-MIGRATE] AUDIT-0010 raised out of its layer: %s", exc)
    error_total += int(index_result.get("error_count") or 0)
    per_migration["AUDIT-0010-missing-indexes"] = index_result
    _record_migration(
        record,
        "AUDIT-0010-missing-indexes",
        "applied" if not index_result.get("error_count") else "FAILED",
        f"indexes={index_result.get('applied_count', 0)} "
        f"errors={index_result.get('error_count', 0)} "
        f"concurrently={index_result.get('concurrently', concurrently)}",
    )
    history.append(
        {
            "migration_id": "AUDIT-0010-missing-indexes",
            "status": "applied" if not index_result.get("error_count") else "FAILED",
        }
    )

    # AUDIT-0011 — the updated_at trigger layer. The function and the triggers
    # are plain DDL, so both go through the transactional executor.
    trigger_result: dict[str, Any]
    try:
        trigger_result = triggers_mod.apply_trigger_layer(
            execute=execute,
            execute_query=query,
            query_scalar=query_scalar,
            recover=recover,
        )
    except Exception as exc:
        trigger_result = {
            "applied": [],
            "applied_count": 0,
            "error_count": 1,
            "errors": [{"statement": "AUDIT-0011", "error": f"{type(exc).__name__}: {exc}"}],
        }
        logger.error("[DB-MIGRATE] AUDIT-0011 raised out of its layer: %s", exc)
    error_total += int(trigger_result.get("error_count") or 0)
    per_migration["AUDIT-0011-updated-at-triggers"] = trigger_result
    _record_migration(
        record,
        "AUDIT-0011-updated-at-triggers",
        "applied" if not trigger_result.get("error_count") else "FAILED",
        f"triggers={trigger_result.get('applied_count', 0)} "
        f"errors={trigger_result.get('error_count', 0)}",
    )
    history.append(
        {
            "migration_id": "AUDIT-0011-updated_at-triggers",
            "status": "applied" if not trigger_result.get("error_count") else "FAILED",
        }
    )

    # AUDIT-0012 — ANALYZE stats maintenance. ANALYZE cannot run inside a
    # transaction block, so it always uses the autocommit executor.
    try:
        analyze_result = analyze_mod.apply_analyze_migrations(
            execute=execute_autocommit,
            query=query,
            query_scalar=query_scalar,
            recover=recover,
            record=None,
        )
    except Exception as exc:
        analyze_result = {
            "applied": [],
            "applied_count": 0,
            "error_count": 1,
            "errors": [{"statement": "AUDIT-0012", "error": f"{type(exc).__name__}: {exc}"}],
        }
        logger.error("[DB-MIGRATE] AUDIT-0012 raised out of its layer: %s", exc)
    error_total += int(analyze_result.get("error_count", 0))
    per_migration["AUDIT-0012-analyze-stats-maintenance"] = analyze_result
    _record_migration(
        record,
        "AUDIT-0012-analyze-stats-maintenance",
        "applied" if not analyze_result.get("error_count") else "FAILED",
        f"analyze_statements={analyze_result.get('applied_count', 0)} "
        f"errors={analyze_result.get('error_count', 0)}",
    )
    history.append(
        {
            "migration_id": "AUDIT-0012-analyze-stats-maintenance",
            "status": "applied" if not analyze_result.get("error_count") else "FAILED",
        }
    )

    # AUDIT-0013 — VACUUM bloat maintenance. VACUUM cannot run inside a
    # transaction block either. Runs last: VACUUM (ANALYZE) refreshes the
    # statistics the AUDIT-0012 pass just wrote.
    try:
        vacuum_result = analyze_mod.apply_vacuum_migrations(
            execute=execute_autocommit,
            query=query,
            recover=recover,
            record=None,
        )
    except Exception as exc:
        vacuum_result = {
            "report": [],
            "report_count": 0,
            "vacuumed": [],
            "vacuumed_count": 0,
            "error_count": 1,
            "errors": [{"statement": "AUDIT-0013", "error": f"{type(exc).__name__}: {exc}"}],
        }
        logger.error("[DB-MIGRATE] AUDIT-0013 raised out of its layer: %s", exc)
    error_total += int(vacuum_result.get("error_count", 0))
    per_migration["AUDIT-0013-vacuum-bloat-maintenance"] = vacuum_result
    _record_migration(
        record,
        "AUDIT-0013-vacuum-bloat-maintenance",
        "applied" if not vacuum_result.get("error_count") else "FAILED",
        f"vacuumed={vacuum_result.get('vacuumed_count', 0)} "
        f"report_rows={vacuum_result.get('report_count', 0)} "
        f"errors={vacuum_result.get('error_count', 0)}",
    )
    history.append(
        {
            "migration_id": "AUDIT-0013-vacuum-bloat-maintenance",
            "status": "applied" if not vacuum_result.get("error_count") else "FAILED",
        }
    )

    result: dict[str, Any] = {
        "migrations": DBA_MIGRATION_IDS,
        "per_migration": per_migration,
        "history": history,
        "applied_count": sum(int(m.get("applied_count", 0)) for m in per_migration.values()),
        "error_count": error_total,
        "errors": [
            {"migration": mid, **err}
            for mid, res in per_migration.items()
            for err in res.get("errors", [])
        ],
    }
    logger.info(
        "[DB-MIGRATE] DBA layer applied=%d errors=%d",
        result["applied_count"],
        error_total,
    )
    return result
