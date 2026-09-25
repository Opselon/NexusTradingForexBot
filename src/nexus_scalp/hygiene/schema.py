"""Schema for the operational tables the hygiene state + quarantine stores
own (DB-FABRIC-002).

``hygiene_worker_state`` / ``hygiene_run_history`` come from
``HygieneStateStore`` and ``quarantine_items`` / ``quarantine_events`` from
``QuarantineStore`` — SQLite-only paths until now (the stores open their own
dedicated SQLite files under ``<root>/archive/_hygiene_state`` and
``<root>/archive/_quarantine``). Under PostgreSQL those tables had no
authored DDL, so the fabric could not provision them and the stores' writes
silently went nowhere.

This module authors that DDL once, in the SQLite dialect, mirroring the
ownership contract of ``model_lifecycle.schema`` and ``shadow.schema``: the
statements the stores' own schema constants carry, so the fabric's
provisioner and the SQLite bootstrap converge on the same physical schema.
``IF NOT EXISTS`` keeps re-provisioning non-destructive.

Note: ``"database"`` and ``"table"`` are reserved words — every reference to
those COLUMN names is double-quoted in the SQL text (the same care the
SQLite path takes).
"""

from __future__ import annotations

_HYGIENE_WORKER_STATE_DDL = """
CREATE TABLE IF NOT EXISTS hygiene_worker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL,
    mode TEXT NOT NULL,
    cycle INTEGER NOT NULL DEFAULT 0,
    last_scan TEXT DEFAULT '',
    last_cleanup TEXT DEFAULT '',
    last_success TEXT DEFAULT '',
    last_failure TEXT DEFAULT '',
    stats TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL
);
"""

_HYGIENE_RUN_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS hygiene_run_history (
    run_id TEXT PRIMARY KEY,
    database TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT DEFAULT '',
    duration_ms REAL DEFAULT 0.0,
    mode TEXT NOT NULL,
    rows_scanned INTEGER DEFAULT 0,
    duplicates_found INTEGER DEFAULT 0,
    orphans_found INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    errors TEXT DEFAULT '',
    bytes_freed INTEGER DEFAULT 0,
    verification_status TEXT DEFAULT '',
    correlation_id TEXT DEFAULT '',
    plan_json TEXT DEFAULT '{}'
);
"""

_QUARANTINE_ITEMS_DDL = """
CREATE TABLE IF NOT EXISTS quarantine_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quarantine_id TEXT UNIQUE NOT NULL,
    "database" TEXT NOT NULL,
    "table" TEXT NOT NULL,
    row_id TEXT NOT NULL,
    row_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    found_by TEXT NOT NULL DEFAULT '',
    cleanup_class TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'QUARANTINED',
    detected_at TEXT NOT NULL,
    resolved_at TEXT DEFAULT '',
    resolved_action TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);
"""

_QUARANTINE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS quarantine_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quarantine_id TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT DEFAULT '',
    at TEXT NOT NULL
);
"""

_STATEMENTS: tuple[str, ...] = (
    _HYGIENE_WORKER_STATE_DDL,
    _HYGIENE_RUN_HISTORY_DDL,
    _QUARANTINE_ITEMS_DDL,
    _QUARANTINE_EVENTS_DDL,
    "CREATE INDEX IF NOT EXISTS idx_quarantine_status ON quarantine_items(status);",
    'CREATE INDEX IF NOT EXISTS idx_quarantine_db_table ON quarantine_items("database", "table");',
    "CREATE INDEX IF NOT EXISTS idx_quarantine_events_id ON quarantine_events(quarantine_id);",
)

#: The tables this domain provisions (the fabric's ``verify_domain_schema``
#: reference). Must stay in sync with ``HYGIENE_TABLES`` in ops_provider.
TABLES: frozenset[str] = frozenset(
    {
        "hygiene_worker_state",
        "hygiene_run_history",
        "quarantine_items",
        "quarantine_events",
    }
)


def ops_hygiene_schema_statements() -> tuple[str, ...]:
    """The hygiene state + quarantine tables, in the SQLite dialect.

    The statements are also the SQLite bootstrap path: the stores' own
    ``_SCHEMA`` constants carry the same DDL, so a SQLite database converges
    to the same shape whether it is created by the store or by the fabric.
    """
    return _STATEMENTS
