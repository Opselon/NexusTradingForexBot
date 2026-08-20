"""Broker history normalization, logical-trade reconstruction & sync worker.

THE DATA LINEAGE (task §2-§7, verified against REAL MetaQuotes captures):

    MetaTrader 5 terminal
      history_orders_get()  -> TradeOrder namedtuples (one per order event)
      history_deals_get()   -> TradeDeal namedtuples (one per deal event)
        |   (adapter already typed these into HistoryOrderSnapshot /
        |    DealSnapshot via providers.build_*_snapshot)
        v
    audit_broker_orders   - durable normalized copy of EVERY broker order
    audit_broker_deals    - durable normalized copy of EVERY broker deal
        |   (identity = broker ticket; insert-or-ignore -> exact idempotency)
        v
    audit_broker_trades   - ONE logical trade per position_id lifecycle
                            (entry + partial closes + final close merged;
                             net_pnl = gross - |commission| - |swap| - |fee|)

MT5 = broker truth. The tables are a durable NORMALIZED COPY + derived
analytics — never a replacement for the terminal as live source of truth.

IDEMPOTENCY
-----------
A deal ticket appears exactly once (UNIQUE(ticket)); an order ticket exactly
once (UNIQUE(ticket)); a logical trade exactly once per position_id
(UNIQUE(position_id)). Re-ingesting the identical history is a no-op.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.adapters.broker_history")


#: Costs are stored NEGATIVE by MT5 in deal records; net = profit - |costs|.
def _net_from_deal(profit: Any, commission: Any, swap: Any, fee: Any) -> float:
    return (
        float(profit or 0.0)
        - abs(float(commission or 0.0))
        - abs(float(swap or 0.0))
        - abs(float(fee or 0.0))
    )


def order_identity(order: dict[str, Any]) -> str:
    """Canonical broker identity for an order row: the broker order ticket."""
    ticket = order.get("ticket")
    if ticket is None:
        raise ValueError(f"order row without ticket: {order!r}")
    return str(ticket)


def deal_identity(deal: dict[str, Any]) -> str:
    """Canonical broker identity for a deal row: the broker deal ticket."""
    ticket = deal.get("ticket")
    if ticket is None:
        raise ValueError(f"deal row without ticket: {deal!r}")
    return str(ticket)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _s(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _utc_epoch_sec(value: Any) -> int:
    """MT5 epoch seconds (UTC). Accepts int/float epoch or datetime."""
    if isinstance(value, datetime):
        return int(value.timestamp())
    return _i(value)


def normalize_order_row(order: dict[str, Any]) -> dict[str, Any]:
    """One broker order -> normalized row (ALL real fields preserved)."""
    return {
        "ticket": _i(order.get("ticket")),
        "position_id": _i(order.get("position_id")),
        "symbol": _s(order.get("symbol")),
        "type": _i(order.get("type")),
        "magic": _i(order.get("magic")),
        "state": _i(order.get("state")),
        "volume_initial": _f(order.get("volume_initial")),
        "volume_current": _f(order.get("volume_current")),
        "price_open": _f(order.get("price_open")),
        "price_current": _f(order.get("price_current")),
        "price_stop_limit": _f(order.get("price_stop_limit", order.get("price_stoplimit"))),
        "sl": _f(order.get("sl")),
        "tp": _f(order.get("tp")),
        "time_setup": _utc_epoch_sec(order.get("time_setup", order.get("time_setup_msc"))),
        "time_done": _utc_epoch_sec(order.get("time_done", order.get("time_done_msc"))),
        "time_expiration": _utc_epoch_sec(order.get("time_expiration")),
        "reason": _i(order.get("reason")),
        "comment": _s(order.get("comment")),
        "external_id": _s(order.get("external_id")),
    }


def normalize_deal_row(deal: dict[str, Any]) -> dict[str, Any]:
    """One broker deal -> normalized row (ALL real fields preserved)."""
    profit = _f(deal.get("profit"))
    commission = _f(deal.get("commission"))
    swap = _f(deal.get("swap"))
    fee = _f(deal.get("fee"))
    return {
        "ticket": _i(deal.get("ticket")),
        "order": _i(deal.get("order")),
        "position_id": _i(deal.get("position_id")),
        "symbol": _s(deal.get("symbol")),
        "type": _i(deal.get("type")),
        "entry": _i(deal.get("entry")),
        "magic": _i(deal.get("magic")),
        "time": _utc_epoch_sec(deal.get("time", deal.get("time_msc"))),
        "reason": _i(deal.get("reason")),
        "volume": _f(deal.get("volume")),
        "price": _f(deal.get("price")),
        "profit": profit,
        "fee": fee,
        "swap": swap,
        "commission": commission,
        "net_result": _net_from_deal(profit, commission, swap, fee),
        "comment": _s(deal.get("comment")),
        "external_id": _s(deal.get("external_id")),
    }


class LogicalTrade:
    """One reconstructed position lifecycle from the broker deal stream."""

    __slots__ = (
        "commission",
        "deal_ids",
        "direction",
        "duration_sec",
        "entry_price",
        "entry_time",
        "exit_comment",
        "exit_price",
        "exit_reason",
        "exit_time",
        "fee",
        "gross_pnl",
        "magic",
        "master_order_id",
        "net_pnl",
        "order_ids",
        "position_id",
        "source",
        "swap",
        "symbol",
        "trade_id",
        "volume",
    )

    def __init__(
        self,
        *,
        trade_id: str,
        position_id: int,
        symbol: str,
    ) -> None:
        self.trade_id = trade_id
        self.position_id = position_id
        self.symbol = symbol
        self.direction = "UNKNOWN"
        self.entry_time: datetime | None = None
        self.exit_time: datetime | None = None
        self.entry_price: float = 0.0
        self.exit_price: float = 0.0
        self.volume: float = 0.0
        self.gross_pnl: float = 0.0
        self.commission: float = 0.0
        self.swap: float = 0.0
        self.fee: float = 0.0
        self.net_pnl: float = 0.0
        self.deal_ids: list[int] = []
        self.order_ids: list[int] = []
        self.master_order_id: int = 0
        self.magic: int = 0
        self.exit_reason: int = 0
        self.exit_comment: str = ""
        self.source: str = "BROKER_DEALS"
        self.duration_sec: float = 0.0


def reconstruct_trades(
    orders: list[dict[str, Any]] | None = None,
    deals: list[dict[str, Any]] | None = None,
    symbol: str | None = None,
) -> list[LogicalTrade]:
    """
    Reconstructs ONE logical trade per position lifecycle.

    Every broker deal for a position is aggregated (profit/commission/swap/fee
    summed, volumes summed, deal tickets collected) — partial closes merge into
    ONE outcome, never duplicated. Deal entry determines direction (type 0 =
    BUY position opened by a BUY-type deal, 1 = SELL).

    When an order stream is also supplied, the opening order's ticket is
    recorded as the master order id (orders and deals are NEVER conflated:
    orders have tickets of their own; a position lifecycle is keyed by
    position_id from the DEAL stream).
    """
    orders = orders or []
    deals = deals or []

    order_by_ticket: dict[int, dict[str, Any]] = {}
    for o in orders:
        ticket = _i(o.get("ticket"))
        if ticket:
            order_by_ticket[ticket] = o

    grouped: dict[int, list[dict[str, Any]]] = {}
    for d in deals:
        pid = _i(d.get("position_id"))
        if not pid:
            continue
        grouped.setdefault(pid, []).append(d)

    trades: list[LogicalTrade] = []
    for position_id, deal_rows in sorted(grouped.items()):
        trade = LogicalTrade(
            trade_id=str(position_id),
            position_id=position_id,
            symbol=_s(deal_rows[0].get("symbol")) or (symbol or ""),
        )
        open_deal: dict[str, Any] | None = None
        for d in deal_rows:
            trade.gross_pnl += _f(d.get("profit"))
            trade.commission += abs(_f(d.get("commission")))
            trade.swap += _f(d.get("swap"))
            trade.fee += _f(d.get("fee"))
            trade.volume += _f(d.get("volume"))
            trade.magic = _i(d.get("magic")) or trade.magic
            deal_ticket = _i(d.get("ticket"))
            if deal_ticket:
                trade.deal_ids.append(deal_ticket)
            order_ticket = _i(d.get("order"))
            if order_ticket:
                trade.order_ids.append(order_ticket)
            # Entry deal (DEAL_ENTRY_IN == 0) establishes direction + entry.
            if _i(d.get("entry")) == 0 and open_deal is None:
                open_deal = d
            # Out deal (DEAL_ENTRY_OUT == 1) drives exit price/timestamp.
            if _i(d.get("entry")) == 1:
                trade.exit_price = _f(d.get("price")) or trade.exit_price
                trade.exit_time = _epoch_utc(_i(d.get("time", d.get("time_msc"))))
                trade.exit_reason = _i(d.get("reason")) or trade.exit_reason
                trade.exit_comment = _s(d.get("comment")) or trade.exit_comment
        if open_deal is not None:
            trade.direction = "BUY" if _i(open_deal.get("type")) == 0 else "SELL"
            trade.entry_price = _f(open_deal.get("price"))
            trade.entry_time = _epoch_utc(_i(open_deal.get("time", open_deal.get("time_msc"))))
            trade.master_order_id = _i(open_deal.get("order"))
        # Volume of the leverage snapshot: sum of OPEN deals (entry leg), so
        # partial-close aggregation never inflates the traded size.
        open_volume = sum(_f(d.get("volume")) for d in deal_rows if _i(d.get("entry")) == 0)
        if open_volume > 0.0:
            trade.volume = open_volume
        trade.net_pnl = round(trade.gross_pnl - trade.commission - trade.swap - trade.fee, 8)
        if trade.entry_time is not None and trade.exit_time is not None:
            trade.duration_sec = max(0.0, (trade.exit_time - trade.entry_time).total_seconds())
        trades.append(trade)
    return trades


def _epoch_utc(epoch_sec: int) -> datetime | None:
    """Broker terminal epoch (server-local) -> real UTC.

    MT5 history deals/orders report `time` as seconds since the UNIX
    epoch in the SERVER timezone (this broker: GMT+3 / +180 min, see
    providers.BROKER_SERVER_UTC_OFFSET_MINUTES). Converting the epoch
    straight as UTC stored every broker timestamp 3h in the future:
    broker entry 07:21Z vs ledger open 04:21Z for the same execution
    (TIMEBASE_DIVERGENCE, INC-2026-7F6DE0C4, BUG-070 chain).
    Subtract the configured server offset before stamping as UTC so
    audit_broker_trades aligns with the canonical UTC ledger.
    """
    if not epoch_sec:
        return None
    try:
        from nexus_scalp.adapters.mt5.providers import (
            BROKER_SERVER_UTC_OFFSET_MINUTES,
        )

        return datetime.fromtimestamp(epoch_sec - BROKER_SERVER_UTC_OFFSET_MINUTES * 60, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Repository persistence
# ---------------------------------------------------------------------------

_BROKER_ORDERS_DDL = """
CREATE TABLE IF NOT EXISTS audit_broker_orders (
    ticket INTEGER PRIMARY KEY,
    position_id INTEGER DEFAULT 0,
    symbol TEXT DEFAULT '',
    type INTEGER DEFAULT 0,
    magic INTEGER DEFAULT 0,
    state INTEGER DEFAULT 0,
    volume_initial REAL DEFAULT 0.0,
    volume_current REAL DEFAULT 0.0,
    price_open REAL DEFAULT 0.0,
    price_current REAL DEFAULT 0.0,
    price_stop_limit REAL DEFAULT 0.0,
    sl REAL DEFAULT 0.0,
    tp REAL DEFAULT 0.0,
    time_setup INTEGER DEFAULT 0,
    time_done INTEGER DEFAULT 0,
    time_expiration INTEGER DEFAULT 0,
    reason INTEGER DEFAULT 0,
    comment TEXT DEFAULT '',
    external_id TEXT DEFAULT '',
    synced_at TEXT DEFAULT ''
);
"""

_BROKER_DEALS_DDL = """
CREATE TABLE IF NOT EXISTS audit_broker_deals (
    ticket INTEGER PRIMARY KEY,
    "order" INTEGER DEFAULT 0,
    position_id INTEGER DEFAULT 0,
    symbol TEXT DEFAULT '',
    type INTEGER DEFAULT 0,
    entry INTEGER DEFAULT 0,
    magic INTEGER DEFAULT 0,
    time INTEGER DEFAULT 0,
    reason INTEGER DEFAULT 0,
    volume REAL DEFAULT 0.0,
    price REAL DEFAULT 0.0,
    profit REAL DEFAULT 0.0,
    fee REAL DEFAULT 0.0,
    swap REAL DEFAULT 0.0,
    commission REAL DEFAULT 0.0,
    net_result REAL DEFAULT 0.0,
    comment TEXT DEFAULT '',
    external_id TEXT DEFAULT '',
    synced_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_broker_deals_position ON audit_broker_deals(position_id);
"""

_BROKER_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS audit_broker_trades (
    trade_id TEXT PRIMARY KEY,
    position_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT DEFAULT 'UNKNOWN',
    entry_time TEXT,
    exit_time TEXT,
    entry_price REAL DEFAULT 0.0,
    exit_price REAL DEFAULT 0.0,
    volume REAL DEFAULT 0.0,
    gross_pnl REAL DEFAULT 0.0,
    commission REAL DEFAULT 0.0,
    swap REAL DEFAULT 0.0,
    fee REAL DEFAULT 0.0,
    net_pnl REAL DEFAULT 0.0,
    deal_ids TEXT DEFAULT '[]',
    order_ids TEXT DEFAULT '[]',
    master_order_id INTEGER DEFAULT 0,
    magic INTEGER DEFAULT 0,
    exit_reason INTEGER DEFAULT 0,
    exit_comment TEXT DEFAULT '',
    duration_sec REAL DEFAULT 0.0,
    source TEXT DEFAULT 'BROKER_DEALS',
    synced_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_broker_trades_exit ON audit_broker_trades(exit_time);
"""

_HISTORY_META_DDL = """
CREATE TABLE IF NOT EXISTS audit_broker_history_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    symbol TEXT NOT NULL,
    last_sync_from TEXT,
    last_sync_to TEXT,
    last_synced_at TEXT,
    last_orders INTEGER DEFAULT 0,
    last_deals INTEGER DEFAULT 0,
    last_trades INTEGER DEFAULT 0
);
"""


def create_history_tables(conn: sqlite3.Connection) -> None:
    """Idempotent table creation for the broker-history normalized copy."""
    for ddl in (_BROKER_ORDERS_DDL, _BROKER_DEALS_DDL, _BROKER_TRADES_DDL, _HISTORY_META_DDL):
        for raw_stmt in ddl.strip().split(";\n"):
            trimmed = raw_stmt.strip()
            if trimmed:
                conn.execute(trimmed)


def sync_broker_history(
    conn: sqlite3.Connection,
    *,
    orders: list[dict[str, Any]],
    deals: list[dict[str, Any]],
    symbol: str,
    sync_from: datetime | None = None,
    sync_to: datetime | None = None,
) -> dict[str, Any]:
    """
    Inserts the normalized broker order/deal rows and reconstructed logical
    trades with EXACT broker-ticket idempotency. Returns sync telemetry.
    """
    started = time.perf_counter()
    create_history_tables(conn)

    orders_sorted = sorted(orders, key=lambda o: _i(o.get("ticket")))
    deals_sorted = sorted(deals, key=lambda d: _i(d.get("ticket")))

    orders_dup = 0
    deals_dup = 0
    trades_dup = 0
    for o in orders_sorted:
        row = normalize_order_row(o)
        cur = conn.execute(
            "INSERT OR IGNORE INTO audit_broker_orders (ticket, position_id, symbol, "
            "type, magic, state, volume_initial, volume_current, price_open, "
            "price_current, price_stop_limit, sl, tp, time_setup, time_done, "
            "time_expiration, reason, comment, external_id, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["ticket"],
                row["position_id"],
                row["symbol"],
                row["type"],
                row["magic"],
                row["state"],
                row["volume_initial"],
                row["volume_current"],
                row["price_open"],
                row["price_current"],
                row["price_stop_limit"],
                row["sl"],
                row["tp"],
                row["time_setup"],
                row["time_done"],
                row["time_expiration"],
                row["reason"],
                row["comment"],
                row["external_id"],
                datetime.now(UTC).isoformat(),
            ),
        )
        if cur.rowcount == 0:
            orders_dup += 1

    for d in deals_sorted:
        row = normalize_deal_row(d)
        cur = conn.execute(
            'INSERT OR IGNORE INTO audit_broker_deals (ticket, "order", position_id, '
            "symbol, type, entry, magic, time, reason, volume, price, profit, fee, "
            "swap, commission, net_result, comment, external_id, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["ticket"],
                row["order"],
                row["position_id"],
                row["symbol"],
                row["type"],
                row["entry"],
                row["magic"],
                row["time"],
                row["reason"],
                row["volume"],
                row["price"],
                row["profit"],
                row["fee"],
                row["swap"],
                row["commission"],
                row["net_result"],
                row["comment"],
                row["external_id"],
                datetime.now(UTC).isoformat(),
            ),
        )
        if cur.rowcount == 0:
            deals_dup += 1

    trades = reconstruct_trades(orders=orders, deals=deals, symbol=symbol)
    now_iso = datetime.now(UTC).isoformat()
    still_open = 0
    for t in trades:
        # A position with no OUT-deal inside the fetched window is still OPEN at
        # the broker; the deal stream has no realized result for it. Persisting
        # it as a closed trade with a zeroed outcome would be a silent fake —
        # skip it (it will surface once the broker closes it).
        if t.exit_time is None:
            still_open += 1
            logger.debug(
                "[ACCOUNT_HISTORY] open position skipped (no OUT deal in window)",
                position_id=t.position_id,
            )
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO audit_broker_trades (trade_id, position_id, symbol, "
            "direction, entry_time, exit_time, entry_price, exit_price, volume, "
            "gross_pnl, commission, swap, fee, net_pnl, deal_ids, order_ids, "
            "master_order_id, magic, exit_reason, exit_comment, duration_sec, "
            "source, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t.trade_id,
                t.position_id,
                t.symbol,
                t.direction,
                t.entry_time.isoformat() if t.entry_time else None,
                t.exit_time.isoformat() if t.exit_time else None,
                t.entry_price,
                t.exit_price,
                t.volume,
                t.gross_pnl,
                t.commission,
                t.swap,
                t.fee,
                t.net_pnl,
                ",".join(str(x) for x in t.deal_ids),
                ",".join(str(x) for x in sorted(set(t.order_ids))),
                t.master_order_id,
                t.magic,
                t.exit_reason,
                t.exit_comment,
                t.duration_sec,
                t.source,
                now_iso,
            ),
        )
        if cur.rowcount == 0:
            trades_dup += 1

    conn.execute(
        "INSERT INTO audit_broker_history_meta (id, symbol, last_sync_from, "
        "last_sync_to, last_synced_at, last_orders, last_deals, last_trades) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET symbol=excluded.symbol, "
        "last_sync_from=MIN(audit_broker_history_meta.last_sync_from, "
        "excluded.last_sync_from), last_sync_to=excluded.last_sync_to, "
        "last_synced_at=excluded.last_synced_at, last_orders=excluded.last_orders, "
        "last_deals=excluded.last_deals, last_trades=excluded.last_trades",
        (
            symbol,
            sync_from.isoformat() if sync_from else None,
            sync_to.isoformat() if sync_to else None,
            now_iso,
            len(orders),
            len(deals),
            len(trades),
        ),
    )
    conn.commit()

    taken_ms = round((time.perf_counter() - started) * 1000.0, 1)
    trades_attempted = max(0, len(trades) - still_open)
    result = {
        "orders_total": len(orders),
        "orders_inserted": len(orders) - orders_dup,
        "orders_duplicates": orders_dup,
        "deals_total": len(deals),
        "deals_inserted": len(deals) - deals_dup,
        "deals_duplicates": deals_dup,
        "trades_total": len(trades),
        "trades_open_skipped": still_open,
        "trades_inserted": max(0, trades_attempted - trades_dup),
        "trades_duplicates": trades_dup,
        "trades_persisted": trades_attempted,
        "duration_ms": taken_ms,
    }
    logger.info(
        "[ACCOUNT_HISTORY] event=SYNC_COMPLETE",
        orders=len(orders),
        deals=len(deals),
        trades=len(trades),
        inserted=(len(orders) - orders_dup) + (len(deals) - deals_dup),
        duplicates=orders_dup + deals_dup,
        duration_ms=taken_ms,
    )
    return result


def last_sync_window(conn: sqlite3.Connection, symbol: str) -> dict[str, Any] | None:
    """Reads the persisted sync watermark for incremental syncs."""
    row = conn.execute(
        "SELECT symbol, last_sync_from, last_sync_to, last_synced_at, "
        "last_orders, last_deals, last_trades FROM audit_broker_history_meta "
        "WHERE id = 1 AND symbol = ?",
        (symbol,),
    ).fetchone()
    if row is None:
        return None
    return (
        dict(row)
        if hasattr(row, "keys")
        else {
            "symbol": row[0],
            "last_sync_from": row[1],
            "last_sync_to": row[2],
            "last_synced_at": row[3],
            "last_orders": row[4],
            "last_deals": row[5],
            "last_trades": row[6],
        }
    )
