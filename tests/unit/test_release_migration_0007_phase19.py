"""TASK-9 (TASK-09-70D-PRODUCTION-RELEASE) — AUDIT-0007 release_metadata
migration tests (TEST-REL-03/04/05 mapping).

Covers:
    TEST-REL-03  database backup before migration (engine backup event)
    TEST-REL-04  migration idempotent (second run = nothing to do)
    TEST-REL-05  migration integrity check (PRAGMA integrity_check + FK)
    brief §4     versioned, additive, auditable migration

Run: .venv/Scripts/python.exe -m pytest tests/unit/test_release_migration_0007_phase19.py -q
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from nexus_scalp.database.engine import DatabaseMigrationEngine
from nexus_scalp.database.models import DatabaseDomain


def _make_old_audit_db(path: Path) -> None:
    """A minimal v6-shaped audit DB: ledger + schema_meta + governance tables."""
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE audit_ledger (
            ticket INTEGER, symbol TEXT, direction TEXT, volume REAL,
            entry_price REAL, exit_price REAL, status TEXT, pnl REAL,
            net_pnl_usd REAL DEFAULT 0.0, close_time TEXT DEFAULT '',
            exit_reason_source TEXT DEFAULT '', exit_evidence TEXT DEFAULT '',
            exit_reason_confidence REAL DEFAULT 0.0
        );
        CREATE TABLE schema_meta (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE schema_migrations (
            migration_id TEXT PRIMARY KEY, domain TEXT, version INTEGER,
            description TEXT, checksum TEXT, applied_at TEXT,
            application_version TEXT, git_commit TEXT, execution_ms INTEGER,
            status TEXT
        );
        CREATE TABLE model_promotion_audit (
            promotion_id TEXT PRIMARY KEY, model_id TEXT DEFAULT ''
        );
        CREATE TABLE model_rollback_audit (
            rollback_id TEXT PRIMARY KEY, model_id TEXT DEFAULT ''
        );
        CREATE TABLE incidents (
            incident_id TEXT PRIMARY KEY
        );
        CREATE TABLE incident_events (
            event_id TEXT PRIMARY KEY
        );
        """
    )
    con.execute("INSERT INTO audit_ledger (ticket, pnl, net_pnl_usd) VALUES (1, -25.5, -25.5)")
    con.execute("INSERT INTO audit_ledger (ticket, pnl, net_pnl_usd) VALUES (2, 40.0, 40.0)")
    con.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', '6')")
    # v6 means 0005/0006 were applied — record honest history rows so the
    # engine plans only AUDIT-0007.
    for mid, ver in (("AUDIT-0002-add-audit-orders-ticket-index", 2),
                     ("AUDIT-0003-ledger-exit-evidence-columns", 3),
                     ("AUDIT-0004-ledger-close-time-index", 4),
                     ("AUDIT-0005-governance-audit-tables", 5),
                     ("AUDIT-0006-incident-response-tables", 6)):
        con.execute(
            "INSERT INTO schema_migrations (migration_id, domain, version, status) "
            "VALUES (?, 'audit', ?, 'applied')",
            (mid, ver),
        )
    con.commit()
    con.close()


def test_audit_0007_applies_and_is_idempotent(tmp_path: Path) -> None:
    """TEST-REL-04: apply -> SUCCEEDED with release_metadata; second run -> NOT_REQUIRED."""
    db = tmp_path / "audit.db"
    _make_old_audit_db(db)
    eng = DatabaseMigrationEngine(db, DatabaseDomain.AUDIT)
    res = eng.migrate()
    assert res.get("state") == "DB_MIGRATION_SUCCEEDED"
    assert "AUDIT-0007-release-metadata" in res.get("applied", [])
    assert eng.current_version() == 7
    assert eng.expected_version() == 7
    # idempotency
    res2 = eng.migrate()
    assert res2.get("state") == "DB_MIGRATION_NOT_REQUIRED"
    assert eng.current_version() == 7


def test_audit_0007_release_metadata_table_shape(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _make_old_audit_db(db)
    eng = DatabaseMigrationEngine(db, DatabaseDomain.AUDIT)
    eng.migrate()
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(release_metadata)")}
    assert {"key", "value", "updated_at"} <= cols
    assert (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_release_metadata_key'"
        ).fetchone()
        is not None
    )
    con.close()


def test_audit_0007_integrity_and_financial_preserved(tmp_path: Path) -> None:
    """TEST-REL-03/05: backup event recorded; integrity ok; ledger rows and PnL unchanged."""
    db = tmp_path / "audit.db"
    _make_old_audit_db(db)
    eng = DatabaseMigrationEngine(db, DatabaseDomain.AUDIT)
    res = eng.migrate()
    # backup path recorded in the result
    backup = res.get("backup") or res.get("backup_path") or ""
    assert backup, "backup path must be recorded"
    # financial invariants
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0] == 2
    pnl = con.execute("SELECT COALESCE(SUM(net_pnl_usd),0) FROM audit_ledger").fetchone()[0]
    assert abs(pnl - 14.5) < 1e-9
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    # migration history row
    row = con.execute(
        "SELECT migration_id, status FROM schema_migrations WHERE migration_id LIKE 'AUDIT-0007%'"
    ).fetchone()
    assert row and row[1] == "applied"
    con.close()


def test_audit_0007_already_current_no_op(tmp_path: Path) -> None:
    """A v7 DB (release_metadata present) must be a no-op."""
    db = tmp_path / "audit.db"
    _make_old_audit_db(db)
    eng = DatabaseMigrationEngine(db, DatabaseDomain.AUDIT)
    eng.migrate()
    # run again → nothing pending
    plan = eng.plan()
    assert eng.current_version() == eng.expected_version() == 7
    assert plan.get("pending") == []
