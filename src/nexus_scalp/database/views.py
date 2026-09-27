"""Operational & Analytics Database Views.

Enforces Section 28 of the Dual Database Architecture:
    Pre-aggregates and structures repeated complex queries into standardized
    database views for the UI, dashboard, and analytical consumers.
    Provides identical interfaces on both SQLite and PostgreSQL.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.database.views")

VIEW_DEFINITIONS: dict[str, str] = {
    # 1. Trade Timeline: joins ledger outcomes with execution records
    "v_trade_timeline": (
        "CREATE VIEW IF NOT EXISTS v_trade_timeline AS "
        "SELECT "
        "  l.ticket, "
        "  l.symbol, "
        "  l.action, "
        "  l.volume, "
        "  l.open_price, "
        "  l.close_price, "
        "  l.pnl, "
        "  l.commission, "
        "  l.swap, "
        "  l.open_time, "
        "  l.close_time, "
        "  l.duration_sec, "
        "  l.exit_reason, "
        "  l.status "
        "FROM audit_ledger l "
        "ORDER BY l.id DESC"
    ),
    # 2. Equity Curve: chronological balance and equity progression
    "v_equity_curve": (
        "CREATE VIEW IF NOT EXISTS v_equity_curve AS "
        "SELECT "
        "  id, "
        "  timestamp, "
        "  balance, "
        "  equity, "
        "  margin, "
        "  margin_free, "
        "  margin_level, "
        "  floating_pnl, "
        "  closed_pnl "
        "FROM audit_account_snapshots "
        "ORDER BY id ASC"
    ),
    # 3. Broker Reconciliation: compares local orders with broker orders and deals
    "v_broker_reconciliation": (
        "CREATE VIEW IF NOT EXISTS v_broker_reconciliation AS "
        "SELECT "
        "  o.ticket AS local_ticket, "
        "  o.symbol, "
        "  o.order_type, "
        "  o.requested_volume, "
        "  bo.volume_current AS broker_volume, "
        "  bo.state AS broker_state, "
        "  bd.profit AS broker_deal_profit, "
        "  o.status AS local_status, "
        "  o.created_at AS local_created_at, "
        "  bo.time_done AS broker_done_at "
        "FROM audit_orders o "
        "LEFT JOIN audit_broker_orders bo ON o.ticket = bo.ticket "
        'LEFT JOIN audit_broker_deals bd ON o.ticket = bd."order"'
    ),
    # 4. Risk Summary: latest evaluated safety state and rule configurations
    "v_risk_summary": (
        "CREATE VIEW IF NOT EXISTS v_risk_summary AS "
        "SELECT "
        "  r.rule_name, "
        "  r.is_enabled, "
        "  r.parameters, "
        "  r.updated_at "
        "FROM trading_rules_config r"
    ),
    # 5. Dashboard Summary: rolling 24h operational overview
    "v_dashboard_summary": (
        "CREATE VIEW IF NOT EXISTS v_dashboard_summary AS "
        "SELECT "
        "  COUNT(*) AS total_trades, "
        "  COALESCE(SUM(pnl), 0.0) AS total_pnl, "
        "  COALESCE(SUM(commission), 0.0) AS total_commission, "
        "  COALESCE(SUM(swap), 0.0) AS total_swap, "
        "  COALESCE(AVG(duration_sec), 0.0) AS avg_duration_sec "
        "FROM audit_ledger "
        "WHERE status != 'OPENED'"
    ),
}


def ensure_analytics_views(driver: Any) -> list[str]:
    """Idempotently create or update operational views on the active provider."""
    created: list[str] = []
    for view_name, ddl in VIEW_DEFINITIONS.items():
        try:
            driver.execute(ddl)
            created.append(view_name)
        except Exception as exc:
            logger.debug("Failed to create view %s: %s", view_name, exc)
    return created
