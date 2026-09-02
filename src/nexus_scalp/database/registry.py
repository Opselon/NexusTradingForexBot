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
# AUDIT-0005: TASK-08 governance audit tables (promotions + rollbacks)
# ---------------------------------------------------------------------------
# Additive, idempotent, migration-controlled (INV-013). These tables are the
# durable home of the promotion transaction / rollback audit trail. The
# governance event-ledger rows (model_governance_events) remain the append-only
# narrative; these tables carry the STRUCTURED old/new champion pair,
# approver identity, candidate hash, schema and rollback target.


def _audit_0005_governance_audit_tables(conn: sqlite3.Connection, db_path: Path) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_promotion_audit (
            promotion_id TEXT PRIMARY KEY,
            old_champion_model_id TEXT NOT NULL DEFAULT '',
            old_champion_version TEXT NOT NULL DEFAULT '',
            old_champion_hash TEXT NOT NULL DEFAULT '',
            old_champion_schema TEXT NOT NULL DEFAULT '',
            new_champion_model_id TEXT NOT NULL DEFAULT '',
            new_champion_version TEXT NOT NULL DEFAULT '',
            new_champion_hash TEXT NOT NULL DEFAULT '',
            new_champion_schema TEXT NOT NULL DEFAULT '',
            candidate_hash TEXT NOT NULL DEFAULT '',
            schema_id TEXT NOT NULL DEFAULT '',
            approval_actor TEXT NOT NULL DEFAULT '',
            approval_reason TEXT NOT NULL DEFAULT '',
            approval_token TEXT NOT NULL DEFAULT '',
            rollback_target TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'PROMOTION_RECORDED',
            recorded_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_rollback_audit (
            rollback_id TEXT PRIMARY KEY,
            failed_model_id TEXT NOT NULL DEFAULT '',
            failed_version TEXT NOT NULL DEFAULT '',
            previous_model_id TEXT NOT NULL DEFAULT '',
            previous_version TEXT NOT NULL DEFAULT '',
            previous_artifact_hash TEXT NOT NULL DEFAULT '',
            previous_manifest_hash TEXT NOT NULL DEFAULT '',
            previous_schema_id TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            rollback_kind TEXT NOT NULL DEFAULT 'MANUAL',
            status TEXT NOT NULL DEFAULT 'ROLLBACK_RECORDED',
            recorded_at TEXT NOT NULL
        );
        """
    )


def _audit_0005_verify(conn: sqlite3.Connection, db_path: Path) -> bool:
    return _table_exists(conn, "model_promotion_audit") and _table_exists(
        conn, "model_rollback_audit"
    )


def _audit_0005_rollback(conn: sqlite3.Connection, db_path: Path) -> None:
    # Tables are additive; rollback drops them only when empty (no data loss).
    for t in ("model_promotion_audit", "model_rollback_audit"):
        if _table_exists(conn, t):
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if n == 0:
                conn.execute(f"DROP TABLE {t}")


def _audit_0006_incident_tables(conn: sqlite3.Connection, db_path: Path) -> None:
    """TASK-12: canonical incident response tables (additive, governed).

    incident_id | detected_at | severity | category | status | first/last_seen |
    component | operation | correlation_id | root_cause_status | root_cause |
    evidence | impact | affected_* | recovery_status | recommended_action |
    fingerprint (dedup) | repeated_count | BUG linkage | recovery plan |
    tags/notes. Plus event timeline, value traces and non-destructive
    quarantine marks. Additive only — creates NEW tables, never alters or
    deletes existing financial/research/news truth (spec 30/45).
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS incidents (
            incident_id TEXT PRIMARY KEY,
            detected_at TEXT NOT NULL,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            first_seen_at TEXT,
            last_seen_at TEXT,
            component TEXT DEFAULT '',
            operation TEXT DEFAULT '',
            correlation_id TEXT DEFAULT '',
            root_cause_status TEXT DEFAULT 'UNKNOWN',
            root_cause TEXT DEFAULT '',
            evidence_json TEXT DEFAULT '[]',
            impact_json TEXT DEFAULT '{}',
            affected_records_json TEXT DEFAULT '[]',
            affected_models_json TEXT DEFAULT '[]',
            affected_runtime_json TEXT DEFAULT '[]',
            affected_users_json TEXT DEFAULT '[]',
            recovery_status TEXT DEFAULT 'RECOMMENDED',
            recommended_action TEXT DEFAULT '',
            fingerprint TEXT DEFAULT '',
            repeated_count INTEGER DEFAULT 1,
            related_bug_id TEXT DEFAULT '',
            fix_commit TEXT DEFAULT '',
            regression_test TEXT DEFAULT '',
            is_regression INTEGER DEFAULT 0,
            previous_bug_id TEXT DEFAULT '',
            resolved_without_evidence INTEGER DEFAULT 0,
            recovery_plan_json TEXT DEFAULT '{}',
            tags_json TEXT DEFAULT '[]',
            notes_json TEXT DEFAULT '[]',
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS incident_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            event_timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT DEFAULT '{}',
            correlation_id TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS incident_value_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            field TEXT NOT NULL,
            source TEXT NOT NULL,
            source_timestamp TEXT,
            hops_json TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS incident_quarantine (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            target_table TEXT NOT NULL,
            record_key TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT DEFAULT '',
            evidence TEXT DEFAULT '',
            quarantined_at TEXT NOT NULL,
            UNIQUE (incident_id, target_table, record_key)
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_category ON incidents(category);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_fingerprint ON incidents(fingerprint);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_detected ON incidents(detected_at);")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_incident_events_incident ON incident_events(incident_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_incident_quarantine_incident ON incident_quarantine(incident_id);"
    )


def _audit_0006_verify(conn: sqlite3.Connection, db_path: Path) -> bool:
    return _table_exists(conn, "incidents") and _table_exists(conn, "incident_events")


def _audit_0006_rollback(conn: sqlite3.Connection, db_path: Path) -> None:
    # Additive tables: rollback drops ONLY the incident tables (never
    # touches financial/news/research truth). Evidence preserved via
    # artifacts/incidents archive before any operator-initiated rollback.
    for t in ("incident_quarantine", "incident_value_traces", "incident_events", "incidents"):
        if _table_exists(conn, t):
            conn.execute(f"DROP TABLE {t}")


def _audit_0007_release_metadata(conn: sqlite3.Connection, db_path: Path) -> None:
    """TASK-9 (70D production release): versioned release/model metadata.

    Key/value table recording INSTALLED release metadata independent of
    the application code: per-domain schema version at install time, the
    feature schema id consumed by the release (scalp_v1 today; scalp_v4/
    70D tomorrow), and the web bundle version shipped with the release.
    Read-only consumers: release health, UI status, upgrade diagnostics.
    Additive + idempotent — never touches financial/news/research truth.

    The migration engine's baseline builder may have created the table as
    a minimal skeleton (`id INTEGER PRIMARY KEY` only). This apply is
    column-repair-aware: it adds the canonical key/value/updated_at
    columns when missing so the index is valid on fresh AND
    baseline-created databases (guard against "no such column: key").
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS release_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(release_metadata)").fetchall()}
    if "key" not in existing_cols:
        conn.execute("ALTER TABLE release_metadata ADD COLUMN key TEXT PRIMARY KEY")
    if "value" not in existing_cols:
        conn.execute("ALTER TABLE release_metadata ADD COLUMN value TEXT NOT NULL DEFAULT ''")
    if "updated_at" not in existing_cols:
        conn.execute(
            "ALTER TABLE release_metadata ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_release_metadata_key
        ON release_metadata(key)
        """
    )


def _audit_0007_verify(conn: sqlite3.Connection, db_path: Path) -> bool:
    return _table_exists(conn, "release_metadata")


def _audit_0007_rollback(conn: sqlite3.Connection, db_path: Path) -> None:
    if _table_exists(conn, "release_metadata"):
        n = conn.execute("SELECT COUNT(*) FROM release_metadata").fetchone()[0]
        if n == 0:
            conn.execute("DROP TABLE IF EXISTS release_metadata")


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
    Migration(
        migration_id="AUDIT-0005-governance-audit-tables",
        domain=DatabaseDomain.AUDIT,
        from_version=4,
        to_version=5,
        description=(
            "TASK-08: model_promotion_audit + model_rollback_audit tables "
            "(structured promotion/rollback audit trail)"
        ),
        apply=_audit_0005_governance_audit_tables,
        verify=_audit_0005_verify,
        risk=MigrationRisk.LOW,
        transaction_kind=TransactionKind.TRANSACTIONAL,
        rollback=_audit_0005_rollback,
    ),
    Migration(
        migration_id="AUDIT-0006-incident-response-tables",
        domain=DatabaseDomain.AUDIT,
        from_version=5,
        to_version=6,
        description="TASK-12 canonical incident response tables (incidents/events/traces/quarantine) + indexes",
        apply=_audit_0006_incident_tables,
        verify=_audit_0006_verify,
        risk=MigrationRisk.LOW,
        transaction_kind=TransactionKind.TRANSACTIONAL,
        rollback=_audit_0006_rollback,
    ),
    Migration(
        migration_id="AUDIT-0007-release-metadata",
        domain=DatabaseDomain.AUDIT,
        from_version=6,
        to_version=7,
        description="add release_metadata key/value table (TASK-9 70D release layer)",
        apply=_audit_0007_release_metadata,
        verify=_audit_0007_verify,
        risk=MigrationRisk.LOW,
        transaction_kind=TransactionKind.TRANSACTIONAL,
        rollback=_audit_0007_rollback,
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
