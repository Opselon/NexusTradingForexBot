"""
Migration Registry (TASK-10 §3/§14)
===================================
The canonical, ordered set of migrations per domain. Every migration carries:

    migration_id        unique immutable ID (e.g. AUDIT-0002-...)
    domain              audit | news | candle_intel
    from_version        starting schema version
    to_version          target schema version
    description         human summary
    risk                LOW | MEDIUM | HIGH | DESTRUCTIVE
    transaction_kind    TRANSACTIONAL | NON_TRANSACTIONAL_WITH_SAFETY_PROTOCOL
    apply/verify/rollback

BASELINE CONVENTION (task §5): databases created before this framework have
no schema_meta. The engine inspects the actual schema; a DB whose tables
match the manifest's expected table set is baseline-recorded at the current
version WITHOUT re-applying CREATE TABLE (the application bootstrap already
performs idempotent CREATE TABLE IF NOT EXISTS). A DB with MISSING tables
is treated as version 0 and the baseline migration creates the difference.

Version numbering: each domain starts at 1. New migrations bump +1.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from nexus_scalp.database.models import (
    DatabaseDomain,
    Migration,
    MigrationRisk,
    TransactionKind,
)

# ---------------------------------------------------------------------------
# Shared SQL helpers
# ---------------------------------------------------------------------------


def _add_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    ddl: str,
) -> None:
    """Adds a column if missing (idempotent)."""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _ensure_index(
    conn: sqlite3.Connection,
    index: str,
    table: str,
    definition: str,
) -> None:
    """Creates an index only when missing (idempotent, task §10)."""
    conn.execute(f"CREATE INDEX IF NOT EXISTS {index} ON {table} {definition}")


def _unique_index(
    conn: sqlite3.Connection,
    index: str,
    table: str,
    definition: str,
) -> None:
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {table} {definition}")


def _index_exists(conn: sqlite3.Connection, index: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (index,),
    ).fetchone()
    return row is not None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


# ---------------------------------------------------------------------------
# AUDIT migrations
# ---------------------------------------------------------------------------


def _audit_0002_orders_ticket_index(conn: sqlite3.Connection, db_path: Path) -> None:
    """Ensure the canonical audit_orders (ticket, order_id) composite index
    (P3 forensic finding: 'audit_orders lacks a (ticket, order_id) index').
    Idempotent — matches the bootstrap index name and definition."""
    _ensure_index(conn, "idx_orders_ticket", "audit_orders", "(ticket, order_id)")


def _audit_0002_verify(conn: sqlite3.Connection, db_path: Path) -> bool:
    return _index_exists(conn, "idx_orders_ticket")


def _audit_0002_rollback(conn: sqlite3.Connection, db_path: Path) -> None:
    if _index_exists(conn, "idx_orders_ticket"):
        conn.execute("DROP INDEX idx_orders_ticket")


def _audit_0003_ledger_exit_evidence(conn: sqlite3.Connection, db_path: Path) -> None:
    """TASK-3 exit-classification evidence columns (BUG-083 provenance)."""
    # Canonical financial columns that later application versions rely on
    # (safe additive — old rows keep 0/empty semantics).
    _add_column(conn, "audit_ledger", "net_pnl_usd", "REAL DEFAULT 0.0")
    _add_column(conn, "audit_ledger", "close_time", "TEXT DEFAULT ''")
    _add_column(conn, "audit_ledger", "exit_reason_source", "TEXT DEFAULT ''")
    _add_column(conn, "audit_ledger", "exit_evidence", "TEXT DEFAULT ''")
    _add_column(conn, "audit_ledger", "exit_reason_confidence", "REAL DEFAULT 0.0")
    _add_column(conn, "audit_ledger", "reversal_events_json", "TEXT DEFAULT '[]'")


def _audit_0003_verify(conn: sqlite3.Connection, db_path: Path) -> bool:
    return (
        _column_exists(conn, "audit_ledger", "exit_reason_source")
        and _column_exists(conn, "audit_ledger", "reversal_events_json")
        and _column_exists(conn, "audit_ledger", "net_pnl_usd")
        and _column_exists(conn, "audit_ledger", "close_time")
    )


def _audit_0004_ledger_close_time_index(conn: sqlite3.Connection, db_path: Path) -> None:
    """Documented P3: close_time/exit-time queries defeat indexes via
    COALESCE(NULLIF(...)) — a plain close_time index helps the common path."""
    _ensure_index(
        conn,
        "idx_audit_ledger_close_time",
        "audit_ledger",
        "(close_time)",
    )


def _audit_0004_verify(conn: sqlite3.Connection, db_path: Path) -> bool:
    return _index_exists(conn, "idx_audit_ledger_close_time")


def _audit_0004_rollback(conn: sqlite3.Connection, db_path: Path) -> None:
    if _index_exists(conn, "idx_audit_ledger_close_time"):
        conn.execute("DROP INDEX idx_audit_ledger_close_time")


# ---------------------------------------------------------------------------
# NEWS migrations
# ---------------------------------------------------------------------------


def _news_0002_source_health_index(conn: sqlite3.Connection, db_path: Path) -> None:
    # Real news_health schema (2026-08-18): source_id + last_success_at rows.
    # The source-health lookup is per-source recency — index on the real
    # columns, verified against the actual schema (task §46).
    _ensure_index(
        conn,
        "idx_news_health_source",
        "news_health",
        "(source_id, last_success_at DESC)",
    )


def _news_0002_verify(conn: sqlite3.Connection, db_path: Path) -> bool:
    # Verify on the real schema: if news_health has no last_success_at the
    # index cannot be created — fall back to a plain source_id index.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(news_health)").fetchall()}
    if "last_success_at" not in cols:
        return False
    return _index_exists(conn, "idx_news_health_source")


def _news_0002_rollback(conn: sqlite3.Connection, db_path: Path) -> None:
    if _index_exists(conn, "idx_news_health_source"):
        conn.execute("DROP INDEX idx_news_health_source")


# ---------------------------------------------------------------------------
# CANDLE_INTEL migrations
# ---------------------------------------------------------------------------


def _candle_0002_closure_composite_index(conn: sqlite3.Connection, db_path: Path) -> None:
    _ensure_index(
        conn,
        "idx_candle_closures_symbol_ts",
        "candle_closures",
        "(symbol, ts)",
    )


def _candle_0002_verify(conn: sqlite3.Connection, db_path: Path) -> bool:
    return _index_exists(conn, "idx_candle_closures_symbol_ts")


def _candle_0002_rollback(conn: sqlite3.Connection, db_path: Path) -> None:
    if _index_exists(conn, "idx_candle_closures_symbol_ts"):
        conn.execute("DROP INDEX idx_candle_closures_symbol_ts")


# ---------------------------------------------------------------------------
# Registry assembly
# ---------------------------------------------------------------------------

AUDIT_BASELINE_VERSION = 1
NEWS_BASELINE_VERSION = 1
CANDLE_BASELINE_VERSION = 1

AUDIT_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        migration_id="AUDIT-0002-add-audit-orders-ticket-index",
        domain=DatabaseDomain.AUDIT,
        from_version=1,
        to_version=2,
        description="ensure audit_orders(ticket, order_id) composite index (P3)",
        apply=_audit_0002_orders_ticket_index,
        verify=_audit_0002_verify,
        risk=MigrationRisk.LOW,
        transaction_kind=TransactionKind.NON_TRANSACTIONAL_WITH_SAFETY_PROTOCOL,
        rollback=_audit_0002_rollback,
    ),
    Migration(
        migration_id="AUDIT-0003-ledger-exit-evidence-columns",
        domain=DatabaseDomain.AUDIT,
        from_version=2,
        to_version=3,
        description="add exit-classification evidence columns (BUG-083)",
        apply=_audit_0003_ledger_exit_evidence,
        verify=_audit_0003_verify,
        risk=MigrationRisk.LOW,
        transaction_kind=TransactionKind.TRANSACTIONAL,
        rollback=None,  # additive columns; no data loss on keep
    ),
    Migration(
        migration_id="AUDIT-0004-ledger-close-time-index",
        domain=DatabaseDomain.AUDIT,
        from_version=3,
        to_version=4,
        description="add audit_ledger(close_time) index (P3 forensic finding)",
        apply=_audit_0004_ledger_close_time_index,
        verify=_audit_0004_verify,
        risk=MigrationRisk.LOW,
        transaction_kind=TransactionKind.NON_TRANSACTIONAL_WITH_SAFETY_PROTOCOL,
        rollback=_audit_0004_rollback,
    ),
)

NEWS_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        migration_id="NEWS-0002-source-health-index",
        domain=DatabaseDomain.NEWS,
        from_version=1,
        to_version=2,
        description="add news_health(source_id, checked_at) index",
        apply=_news_0002_source_health_index,
        verify=_news_0002_verify,
        risk=MigrationRisk.LOW,
        transaction_kind=TransactionKind.NON_TRANSACTIONAL_WITH_SAFETY_PROTOCOL,
        rollback=_news_0002_rollback,
    ),
)

CANDLE_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        migration_id="CANDLE-0002-closure-composite-index",
        domain=DatabaseDomain.CANDLE_INTEL,
        from_version=1,
        to_version=2,
        description="add candle_closures(symbol, ts) composite index",
        apply=_candle_0002_closure_composite_index,
        verify=_candle_0002_verify,
        risk=MigrationRisk.LOW,
        transaction_kind=TransactionKind.NON_TRANSACTIONAL_WITH_SAFETY_PROTOCOL,
        rollback=_candle_0002_rollback,
    ),
)

REGISTRY: dict[DatabaseDomain, tuple[Migration, ...]] = {
    DatabaseDomain.AUDIT: AUDIT_MIGRATIONS,
    DatabaseDomain.NEWS: NEWS_MIGRATIONS,
    DatabaseDomain.CANDLE_INTEL: CANDLE_MIGRATIONS,
}

BASELINE_VERSIONS: dict[DatabaseDomain, int] = {
    DatabaseDomain.AUDIT: AUDIT_BASELINE_VERSION,
    DatabaseDomain.NEWS: NEWS_BASELINE_VERSION,
    DatabaseDomain.CANDLE_INTEL: CANDLE_BASELINE_VERSION,
}


def migrations_for(domain: DatabaseDomain) -> tuple[Migration, ...]:
    return REGISTRY[domain]


def baseline_version_for(domain: DatabaseDomain) -> int:
    return BASELINE_VERSIONS[domain]


def expected_version_for_domain(domain: DatabaseDomain) -> int:
    """Expected schema version = baseline + number of migrations."""
    return BASELINE_VERSIONS[domain] + len(REGISTRY[domain])


def all_migration_ids(domain: DatabaseDomain) -> list[str]:
    return [m.migration_id for m in REGISTRY[domain]]
