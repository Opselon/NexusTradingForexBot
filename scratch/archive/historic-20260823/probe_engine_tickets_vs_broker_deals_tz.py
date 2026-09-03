"""Forensic probe: engine tickets vs broker deal history + timezone story."""

import sqlite3
import sys
from datetime import UTC, datetime, timedelta

import MetaTrader5 as mt5

ok = mt5.initialize()
if not ok:
    print("init failed", mt5.last_error())
    sys.exit(1)

now_utc = datetime.now(UTC)
ai = mt5.account_info()
ti = mt5.terminal_info()
print(f"account login={ai.login} balance={ai.balance} equity={ai.equity}")
print(f"terminal connected={ti.connected}")

# 1) All deals in last 48h grouped by entry
deals = mt5.history_deals_get(now_utc - timedelta(hours=48), now_utc) or []
print(f"\nTOTAL deals 48h: {len(deals)}")
print("deal ticket range:", min(d.ticket for d in deals), "..", max(d.ticket for d in deals))
print(
    "position_id range:", min(d.position_id for d in deals), "..", max(d.position_id for d in deals)
)

# 2) Current positions (including magic)
positions = mt5.positions_get() or []
print(f"\nCURRENT positions: {len(positions)}")
for p in positions:
    print(
        f"  ticket={p.ticket} magic={p.magic} symbol={p.symbol} type={p.type} "
        f"vol={p.volume} open={p.price_open} profit={p.profit} "
        f"open_time={datetime.fromtimestamp(p.time, tz=UTC)} comment={p.comment}"
    )

# 3) Pending orders
orders = mt5.orders_get() or []
print(f"\nCURRENT pending orders: {len(orders)}")
for o in orders[:10]:
    print(
        f"  ticket={o.ticket} magic={o.magic} vol={o.volume_current} price={o.price_open} "
        f"comment={o.comment} time_setup={datetime.fromtimestamp(o.time_setup, tz=UTC)}"
    )

# 4) history orders (the order-level view) last 48h
horders = mt5.history_orders_get(now_utc - timedelta(hours=48), now_utc) or []
print(f"\nHISTORY ORDERS 48h: {len(horders)}")
for o in sorted(horders, key=lambda x: x.time_done)[:40]:
    print(
        f"  order={o.ticket} type={o.type} vol={o.volume_initial} "
        f"price={o.price_open} state={o.state} reason={o.reason} "
        f"time_setup={datetime.fromtimestamp(o.time_setup, tz=UTC)} "
        f"time_done={datetime.fromtimestamp(o.time_done, tz=UTC)} comment={o.comment}"
    )

mt5.shutdown()

# 5) The 15 experience decision timestamps from the DB (engine clock)
con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
cur = con.cursor()
rows = cur.execute(
    """SELECT e.idempotency_key, e.execution_id, e.decision_timestamp, o.execution_id AS ticket
       FROM audit_experiences e LEFT JOIN audit_experience_outcomes o
       ON o.idempotency_key = e.idempotency_key
       WHERE o.execution_id != '' ORDER BY e.decision_timestamp"""
).fetchall()
print("\n=== engine experience decision timestamps (engine clock) ===")
for r in rows:
    print(f"  key={r[0][:20]} decision={r[2]} ticket={r[3]}")
con.close()
