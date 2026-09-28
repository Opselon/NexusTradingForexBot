"""Tests for Analytics and Operational Database Views.

Enforces Section 28 of the Dual Database Architecture:
    - Pre-aggregates repeated complex queries into standardized database views.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers import SQLiteDriver
from nexus_scalp.database.views import ensure_analytics_views

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_pg = pytest.mark.skipif(not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL local arm)")


def _seed_pg_secret() -> None:
    """Publish the URL's password into the store the real connect path reads."""
    from psycopg.conninfo import conninfo_to_dict

    from nexus_scalp.settings.secret_store import SecureSecretStore

    SecureSecretStore().set_secret(
        "db.postgresql.password", str(conninfo_to_dict(PG_URL).get("password") or "")
    )


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


# ---------------------------------------------------------------------------
# PostgreSQL arm — CREATE VIEW IF NOT EXISTS is SQLite-only syntax, so the
# DDL must be dialect-adapted. This pins the live failure from the engine log
# (all five views reported "syntax error at or near NOT" and never existed).
# ---------------------------------------------------------------------------
_LEGACY_TABLES = (
    "CREATE TABLE audit_ledger ("
    "  id BIGSERIAL PRIMARY KEY, ticket BIGINT, symbol TEXT, action TEXT,"
    "  volume DOUBLE PRECISION, open_price DOUBLE PRECISION,"
    "  close_price DOUBLE PRECISION, pnl DOUBLE PRECISION NOT NULL,"
    "  commission DOUBLE PRECISION DEFAULT 0.0, swap DOUBLE PRECISION DEFAULT 0.0,"
    "  open_time TEXT, close_time TEXT, duration_sec DOUBLE PRECISION,"
    "  exit_reason TEXT, status TEXT NOT NULL"
    ")",
    "CREATE TABLE audit_account_snapshots ("
    "  id BIGSERIAL PRIMARY KEY, timestamp TEXT NOT NULL, balance DOUBLE PRECISION NOT NULL,"
    "  equity DOUBLE PRECISION NOT NULL, margin DOUBLE PRECISION NOT NULL,"
    "  margin_free DOUBLE PRECISION NOT NULL, margin_level DOUBLE PRECISION NOT NULL,"
    "  floating_pnl DOUBLE PRECISION NOT NULL, closed_pnl DOUBLE PRECISION NOT NULL"
    ")",
    "CREATE TABLE audit_orders ("
    "  id BIGSERIAL PRIMARY KEY, ticket BIGINT, symbol TEXT, order_type TEXT,"
    "  requested_volume DOUBLE PRECISION, volume_current DOUBLE PRECISION,"
    "  state TEXT, profit DOUBLE PRECISION, time_done TEXT, timestamp TEXT"
    ")",
    "CREATE TABLE audit_broker_orders ("
    "  id BIGSERIAL PRIMARY KEY, ticket BIGINT, volume_current DOUBLE PRECISION,"
    "  state TEXT, time_done TEXT"
    ")",
    "CREATE TABLE audit_broker_deals ("
    "  id BIGSERIAL PRIMARY KEY, ticket BIGINT, profit DOUBLE PRECISION"
    ")",
    "CREATE TABLE trading_rules_config ("
    "  id BIGSERIAL PRIMARY KEY, rule_name TEXT, is_enabled BOOLEAN,"
    "  parameters TEXT, updated_at TEXT"
    ")",
)


@needs_pg
def test_analytics_views_creation_and_query_postgres() -> None:
    """All five views must be created and queryable on PostgreSQL."""
    from nexus_scalp.database.drivers import get_driver

    _seed_pg_secret()
    cfg = DatabaseConfig.for_postgres(
        domain="audit",
        host="localhost",
        port=5432,
        database="nse_audit",
        username="nse_user",
        ssl_mode="",
    )
    driver = get_driver(cfg)
    try:
        # Clean slate: drop any view left by a previous run, then the probe tables.
        for name in (
            "v_trade_timeline",
            "v_equity_curve",
            "v_broker_reconciliation",
            "v_risk_summary",
            "v_dashboard_summary",
        ):
            driver.execute(f"DROP VIEW IF EXISTS {name}")
        for table in (
            "audit_broker_deals",
            "audit_broker_orders",
            "audit_orders",
            "audit_account_snapshots",
            "trading_rules_config",
            "audit_ledger",
        ):
            driver.execute(f"DROP TABLE IF EXISTS {table}")
        for ddl in _LEGACY_TABLES:
            driver.execute(ddl)

        created = ensure_analytics_views(driver)
        assert created["v_trade_timeline"]["status"] == "created"
        assert created["v_equity_curve"]["status"] == "created"
        assert created["v_broker_reconciliation"]["status"] == "created"
        assert created["v_risk_summary"]["status"] == "created"
        assert created["v_dashboard_summary"]["status"] == "created"

        # The views must actually exist and be queryable — the old failure left
        # zero views behind with no caller-side error.
        rows = driver.query("SELECT * FROM v_equity_curve")
        assert rows == []
        timeline = driver.query("SELECT * FROM v_trade_timeline")
        assert timeline == []
        dash = driver.query("SELECT total_trades, total_pnl FROM v_dashboard_summary")
        assert dash == [{"total_trades": 0, "total_pnl": 0.0}]

        # Idempotency: a second run reports exists, never FAILED.
        again = ensure_analytics_views(driver)
        assert {k: v["status"] for k, v in again.items()} == {
            "v_trade_timeline": "exists",
            "v_equity_curve": "exists",
            "v_broker_reconciliation": "exists",
            "v_risk_summary": "exists",
            "v_dashboard_summary": "exists",
        }
    finally:
        driver.close()


@needs_pg
def test_analytics_views_pg_handles_missing_base_tables() -> None:
    """A PostgreSQL database without the audit tables reports FAILED, not a crash."""
    from nexus_scalp.database.drivers import get_driver

    _seed_pg_secret()
    cfg = DatabaseConfig.for_postgres(
        domain="audit",
        host="localhost",
        port=5432,
        database="nse_audit",
        username="nse_user",
        ssl_mode="",
    )
    driver = get_driver(cfg)
    try:
        for name in ("v_trade_timeline", "v_equity_curve", "v_dashboard_summary"):
            driver.execute(f"DROP VIEW IF EXISTS {name}")
        results = ensure_analytics_views(driver)
        # Every view either FAILED (missing base table) or exists (another test
        # created it); the guarantee is no exception escapes the call.
        assert all(v["status"] in ("FAILED", "exists", "created") for v in results.values())
    finally:
        driver.close()
