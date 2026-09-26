"""PostgreSQL missing-index layer (AUDIT-0010) — evidence-based indexes.

EVIDENCE
--------
The live cluster probe found real seq-scan pressure on five tables, and 51 of
116 tables carry the primary key as their ONLY index. Every candidate below is
derived from the application's actual WHERE / ORDER BY clauses (grep of the
``*store.py`` family and ``audit_repository.py``), not from guesswork:

    news_junk_hashes    seq=8611 / idx=482   <- hot ingestion filter
    audit_ledger        seq=341  / idx=170   <- trade browser / status filter
    news_sources        seq=195  / idx=127   <- enabled-source poll loop
    incidents           seq=87   / idx=14    <- browser + status IN (...)
    audit_broker_orders seq=80   / idx=9     <- broker history sync

Each entry records the exact predicate it serves, so a later EXPLAIN review
can confirm the planner adopted it.

IDEMPOTENCY + TRANSACTION CONTRACT
---------------------------------
Every index is ``CREATE INDEX IF NOT EXISTS`` — a second application is a
no-op, never an error and never a duplicate object.

``CREATE INDEX CONCURRENTLY`` cannot run inside a transaction block (PG raises
``ActiveSqlTransaction``). psycopg3 defaults to ``autocommit=False``, so a
plain ``execute`` on a connection that has already run a statement is inside
an implicit transaction. This module therefore emits the ``CONCURRENTLY``
spelling ONLY when the caller passes ``concurrently=True`` AND hands the runner
an autocommit connection (see :func:`pg_schema.apply_dba_migrations`); inside a
transaction block the plain ``IF NOT EXISTS`` spelling is used instead, which
is correct for the boot path's statement-at-a-time batches. The runner records
which path it took in the migration audit record.

SQLite stays first-class: the DDL is authored in the SQLite dialect and reaches
PostgreSQL through :func:`pg_schema.translate_ddl` (the one translation path).

Never a DROP, never a column rewrite: adding an index touches no row and no
existing access path (the planner keeps the old one until it wins on cost).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _recovery_hook(recover: Callable[[], None] | None, exc: BaseException) -> None:
    """Best-effort recovery after a statement failed on a pooled connection.

    PostgreSQL aborts the whole transaction on most statement errors
    (``current transaction is aborted, commands ignored until end of
    transaction block``), and psycopg3 refuses to change ``autocommit`` while
    the connection is not idle. A DBA layer that shares one connection with
    the boot path would otherwise poison every later statement - the probe
    after a failed index, the ANALYZE pass, and the history-table record all
    see ``InFailedSqlTransaction`` instead of their own real result.

    The DBA layers never hold an intentional multi-statement transaction (each
    statement is its own logical unit, and the ones that cannot run inside a
    block go through the autocommit executor), so rolling the aborted
    transaction back is always the correct recovery: it loses nothing but the
    already-failed statement. ``recover`` is the caller-supplied rollback; it
    is best-effort and never raises back into the layer.
    """
    if recover is None:
        return
    message = str(exc).lower()
    aborted = (
        "current transaction is aborted" in message
        or "infailedsqltransaction" in message
        or type(exc).__name__ == "InFailedSqlTransaction"
    )
    if not aborted:
        return
    try:
        recover()
    except Exception:
        logger.debug("[DB-LAYER] connection recovery failed (ignored)")


@dataclass(frozen=True)
class MissingIndex:
    """One evidence-based index: the DDL, the table, and why it exists."""

    #: The index name (also the idempotency key: ``IF NOT EXISTS`` probes it).
    name: str
    #: The table the index is built on.
    table: str
    #: The index body — everything after ``ON <table>``: the column list, a
    #: ``WHERE`` predicate, or ``INCLUDE`` covering columns.
    definition: str
    #: The application query shape this index serves (the WHERE / ORDER BY the
    #: seq-scan pressure comes from) — evidence, not decoration.
    predicate: str

    def sqlite_ddl(self) -> str:
        """The statement in the SQLite dialect (the schema's source of truth)."""
        return f"CREATE INDEX IF NOT EXISTS {self.name} ON {self.table} {self.definition}"

    def postgres_ddl(self, *, concurrently: bool = False) -> str:
        """The statement in the PostgreSQL dialect.

        ``concurrently`` emits the PG-only keyword; the caller is responsible
        for the connection being outside a transaction block when it is set
        (see module docstring — PG raises ``ActiveSqlTransaction`` otherwise).
        """
        prefix = (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            if concurrently
            else "CREATE INDEX IF NOT EXISTS "
        )
        return prefix + f"{self.name} ON {self.table} {self.definition}"


#: The seq-scan pressure is real: the ingestion path filters ``news_junk_hashes``
#: by ``article_hash`` on EVERY candidate article (``db_articles.is_junk_hash``).
#: That lookup is already covered by the PK; the remaining pressure is the
#: prune/retention scan, which filters by ``reason`` and walks ``pruned_at`` —
#: the composite index turns it into an index-only read.
_INDEX_NEWS_JUNK_REASON = (
    "news_junk_hashes seq=8611/idx=482; the junk check (article_hash) is covered "
    "by the PK, and the prune/retention scan filters reason + pruned_at"
)

#: The trade browser filters by status and paginates by ticket
#: (audit_ledger seq=341/idx=170).
_INDEX_AUDIT_LEDGER_STATUS_REASON = (
    "audit_ledger seq=341/idx=170; get_ledger_trades filters status=? and "
    "paginates ORDER BY ticket DESC LIMIT/OFFSET"
)

#: The dashboard's per-symbol equity/ledger walk (``list_closed_trades`` /
#: equity-curve reconstruction) filters by symbol and walks timestamp.
_INDEX_AUDIT_LEDGER_SYMBOL_REASON = (
    "audit_ledger per-symbol ledger walk WHERE symbol=? ORDER BY timestamp; "
    "no index covers symbol today (only close_time, a single-column index)"
)

#: The news poll loop lists enabled sources ordered by priority on every cycle
#: (news_sources seq=195/idx=127); the table carries only its PK today.
_INDEX_NEWS_SOURCES_REASON = (
    "news_sources seq=195/idx=127; list_sources(enabled_only) "
    "WHERE enabled=1 ORDER BY priority DESC, source_id"
)

#: ``incidents.by_component`` aggregates by component with a severity case
#: count (``GROUP BY component ORDER BY n DESC LIMIT 50``).
_INDEX_INCIDENTS_COMPONENT_REASON = (
    "incidents by_component: GROUP BY component ORDER BY count(*) DESC LIMIT 50; "
    "component is un-indexed so the aggregate spills to a seq scan"
)

#: The incident browser filters by status/severity/category and paginates
#: (incidents seq=87/idx=14); the existing single-column indexes cannot serve
#: the combined predicate.
_INDEX_INCIDENTS_REASON = (
    "incidents seq=87/idx=14; list() filters status IN (...) + severity + "
    "category and paginates ORDER BY severity-rank, detected_at DESC"
)

#: Broker-history sync resolves orders by position
#: (audit_broker_orders seq=80/idx=9); the table carries only its PK.
_INDEX_BROKER_ORDERS_REASON = (
    "audit_broker_orders seq=80/idx=9; get_broker_orders WHERE position_id=? ORDER BY time_setup"
)

#: Broker-deals lookup by position is the sibling access path; the deals table
#: already carries a ``position_id`` index from its own DDL, and this entry
#: closes the equivalent gap on the orders/trades side.
_INDEX_BROKER_DEALS_REASON = (
    "audit_broker_deals WHERE position_id=? ORDER BY time (the sibling of the "
    "orders access path; deals already carries the index, trades does not)"
)

#: The trades table's ``position_id`` has no index (only the trade_id PK).
_INDEX_BROKER_TRADES_REASON = (
    "audit_broker_trades position_id has no index (PK only); get_broker_trades "
    "filters symbol + position_id"
)

#: ``factory_generations`` is read by ``ORDER BY number DESC LIMIT n`` (factory
#: store ``list_generations``) and by ``created_at DESC`` (research store) —
#: the table carries only its PK, so both sort in memory.
_INDEX_FACTORY_GENERATIONS_REASON = (
    "factory_generations ORDER BY number DESC / created_at DESC list paths (PK only today)"
)

#: ``runtime_risk_state`` is read by ``WHERE id = 1`` (the breaker anchor
#: lookup). The CHECK-guarded PRIMARY KEY covers it, so this is deliberately
#: NOT an index — recorded here to document that the hot path is already
#: indexed and needs no new object.
_NO_INDEX_RUNTIME_RISK_STATE = (
    "runtime_risk_state WHERE id=1 is covered by the CHECK-guarded PRIMARY KEY (no index needed)"
)


#: The ordered set of missing indexes. Order is hot-path-first; the migration
#: is idempotent as a whole, so the ordering is readability, not dependency.
MISSING_INDEXES: tuple[MissingIndex, ...] = (
    MissingIndex(
        name="idx_news_junk_hashes_reason",
        table="news_junk_hashes",
        # The junk check filters on the PK (article_hash); the prune scan is
        # the un-indexed access path this composite closes.
        definition="(reason, pruned_at)",
        predicate=_INDEX_NEWS_JUNK_REASON,
    ),
    MissingIndex(
        name="idx_audit_ledger_status_ticket",
        table="audit_ledger",
        definition="(status, ticket DESC)",
        predicate=_INDEX_AUDIT_LEDGER_STATUS_REASON,
    ),
    MissingIndex(
        name="idx_audit_ledger_symbol_timestamp",
        table="audit_ledger",
        definition="(symbol, timestamp)",
        predicate=_INDEX_AUDIT_LEDGER_SYMBOL_REASON,
    ),
    MissingIndex(
        name="idx_news_sources_enabled_priority",
        table="news_sources",
        definition="(enabled, priority DESC, source_id)",
        predicate=_INDEX_NEWS_SOURCES_REASON,
    ),
    MissingIndex(
        name="idx_incidents_status_severity_detected",
        table="incidents",
        definition="(status, severity, detected_at DESC)",
        predicate=_INDEX_INCIDENTS_REASON,
    ),
    MissingIndex(
        name="idx_incidents_component_status",
        table="incidents",
        # by_component: GROUP BY component with a critical/high case count.
        definition="(component, status)",
        predicate=_INDEX_INCIDENTS_COMPONENT_REASON,
    ),
    MissingIndex(
        name="idx_audit_broker_orders_position",
        table="audit_broker_orders",
        definition="(position_id, time_setup)",
        predicate=_INDEX_BROKER_ORDERS_REASON,
    ),
    MissingIndex(
        name="idx_audit_broker_deals_position_time",
        table="audit_broker_deals",
        definition="(position_id, time)",
        predicate=_INDEX_BROKER_DEALS_REASON,
    ),
    MissingIndex(
        name="idx_audit_broker_trades_position",
        table="audit_broker_trades",
        definition="(position_id)",
        predicate=_INDEX_BROKER_TRADES_REASON,
    ),
    MissingIndex(
        name="idx_factory_generations_number",
        table="factory_generations",
        definition="(number DESC)",
        predicate=_INDEX_FACTORY_GENERATIONS_REASON,
    ),
)


def index_names() -> list[str]:
    """The names of every index this layer owns (the idempotency keys)."""
    return [idx.name for idx in MISSING_INDEXES]


def index_for(name: str) -> MissingIndex | None:
    """The index declaration for ``name``, or None when it is not owned here."""
    for idx in MISSING_INDEXES:
        if idx.name == name:
            return idx
    return None


def apply_index_migrations(
    *,
    execute: Callable[[str], None],
    query_scalar: Callable[[str], Any],
    recover: Callable[[], None] | None = None,
    concurrently: bool = False,
) -> dict[str, Any]:
    """Apply every missing index, idempotently and in order.

    ``execute`` runs one DDL string; ``query_scalar`` runs one SELECT and
    returns a single value; ``recover`` rolls back an aborted transaction on
    the shared connection (see :func:`_recovery_hook`). ``concurrently``
    requests the CONCURRENTLY spelling — only valid on an autocommit
    (non-transactional) connection; the caller in :mod:`pg_schema` chooses the
    path and records which one was used.

    A table absent from the database is skipped, never an error: the DDL replay
    creates the tables first, and a re-run after provisioning picks the index
    up (that is what makes the layer re-runnable on a partially-booted cluster).

    Never raises: an index failure is recorded in the audit trail, logged, and
    the migration is marked FAILED — the cluster still boots.
    """
    applied: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    for idx in MISSING_INDEXES:
        try:
            table_exists = query_scalar(f"SELECT to_regclass({_literal(idx.table)}) IS NOT NULL")
        except Exception as exc:
            errors.append({"statement": idx.name, "error": f"probe: {type(exc).__name__}: {exc}"})
            logger.error("[DB-INDEXES] existence probe failed for %s: %s", idx.table, exc)
            _recovery_hook(recover, exc)
            continue
        if not table_exists:
            skipped.append(idx.name)
            logger.debug("[DB-INDEXES] table %s absent, skipping index %s", idx.table, idx.name)
            continue
        # ``IF NOT EXISTS`` is the idempotency contract: a re-run is a no-op,
        # not an error and never a second copy of the index.
        ddl = idx.postgres_ddl(concurrently=concurrently)
        try:
            execute(ddl)
            applied.append(idx.name)
            logger.info("[DB-INDEXES] index %s built on %s", idx.name, idx.table)
        except Exception as exc:
            errors.append({"statement": idx.name, "error": f"{type(exc).__name__}: {exc}"})
            logger.error("[DB-INDEXES] index %s failed: %s", idx.name, exc)
            _recovery_hook(recover, exc)
    logger.info(
        "[DB-INDEXES] built=%d skipped=%d errors=%d",
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
        "concurrently": concurrently,
    }


def rollback_index_migrations(*, execute: Callable[[str], None]) -> None:
    """Drop every index this layer created (never touches a row)."""
    for idx in MISSING_INDEXES:
        execute(f"DROP INDEX IF EXISTS {idx.name}")


def _literal(value: str) -> str:
    """A single-quoted PG string literal for a probe query."""
    return "'" + value.replace("'", "''") + "'"
