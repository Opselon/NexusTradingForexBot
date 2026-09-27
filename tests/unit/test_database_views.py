"""Tests for Analytics and Operational Database Views.

Enforces Section 28 of the Dual Database Architecture:
    - Pre-aggregates repeated complex queries into standardized database views.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers import SQLiteDriver
from nexus_scalp.database.views import ensure_analytics_views


def test_analytics_views_creation_and_query() -> None:
    """Verifies that analytics views are created idempotently and return valid query results."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "views_test.db"
        cfg = DatabaseConfig.for_sqlite("audit", path=str(db_path))
        driver = SQLiteDriver(cfg)

        # Create underlying base tables
        driver.execute(
            """
            CREATE TABLE audit_ledger (
                id INTEGER PRIMARY KEY,
                ticket INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT,
                volume REAL,
                open_price REAL,
                close_price REAL,
                pnl REAL NOT NULL,
                commission REAL DEFAULT 0.0,
                swap REAL DEFAULT 0.0,
                open_time TEXT,
                close_time TEXT,
                duration_sec REAL DEFAULT 0.0,
                exit_reason TEXT,
                status TEXT NOT NULL
            )
            """
        )
        driver.execute(
            """
            CREATE TABLE audit_signals (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                decision_stage TEXT,
                blocked_by TEXT,
                generated_at TEXT NOT NULL
            )
            """
        )
        driver.execute(
            """
            CREATE TABLE audit_account_snapshots (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                balance REAL NOT NULL,
                equity REAL NOT NULL,
                margin REAL NOT NULL,
                margin_free REAL NOT NULL,
                margin_level REAL NOT NULL,
                floating_pnl REAL NOT NULL,
                closed_pnl REAL NOT NULL
            )
            """
        )

        # Populate sample data
        driver.execute(
            "INSERT INTO audit_account_snapshots (id, timestamp, balance, equity, margin, margin_free, margin_level, floating_pnl, closed_pnl) "
            "VALUES (1, '2026-09-27T10:00:00Z', 10000.0, 10050.0, 100.0, 9950.0, 1005.0, 50.0, 0.0)"
        )
        driver.execute(
            "INSERT INTO audit_account_snapshots (id, timestamp, balance, equity, margin, margin_free, margin_level, floating_pnl, closed_pnl) "
            "VALUES (2, '2026-09-27T10:05:00Z', 10050.0, 10040.0, 100.0, 9940.0, 1004.0, 40.0, 50.0)"
        )

        # Populate sample data
        driver.execute(
            "INSERT INTO audit_ledger (id, ticket, symbol, pnl, status) VALUES (1, 1001, 'EURUSD', 50.0, 'CLOSED')"
        )
        driver.execute(
            "INSERT INTO audit_ledger (id, ticket, symbol, pnl, status) VALUES (2, 1002, 'EURUSD', -10.0, 'CLOSED')"
        )
        driver.execute(
            "INSERT INTO audit_signals (id, symbol, action, confidence, decision_stage, blocked_by, generated_at) "
            "VALUES (1, 'EURUSD', 'BUY', 0.9, 'PROMOTED', NULL, '2026-09-27T10:00:00Z')"
        )

        # Ensure views
        created = ensure_analytics_views(driver)
        assert len(created) >= 3

        # Query v_equity_curve
        rows = driver.query("SELECT * FROM v_equity_curve")
        assert len(rows) == 2
        assert rows[0]["floating_pnl"] == 50.0

        # Query v_trade_timeline
        timeline = driver.query("SELECT * FROM v_trade_timeline")
        assert len(timeline) == 2

        driver.close()
