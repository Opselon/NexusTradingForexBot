"""Portable operational analytics views with honest per-view results."""

from __future__ import annotations

from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.database.views")

_VIEW_TABLES = {
    "v_trade_timeline": "audit_ledger",
    "v_equity_curve": "audit_account_snapshots",
    "v_broker_reconciliation": "audit_orders",
    "v_risk_summary": "trading_rules_config",
    "v_dashboard_summary": "audit_ledger",
}


def _columns(driver: Any, table: str) -> set[str]:
    """Read a table's columns, returning an empty set when it is absent."""
    try:
        return {
            str(row.get("name") or row.get("column_name")) for row in driver.table_columns(table)
        }
    except Exception:
        return set()


def _expr(columns: set[str], name: str, alias: str, fallback: str = "NULL") -> str:
    source = f'"{name}"' if name in columns else fallback
    return f'{source} AS "{alias}"'


def _definitions(driver: Any) -> dict[str, str]:
    """Build DDL from the live column contract, without dialect-specific ordering."""
    ledger = _columns(driver, "audit_ledger")
    snapshots = _columns(driver, "audit_account_snapshots")
    orders = _columns(driver, "audit_orders")

    broker_deals = _columns(driver, "audit_broker_deals")
    rules = _columns(driver, "trading_rules_config")

    ledger_expr = ", ".join(
        _expr(ledger, c, c)
        for c in (
            "ticket",
            "symbol",
            "action",
            "volume",
            "open_price",
            "close_price",
            "pnl",
            "commission",
            "swap",
            "open_time",
            "close_time",
            "duration_sec",
            "exit_reason",
            "status",
        )
    )
    # Current PostgreSQL uses direction/entry_price; the legacy SQLite contract
    # uses action/open_price. Both are exposed through the stable view names.
    ledger_expr = ", ".join(
        [
            _expr(ledger, "ticket", "ticket"),
            _expr(ledger, "symbol", "symbol"),
            _expr(ledger, "action" if "action" in ledger else "direction", "action"),
            _expr(ledger, "volume", "volume"),
            _expr(ledger, "open_price" if "open_price" in ledger else "entry_price", "open_price"),
            _expr(
                ledger, "close_price" if "close_price" in ledger else "exit_price", "close_price"
            ),
            _expr(ledger, "pnl", "pnl"),
            _expr(ledger, "commission", "commission"),
            _expr(ledger, "swap", "swap"),
            _expr(ledger, "open_time", "open_time"),
            _expr(ledger, "close_time", "close_time"),
            _expr(ledger, "duration_sec", "duration_sec"),
            _expr(ledger, "exit_reason", "exit_reason"),
            _expr(ledger, "status", "status"),
        ]
    )
    snap_expr = ", ".join(
        [
            _expr(snapshots, "id", "id"),
            _expr(snapshots, "timestamp", "timestamp"),
            _expr(snapshots, "balance", "balance"),
            _expr(snapshots, "equity", "equity"),
            _expr(snapshots, "margin", "margin"),
            _expr(snapshots, "margin_free", "margin_free"),
            _expr(snapshots, "margin_level", "margin_level"),
            _expr(snapshots, "floating_pnl", "floating_pnl"),
            _expr(snapshots, "closed_pnl", "closed_pnl"),
        ]
    )
    order_type = "order_type" if "order_type" in orders else "execution_mode"
    requested_volume = "requested_volume" if "requested_volume" in orders else "volume"
    broker_join = 'bd."order"' if "order" in broker_deals else "bd.ticket"
    risk_expr = ", ".join(
        [
            _expr(rules, "rule_name", "rule_name"),
            _expr(rules, "is_enabled", "is_enabled"),
            _expr(rules, "parameters", "parameters"),
            _expr(rules, "updated_at", "updated_at"),
        ]
    )
    return {
        "v_trade_timeline": f"CREATE VIEW IF NOT EXISTS v_trade_timeline AS SELECT {ledger_expr} FROM audit_ledger",
        "v_equity_curve": f"CREATE VIEW IF NOT EXISTS v_equity_curve AS SELECT {snap_expr} FROM audit_account_snapshots",
        "v_broker_reconciliation": (
            "CREATE VIEW IF NOT EXISTS v_broker_reconciliation AS SELECT "
            f'o.ticket AS local_ticket, o.symbol, o."{order_type}" AS order_type, '
            f'o."{requested_volume}" AS requested_volume, bo.volume_current AS broker_volume, '
            "bo.state AS broker_state, bd.profit AS broker_deal_profit, "
            "o.ticket AS local_status, o.timestamp AS local_created_at, bo.time_done AS broker_done_at "
            "FROM audit_orders o LEFT JOIN audit_broker_orders bo ON o.ticket = bo.ticket "
            f"LEFT JOIN audit_broker_deals bd ON o.ticket = {broker_join}"
        ),
        "v_risk_summary": f"CREATE VIEW IF NOT EXISTS v_risk_summary AS SELECT {risk_expr} FROM trading_rules_config",
        "v_dashboard_summary": (
            "CREATE VIEW IF NOT EXISTS v_dashboard_summary AS SELECT COUNT(*) AS total_trades, "
            "COALESCE(SUM(pnl), 0.0) AS total_pnl, COALESCE(SUM(commission), 0.0) AS total_commission, "
            "COALESCE(SUM(swap), 0.0) AS total_swap, COALESCE(AVG(duration_sec), 0.0) AS avg_duration_sec "
            "FROM audit_ledger WHERE status != 'OPENED'"
        ),
    }


def ensure_analytics_views(driver: Any) -> dict[str, dict[str, str]]:
    """Create analytics views and return ``created``, ``exists`` or ``FAILED`` per view."""
    results: dict[str, dict[str, str]] = {}
    try:
        definitions = _definitions(driver)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        return {name: {"status": "FAILED", "error": reason} for name in _VIEW_TABLES}
    for name, ddl in definitions.items():
        try:
            if driver.table_exists(name):
                results[name] = {"status": "exists"}
            else:
                driver.execute(ddl)
                results[name] = {"status": "created"}
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("Analytics view %s failed: %s", name, reason)
            results[name] = {"status": "FAILED", "error": reason}
    return results


__all__ = ["ensure_analytics_views"]
